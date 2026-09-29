from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas

from clickup_integration.invoice_delivery import (
    finalize_clickup_issued_invoices,
    send_issued_invoice_customer_emails,
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
    bc = FakeEmailBC()
    with pytest.raises(ValueError, match="native Mexico delivery route"):
        send_issued_invoice_customer_emails(
            bc_client=bc, invoice_result={"market": "MX"},
            settings=replace(make_settings(), supported_market="MX"),
        )
    assert bc.sent_row_ids == []
    assert bc.audit_lookup_ids == []
