from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

import pytest
import requests
from reportlab.pdfgen import canvas

from clickup_integration.invoice_delivery import (
    finalize_clickup_issued_invoices,
    send_issued_invoice_customer_emails,
    should_send_invoice_customer_email,
    validate_invoice_pdf_layout,
)
from clickup_integration.invoice_sync import InvoiceAutomationSettings


def make_pdf_bytes(*lines: str) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    y = 780
    for line in lines:
        pdf.drawString(36, y, line)
        y -= 14
    pdf.save()
    return output.getvalue()


class FakeClickUp:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(default_workspace_id="8451352")
        self.uploads: list[dict[str, object]] = []
        self.file_field_updates: list[dict[str, object]] = []
        self.file_field_clears: list[dict[str, object]] = []
        self.comments: list[dict[str, object]] = []
        self.field_updates: list[dict[str, object]] = []

    def upload_custom_field_attachment(
        self,
        workspace_id,
        field_id,
        local_path,
        *,
        file_name=None,
        mime_type=None,
    ):
        upload = {
            "id": f"attachment-{len(self.uploads) + 1}",
            "workspace_id": workspace_id,
            "field_id": field_id,
            "file_name": file_name,
            "mime_type": mime_type,
        }
        self.uploads.append(upload)
        return upload

    def set_task_file_custom_field_attachments(self, task_id, field_id, attachment_ids):
        update = {"task_id": task_id, "field_id": field_id, "attachment_ids": attachment_ids}
        self.file_field_updates.append(update)
        return update

    def clear_task_custom_field_value(self, task_id, field_id):
        clear = {"task_id": task_id, "field_id": field_id}
        self.file_field_clears.append(clear)
        return clear

    def create_task_comment(self, task_id, *, comment_text, notify_all=False):
        comment = {"task_id": task_id, "comment_text": comment_text, "notify_all": notify_all}
        self.comments.append(comment)
        return comment

    def set_task_custom_field_value(self, task_id, field_id, value):
        update = {"task_id": task_id, "field_id": field_id, "value": value}
        self.field_updates.append(update)
        return update


class FakeBC:
    def get_sales_invoice_pdf_content(self, sales_invoice_id, *, company_id=None, market=None):
        return make_pdf_bytes(
            "FACTURA ELECTRONICA",
            "DOCUMENTO TRIBUTARIO ELECTRONICO",
            "INFORMACION DE EMBARQUE",
            "SERIE INTERNA",
            "NO. INTERNO",
        )

    def get_company_metadata(self, *, company_id=None, market=None):
        return {"name": "MTM_GT_PROD"}

    def build_sales_invoice_url(self, *, company_name, invoice_number):
        return f"https://bc.example/{company_name}/{invoice_number}"


class FakeEmailBC(FakeBC):
    def __init__(self, *, sender="consuelo@mtmlogix.com", native_verified=True, message_id="message-id"):
        self.sender = sender
        self.native_verified = native_verified
        self.message_id = message_id
        self.sent_row_ids = []
        self.audit_lookup_ids = []

    def get_posted_invoice_fel_description_by_number(self, invoice_number, *, market=None):
        return {"id": f"fel-{invoice_number}"}

    def send_posted_invoice_customer_email(self, fel_row_id, *, market=None):
        self.sent_row_ids.append(fel_row_id)
        return {}

    def get_invoice_email_delivery_by_posted_invoice_id(self, invoice_id, *, market=None):
        self.audit_lookup_ids.append(invoice_id)
        return {
            "status": "Sent",
            "senderEmail": self.sender,
            "bcEmailMessageId": self.message_id,
            "nativeSentVerified": self.native_verified,
        }


def make_settings() -> InvoiceAutomationSettings:
    return InvoiceAutomationSettings(
        ready_status="Listo para facturar",
        ok_finops_status="OK Finops",
        eta_horizon_days=10,
        supported_market="GT",
        supported_currency="USD",
        invoice_status_field_names=("Estatus de facturación (USD)/",),
        eta_field_names=("ETA",),
        currency_field_names=("Invoice Currency",),
        reference_field_names=("Reference",),
        invoice_date_field_names=("Invoice Date",),
        posting_date_field_names=("Posting Date",),
        due_date_field_names=("Due Date",),
        freight_field_names=("Freight",),
        inland_field_names=("Inland",),
        destination_field_names=("Destination Charges",),
        bc_customer_id_field_names=("Business Central Customer ID",),
        bc_customer_number_field_names=("Business Central Customer Number",),
        bc_invoice_number_field_names=("Business Central Invoice Number",),
        bc_invoice_id_field_names=("Business Central Invoice ID",),
        freight_account_number=None,
        inland_account_number=None,
        destination_account_number=None,
    )


def clickup_summary() -> dict:
    return {
        "task_id": "task-1",
        "custom_fields": {
            "Invoice to Client": {
                "id": "5d67859a-1ae0-4cda-9f57-2a89bf1ff259",
                "value": None,
            },
            "Estatus de facturación (USD)/": {
                "id": "invoice-status",
                "value": 1,
                "type_config": {
                    "options": [
                        {"id": "ready", "name": "Listo para facturar", "orderindex": 1},
                        {"id": "invoiced", "name": "Facturada", "orderindex": 2},
                    ]
                },
            },
        },
    }


def finalized_invoice_result(*, stamp_status: str = "Stamp Received") -> dict:
    return {
        "status": "applied",
        "market": "GT",
        "created_invoices": [
            {
                "id": "draft-int-id",
                "number": "GTFV00000059",
                "externalDocumentNumber": "MTMLXGT-24096-INT",
                "invoice_group": "INT",
            }
        ],
        "finalized_invoices": [
            {
                "invoice_group": "INT",
                "externalDocumentNumber": "MTMLXGT-24096-INT",
                "posted_invoice_after_stamp": {
                    "id": "bc-int-id",
                    "number": "GTFVR0003923",
                    "externalDocumentNumber": "MTMLXGT-24096-INT",
                },
                "custom_api_row_after_stamp": {
                    "electronicDocumentStatus": stamp_status,
                },
            },
            {
                "invoice_group": "NAT",
                "externalDocumentNumber": "MTMLXGT-24096-NAT",
                "posted_invoice_after_stamp": {
                    "id": "bc-nat-id",
                    "number": "GTFVR0003924",
                    "externalDocumentNumber": "MTMLXGT-24096-NAT",
                },
                "custom_api_row_after_stamp": {
                    "electronicDocumentStatus": stamp_status,
                },
            },
        ],
    }


def test_finalize_clickup_issued_invoices_accepts_finalized_stamped_invoice_result() -> None:
    clickup = FakeClickUp()

    result = finalize_clickup_issued_invoices(
        clickup=clickup,
        bc_client=FakeBC(),
        clickup_summary=clickup_summary(),
        invoice_result=finalized_invoice_result(),
        settings=make_settings(),
        workspace_id="8451352",
        mark_status=True,
    )

    assert [upload["file_name"] for upload in clickup.uploads] == [
        "MTMLXGT-24096-INT.pdf",
        "MTMLXGT-24096-NAT.pdf",
    ]
    assert clickup.file_field_updates == [
        {
            "task_id": "task-1",
            "field_id": "5d67859a-1ae0-4cda-9f57-2a89bf1ff259",
            "attachment_ids": ["attachment-1", "attachment-2"],
        }
    ]
    assert clickup.file_field_clears == [
        {"task_id": "task-1", "field_id": "5d67859a-1ae0-4cda-9f57-2a89bf1ff259"}
    ]
    assert "GTFVR0003923" in result["comment_text"]
    assert "GTFVR0003924" in result["comment_text"]
    assert "GTFV00000059" not in result["comment_text"]
    assert result["final_status_update"] == {
        "task_id": "task-1",
        "field_id": "invoice-status",
        "value": "invoiced",
    }


def test_finalize_clickup_issued_invoices_marks_status_by_field_id_when_name_differs() -> None:
    clickup = FakeClickUp()
    summary = clickup_summary()
    status_field = summary["custom_fields"].pop("Estatus de facturación (USD)/")
    summary["custom_fields"]["Shared invoice status"] = status_field
    settings = replace(
        make_settings(),
        invoice_status_field_names=("Wrong field name",),
        invoice_status_field_ids=("invoice-status",),
    )

    result = finalize_clickup_issued_invoices(
        clickup=clickup,
        bc_client=FakeBC(),
        clickup_summary=summary,
        invoice_result=finalized_invoice_result(),
        settings=settings,
        workspace_id="8451352",
        mark_status=True,
    )

    assert result["final_status_update"] == {
        "task_id": "task-1",
        "field_id": "invoice-status",
        "value": "invoiced",
    }


def test_finalize_clickup_issued_invoices_blocks_unstamped_finalized_result() -> None:
    with pytest.raises(ValueError, match="Stamp Received"):
        finalize_clickup_issued_invoices(
            clickup=FakeClickUp(),
            bc_client=FakeBC(),
            clickup_summary=clickup_summary(),
            invoice_result=finalized_invoice_result(stamp_status="Stamp Pending"),
            settings=make_settings(),
            workspace_id="8451352",
            mark_status=True,
        )


def test_validate_invoice_pdf_layout_blocks_legacy_or_wrong_form_pdf() -> None:
    legacy_pdf = make_pdf_bytes(
        "FACTRURA",
        "SALDO PENDIENTE",
        "report.feel.com.gt",
    )

    with pytest.raises(ValueError, match="approved MTM GT invoice form"):
        validate_invoice_pdf_layout(
            legacy_pdf,
            invoice_number="GTFVR0003945",
            invoice_group="INT",
        )


def test_validate_invoice_pdf_layout_accepts_mx_cfdi_markers() -> None:
    mx_pdf = make_pdf_bytes(
        "Factura",
        "Version CFDI: 4.0",
        "Folio Fiscal",
        "Sello CFDI",
        "Sello SAT",
        "Este documento es una representacion impresa de un CFDI",
    )

    result = validate_invoice_pdf_layout(
        mx_pdf,
        invoice_number="B0003342",
        invoice_group="INT",
        market="MX",
    )

    assert result["status"] == "passed"
    assert result["market"] == "MX"


def test_send_customer_email_requires_native_bc_sent_evidence() -> None:
    bc = FakeEmailBC()

    result = send_issued_invoice_customer_emails(
        bc_client=bc,
        invoice_result=finalized_invoice_result(),
        settings=make_settings(),
    )

    assert result["status"] == "sent"
    assert result["sender"] == "consuelo@mtmlogix.com"
    assert bc.sent_row_ids == ["fel-GTFVR0003923", "fel-GTFVR0003924"]
    assert bc.audit_lookup_ids == ["fel-GTFVR0003923", "fel-GTFVR0003924"]


@pytest.mark.parametrize(
    ("bc", "error"),
    [
        (FakeEmailBC(sender="mario@mtmlogix.com"), "unexpected sender"),
        (FakeEmailBC(message_id=""), "native email message evidence"),
        (FakeEmailBC(native_verified=False), "native Sent Email record"),
    ],
)
def test_send_customer_email_blocks_incomplete_native_bc_evidence(bc, error) -> None:
    with pytest.raises(ValueError, match=error):
        send_issued_invoice_customer_emails(
            bc_client=bc,
            invoice_result=finalized_invoice_result(),
            settings=make_settings(),
        )


def test_mexico_email_never_uses_guatemala_report_action():
    bc = FakeMXEmailBC(readiness="")
    with pytest.raises(ValueError, match="native Mexico delivery route"):
        send_issued_invoice_customer_emails(
            bc_client=bc, invoice_result=mx_email_result(),
            settings=replace(make_settings(), supported_market="MX"),
        )
    assert bc.sent_row_ids == []
    assert bc.audit_lookup_ids == []


MX_FISCAL_UUID = "bb14b70f-21d0-423b-8c6d-f2d46bdad658"
MX_READINESS = f"Market=MX|Capability=MX_PAC_PDF_CFDI_XML_V1|AttachmentsValidated=true|FiscalUuid={MX_FISCAL_UUID}|Sender=carlos@mtmlogix.com"


def mx_email_result():
    return {
        "status": "applied", "market": "MX",
        "created_invoices": [{
            "id": "mx-standard-id", "number": "B0003383", "customerNumber": "C00068",
            "externalDocumentNumber": "UW-26-ES-002", "currencyCode": "USD",
            "totalAmountIncludingTax": 15307.20, "dueDate": "2026-11-03",
        }],
    }


class FakeMXEmailBC(FakeEmailBC):
    def __init__(self, *, readiness=MX_READINESS, prepared_cc=None, sender="carlos@mtmlogix.com", timed_out=False):
        super().__init__(sender=sender)
        self.readiness = readiness
        self.preparations = []
        self.timed_out = timed_out
        self.audit = {}
        if prepared_cc is not None:
            self.audit = self._prepared_audit(prepared_cc)

    def get_sales_invoice_pdf_content(self, *args, **kwargs):
        raise AssertionError("MX email must validate the PAC attachment route instead of GT/standard report")

    def get_posted_invoice_fel_description_by_number(self, invoice_number, *, market=None):
        assert market == "MX"
        return {
            "id": "mx-posted-header-id", "number": invoice_number, "cancelled": False,
            "electronicDocumentStatus": "Stamp Received", "fiscalInvoiceNumberPac": MX_FISCAL_UUID,
            "invoiceEmailReadiness": self.readiness,
        }

    def get_posted_sales_invoice_by_number(self, number, *, market=None):
        assert market == "MX"
        return {**mx_email_result()["created_invoices"][0]}

    def _prepared_audit(self, cc):
        return {
            "deliveryPrepared": True, "ccRecipients": cc, "fiscalUuid": MX_FISCAL_UUID,
            "recipient": "customer@example.com", "pdfAttachmentSha256": "a" * 64,
            "xmlAttachmentSha256": "b" * 64, "expectedExternalDocumentNumber": "UW-26-ES-002",
            "expectedAmountIncludingVat": 15307.20, "expectedDueDate": "2026-11-03",
        }

    def prepare_invoice_email_delivery(self, row_id, **kwargs):
        self.preparations.append({"row_id": row_id, **kwargs})
        self.audit = self._prepared_audit(kwargs["cc_recipients"])

    def send_posted_invoice_customer_email(self, fel_row_id, *, market=None):
        assert market == "MX"
        self.sent_row_ids.append(fel_row_id)
        self.audit.update(status="Sent", senderEmail=self.sender, bcEmailMessageId=self.message_id,
                          senderAccountId="carlos-account-id", nativeSentVerified=self.native_verified)
        if self.timed_out:
            raise requests.Timeout("HTTP timed out after BC accepted")
        return {}

    def get_invoice_email_delivery_by_posted_invoice_id(self, invoice_id, *, market=None):
        self.audit_lookup_ids.append(invoice_id)
        return dict(self.audit)


def test_mexico_customer_email_binds_identity_and_fiscal_attachments():
    bc = FakeMXEmailBC(prepared_cc="mario@mtmlogix.com")
    result = send_issued_invoice_customer_emails(
        bc_client=bc, invoice_result=mx_email_result(), settings=make_settings(),
        cc_recipients_by_invoice={"B0003383": "mario@mtmlogix.com"},
    )
    assert result["sender"] == "carlos@mtmlogix.com"
    assert bc.sent_row_ids == ["mx-posted-header-id"]
    assert bc.preparations == []


def test_mexico_email_preserves_prepared_invoice_cc_without_global_cc():
    existing = FakeMXEmailBC(prepared_cc="mario@mtmlogix.com")
    new = FakeMXEmailBC(prepared_cc="")
    for bc in (existing, new):
        send_issued_invoice_customer_emails(bc_client=bc, invoice_result=mx_email_result(), settings=make_settings())
    assert existing.preparations == []
    assert existing.audit["ccRecipients"] == "mario@mtmlogix.com"
    assert new.audit["ccRecipients"] == ""
    assert new.preparations == []


def test_mexico_generic_customer_delivery_never_prepares_unreviewed_pdf():
    bc = FakeMXEmailBC()
    with pytest.raises(ValueError, match="independently review the PAC PDF"):
        send_issued_invoice_customer_emails(
            bc_client=bc, invoice_result=mx_email_result(), settings=make_settings(),
            cc_recipients_by_invoice={"B0003383": "mario@mtmlogix.com"},
        )
    assert bc.preparations == []
    assert bc.sent_row_ids == []


@pytest.mark.parametrize("field,value,error", [
    ("expectedDueDate", "2026-10-23", "current invoice"),
    ("expectedAmountIncludingVat", 1, "current invoice"),
    ("xmlAttachmentSha256", "", "attachment evidence"),
    ("fiscalUuid", "wrong-uuid", "fiscal email identity"),
])
def test_mexico_email_holds_changed_prepared_identity_before_send(field, value, error):
    bc = FakeMXEmailBC(prepared_cc="mario@mtmlogix.com")
    bc.audit[field] = value
    with pytest.raises(ValueError, match=error):
        send_issued_invoice_customer_emails(bc_client=bc, invoice_result=mx_email_result(), settings=make_settings())
    assert bc.sent_row_ids == []
    assert bc.preparations == []


def test_mexico_email_rejects_changed_explicit_cc_before_send():
    bc = FakeMXEmailBC(prepared_cc="mario@mtmlogix.com")
    with pytest.raises(ValueError, match="CC intent differs"):
        send_issued_invoice_customer_emails(
            bc_client=bc, invoice_result=mx_email_result(), settings=make_settings(),
            cc_recipients_by_invoice={"B0003383": "another@example.com"},
        )
    assert bc.sent_row_ids == []


def test_mexico_email_timeout_reconciles_native_audit_without_resending():
    bc = FakeMXEmailBC(timed_out=True, prepared_cc="")
    result = send_issued_invoice_customer_emails(bc_client=bc, invoice_result=mx_email_result(), settings=make_settings())
    assert result["status"] == "sent"
    assert len(bc.sent_row_ids) == 1


def test_mexico_tagomago_warehouse_boundary_still_blocks_customer_delivery():
    bc = FakeMXEmailBC()
    invoice_result = mx_email_result()
    invoice_result["created_invoices"][0]["customerNumber"] = "C00067"
    with pytest.raises(ValueError, match="dedicated approval-ledger"):
        send_issued_invoice_customer_emails(bc_client=bc, invoice_result=invoice_result, settings=make_settings())
    assert bc.sent_row_ids == []
    assert bc.preparations == []


def test_mexico_tagomago_approved_usd_ocean_can_use_native_email(monkeypatch):
    bc = FakeMXEmailBC(prepared_cc="")
    result = mx_email_result()
    result.update(source_list_id="901703461634", currency="USD", customer_number="C00067",
                  shipment_metadata={"product": "OCEAN/FCL"})
    result["created_invoices"][0]["customerNumber"] = "C00067"
    monkeypatch.setattr(bc, "get_posted_sales_invoice_by_number", lambda *args, **kwargs: dict(result["created_invoices"][0]))
    delivery = send_issued_invoice_customer_emails(bc_client=bc, invoice_result=result, settings=make_settings())
    assert delivery["status"] == "sent"
    assert bc.sent_row_ids == ["mx-posted-header-id"]


def test_mexico_email_timeout_with_incomplete_audit_is_held(monkeypatch):
    bc = FakeMXEmailBC(prepared_cc="")

    def ambiguous_send(row_id, **kwargs):
        bc.sent_row_ids.append(row_id)
        bc.audit["status"] = "Pending"
        raise requests.Timeout("The server may have sent")

    monkeypatch.setattr(bc, "send_posted_invoice_customer_email", ambiguous_send)
    with pytest.raises(ValueError, match="no automatic resend"):
        send_issued_invoice_customer_emails(bc_client=bc, invoice_result=mx_email_result(), settings=make_settings())
    assert len(bc.sent_row_ids) == 1


def test_mexico_email_gate_is_independent_and_false_by_default(monkeypatch):
    monkeypatch.setenv("CLICKUP_INVOICE_SEND_ENABLED", "true")
    monkeypatch.delenv("CLICKUP_MX_INVOICE_SEND_ENABLED", raising=False)
    assert should_send_invoice_customer_email()
    assert not should_send_invoice_customer_email("MX")
    monkeypatch.setenv("CLICKUP_MX_INVOICE_SEND_ENABLED", "true")
    assert should_send_invoice_customer_email("MX")
    monkeypatch.setenv("CLICKUP_INVOICE_SEND_ENABLED", "false")
    assert not should_send_invoice_customer_email("GT")
    assert should_send_invoice_customer_email("MX")
