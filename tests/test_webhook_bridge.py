from io import BytesIO

from reportlab.pdfgen import canvas

from clickup_integration.config import ClickUpSettings
from clickup_integration.invoice_sync import InvoiceAutomationSettings, InvoiceChargeMapping
from webhook_bridge.main import (
    _extract_invoice_numbers,
    _fetch_clickup_task_for_webhook,
    _infer_clickup_team_id,
    app,
    extract_task_id,
    extract_task_id_from_path,
)


def _make_invoice_pdf_bytes() -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    y = 780
    for line in (
        "FACTURA ELECTRONICA",
        "DOCUMENTO TRIBUTARIO ELECTRONICO",
        "INFORMACION DE EMBARQUE",
        "SERIE INTERNA",
        "NO. INTERNO",
    ):
        pdf.drawString(36, y, line)
        y -= 14
    pdf.save()
    return output.getvalue()


def test_customer_webhook_accepts_task_id_path_before_auth(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    response = TestClient(app).post(
        "/clickup/webhooks/customer-sync/2w8majz/",
        headers={"Authorization": "Bearer wrong-token"},
        json={},
    )

    assert response.status_code == 401


def test_extract_invoice_numbers_accepts_mx_posted_prefix(monkeypatch) -> None:
    monkeypatch.setenv("CLICKUP_INVOICE_MARKET", "MX")

    result = _extract_invoice_numbers({"invoice_numbers": "B0003342, B0003342"})

    assert result == ["B0003342"]


def test_customer_webhook_accepts_clickup_appended_task_id_before_auth(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    response = TestClient(app).post(
        "/clickup/webhooks/customer-sync2w8majz/",
        headers={"Authorization": "Bearer wrong-token"},
        json={},
    )

    assert response.status_code == 401


def test_invoice_webhook_accepts_task_id_path_before_auth(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    response = TestClient(app).post(
        "/clickup/webhooks/invoice-sync/2w8majz/",
        headers={"Authorization": "Bearer wrong-token"},
        json={},
    )

    assert response.status_code == 401


def test_invoice_webhook_accepts_clickup_appended_task_id_before_auth(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    response = TestClient(app).post(
        "/clickup/webhooks/invoice-sync2w8majz/",
        headers={"Authorization": "Bearer wrong-token"},
        json={},
    )

    assert response.status_code == 401


def test_invoice_readiness_reports_mapping_count(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CLICKUP_INVOICE_WEBHOOK_APPLY", "false")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("CLICKUP_ACCESS_TOKEN", "pk_test")
    monkeypatch.setattr("webhook_bridge.main.InvoiceAutomationSettings.from_env", _invoice_settings)

    response = TestClient(app).get("/clickup/webhooks/invoice-sync/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["missing_runtime_config"] == []
    assert payload["apply_mode"] is False
    assert payload["charge_mapping_count"] == 2
    assert payload["line_type"] == "Item"


def test_extract_task_id_prefers_explicit_task_id_keys() -> None:
    assert extract_task_id({"Task ID": "MTM-2035664"}) == "MTM-2035664"
    assert extract_task_id({"task_id": "12345"}) == "12345"
    assert extract_task_id({"taskId": "abc"}) == "abc"


def test_extract_task_id_supports_nested_task_object() -> None:
    assert extract_task_id({"task": {"id": "nested-1"}}) == "nested-1"


def test_extract_task_id_returns_none_when_missing() -> None:
    assert extract_task_id({"foo": "bar"}) is None


def test_extract_task_id_returns_none_for_empty_payload() -> None:
    assert extract_task_id({}) is None


def test_extract_task_id_from_path_supports_clickup_dynamic_segments() -> None:
    path = "/clickup/webhooks/customer-sync86e0ty7pg/OPERA%2520LOGISTICA/1775697146706/"
    assert (
        extract_task_id_from_path(path, base_path="/clickup/webhooks/customer-sync")
        == "86e0ty7pg"
    )


def test_extract_task_id_from_invoice_path_supports_clickup_dynamic_segments() -> None:
    path = "/clickup/webhooks/invoice-sync86e0ty7pg/OPERA%2520LOGISTICA/1775697146706/"
    assert (
        extract_task_id_from_path(path, base_path="/clickup/webhooks/invoice-sync")
        == "86e0ty7pg"
    )


def test_extract_task_id_from_path_returns_none_for_other_routes() -> None:
    assert (
        extract_task_id_from_path(
            "/clickup/webhooks/not-customer-sync/123",
            base_path="/clickup/webhooks/customer-sync",
        )
        is None
    )


class _FakeClickUpClient:
    def __init__(self, *, default_workspace_id: str | None = None) -> None:
        self.settings = ClickUpSettings(
            client_id=None,
            client_secret=None,
            redirect_uri=None,
            access_token="pk_test",
            token_type="Bearer",
            default_workspace_id=default_workspace_id,
            default_customer_list_id=None,
        )
        self.calls: list[tuple[str, bool, str | None]] = []
        self.comments: list[dict[str, object]] = []
        self.custom_field_uploads: list[dict[str, object]] = []
        self.file_field_updates: list[dict[str, object]] = []

    def get_authorized_workspaces(self) -> dict[str, object]:
        return {"teams": [{"id": "8451352"}]}

    def get_task(
        self,
        task_id: str,
        *,
        custom_task_ids: bool = False,
        team_id: str | None = None,
        include_subtasks: bool = False,
    ) -> dict[str, object]:
        self.calls.append((task_id, custom_task_ids, team_id))
        if custom_task_ids and team_id == "8451352":
            return {"id": "1", "name": "ok"}
        raise RuntimeError("lookup failed")


class _FakeClickUpClientInternalIdFallback(_FakeClickUpClient):
    def get_task(
        self,
        task_id: str,
        *,
        custom_task_ids: bool = False,
        team_id: str | None = None,
        include_subtasks: bool = False,
    ) -> dict[str, object]:
        self.calls.append((task_id, custom_task_ids, team_id))
        if not custom_task_ids and team_id is None:
            return {"id": task_id, "name": "internal id ok"}
        raise RuntimeError("lookup failed")


def test_infer_clickup_team_id_uses_single_authorized_workspace() -> None:
    client = _FakeClickUpClient()
    assert _infer_clickup_team_id(client) == "8451352"


def test_fetch_clickup_task_for_webhook_retries_with_inferred_team_id() -> None:
    client = _FakeClickUpClient()
    task = _fetch_clickup_task_for_webhook(
        clickup=client,
        task_id="MTM-1",
        custom_task_ids=True,
        team_id=None,
    )
    assert task == {"id": "1", "name": "ok"}
    assert client.calls == [
        ("MTM-1", True, None),
        ("MTM-1", True, "8451352"),
    ]


def test_fetch_clickup_task_for_webhook_retries_internal_id_after_custom_lookup() -> None:
    client = _FakeClickUpClientInternalIdFallback(default_workspace_id="8451352")
    task = _fetch_clickup_task_for_webhook(
        clickup=client,
        task_id="86e0nwb2p",
        custom_task_ids=True,
        team_id="8451352",
    )
    assert task == {"id": "86e0nwb2p", "name": "internal id ok"}
    assert client.calls == [
        ("86e0nwb2p", True, "8451352"),
        ("86e0nwb2p", False, None),
    ]


class _FakeInvoiceClickUpClient:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.updated_tasks: list[dict[str, object]] = []
        self.field_updates: list[dict[str, object]] = []
        self.comments: list[dict[str, object]] = []
        self.custom_field_uploads: list[dict[str, object]] = []
        self.file_field_updates: list[dict[str, object]] = []
        self.file_field_clears: list[dict[str, object]] = []

    def get_authorized_workspaces(self) -> dict[str, object]:
        return {"teams": [{"id": "8451352"}]}

    def get_task(
        self,
        task_id: str,
        *,
        custom_task_ids: bool = False,
        team_id: str | None = None,
        include_subtasks: bool = False,
    ) -> dict[str, object]:
        assert task_id == "task-1"
        assert custom_task_ids is True
        assert team_id == "8451352"
        return _invoice_task(status="OK Finops")

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        name: str | None = None,
        description: str | None = None,
        custom_task_ids: bool = False,
        team_id: str | None = None,
    ) -> dict[str, object]:
        update = {"task_id": task_id, "status": status, "custom_task_ids": custom_task_ids, "team_id": team_id}
        self.updated_tasks.append(update)
        return update

    def set_task_custom_field_value(self, task_id: str, field_id: str, value: object) -> dict[str, object]:
        update = {"task_id": task_id, "field_id": field_id, "value": value}
        self.field_updates.append(update)
        return update

    def upload_custom_field_attachment(
        self,
        workspace_id: str,
        field_id: str,
        local_path,
        *,
        file_name: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, object]:
        upload = {
            "workspace_id": workspace_id,
            "field_id": field_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "id": f"attachment-{len(self.custom_field_uploads) + 1}",
        }
        self.custom_field_uploads.append(upload)
        return upload

    def set_task_file_custom_field_attachments(
        self,
        task_id: str,
        field_id: str,
        attachment_ids: list[str],
    ) -> dict[str, object]:
        update = {"task_id": task_id, "field_id": field_id, "attachment_ids": attachment_ids}
        self.file_field_updates.append(update)
        return update

    def clear_task_custom_field_value(self, task_id: str, field_id: str) -> dict[str, object]:
        clear = {"task_id": task_id, "field_id": field_id}
        self.file_field_clears.append(clear)
        return clear

    def create_task_comment(self, task_id: str, *, comment_text: str, notify_all: bool = False) -> dict[str, object]:
        comment = {"task_id": task_id, "comment_text": comment_text, "notify_all": notify_all}
        self.comments.append(comment)
        return comment


class _FakeInvoiceBCClient:
    def __init__(self, settings) -> None:
        self.created_headers: list[dict[str, object]] = []
        self.created_lines: list[dict[str, object]] = []
        self.draft_invoices: dict[str, dict[str, object]] = {}
        self.posted_invoices: dict[str, dict[str, object]] = {}
        self.fel_rows: dict[str, dict[str, object]] = {}
        self.stamp_status = "Stamp Received"

    def find_entities(self, entity_name: str, *, filters: str, top: int = 1, company_id=None, market=None):
        if entity_name == "customers":
            return [
                {
                    "id": "customer-id",
                    "number": "C00067",
                    "displayName": "Customer",
                    "currencyCode": "USD",
                    "paymentTermsId": "term-30-days",
                    "country": "GT",
                    "countryCode": None,
                }
            ]
        assert entity_name == "salesInvoices"
        return []

    def get_customer_by_id(self, customer_id: str, *, company_id=None, market=None):
        return {
            "id": customer_id,
            "number": "C00067",
            "displayName": "Customer",
            "currencyCode": "USD",
            "paymentTermsId": "term-30-days",
            "country": "GT",
            "countryCode": None,
        }

    def get_customer_invoicing_by_number(self, customer_number: str, *, company_id=None, market=None):
        return {
            "id": "customer-id",
            "number": customer_number,
            "felPais": "",
            "countryRegionCode": "GT",
            "resolvedFelCountryCode": "GT",
            "felCountryReady": True,
        }

    def resolve_item_by_number(self, item_number: str, *, market: str | None = None):
        assert market == "GT"
        return {"id": f"item-{item_number}", "number": item_number}

    def create_sales_invoice(self, payload: dict, *, company_id=None, market=None):
        self.created_headers.append(payload)
        index = len(self.created_headers)
        invoice = {"id": f"bc-invoice-id-{index}", "number": f"GTFVTEST{index}", **payload}
        self.draft_invoices[str(invoice["id"])] = invoice
        return invoice

    def create_sales_invoice_line(self, sales_invoice_id: str, payload: dict, *, company_id=None, market=None):
        self.created_lines.append(payload)
        return {"id": f"line-{len(self.created_lines)}", **payload}

    def post_sales_invoice(self, sales_invoice_id: str, *, company_id=None, market=None):
        draft = self.draft_invoices[sales_invoice_id]
        index = len(self.posted_invoices) + 1
        posted = {
            **draft,
            "id": sales_invoice_id,
            "number": f"GTFVRTEST{index}",
        }
        self.posted_invoices[sales_invoice_id] = posted
        self.fel_rows[str(posted["number"])] = {
            "id": f"fel-row-{index}",
            "number": posted["number"],
            "electronicDocumentStatus": "Pending",
            "errorDescription": "",
        }
        return {"status": "posted", "id": sales_invoice_id, "number": posted["number"]}

    def get_entity(self, entity_name: str, entity_id: str, *, company_id=None, market=None):
        assert entity_name == "salesInvoices"
        return self.posted_invoices.get(entity_id) or self.draft_invoices.get(entity_id)

    def get_posted_sales_invoice_by_external_document_number(
        self,
        external_document_number: str,
        *,
        company_id=None,
        market=None,
    ):
        for invoice in self.posted_invoices.values():
            if invoice.get("externalDocumentNumber") == external_document_number:
                return invoice
        return None

    def get_posted_invoice_fel_description_by_number(self, invoice_number: str, *, company_id=None, market=None):
        return self.fel_rows.get(invoice_number)

    def sync_posted_invoice_fel_line_descriptions(self, posted_invoice_fel_row_id: str, *, company_id=None, market=None):
        return {"status": "synced", "id": posted_invoice_fel_row_id}

    def stamp_posted_invoice_fel(self, posted_invoice_fel_row_id: str, *, company_id=None, market=None):
        for row in self.fel_rows.values():
            if row.get("id") == posted_invoice_fel_row_id:
                row["electronicDocumentStatus"] = self.stamp_status
                row["errorDescription"] = "" if self.stamp_status == "Stamp Received" else "SAT test failure"
                return {"status": "stamp_requested", "id": posted_invoice_fel_row_id}
        raise ValueError(f"Unknown FEL row {posted_invoice_fel_row_id}")

    def get_gt_registered_invoice_by_number(self, invoice_number: str, *, company_id=None, market=None):
        return {"No": invoice_number, "Estado_DTE": self.fel_rows.get(invoice_number, {}).get("electronicDocumentStatus")}

    def get_sales_invoice_pdf_content(self, sales_invoice_id: str, *, company_id=None, market=None):
        return _make_invoice_pdf_bytes()

    def get_company_metadata(self, *, company_id=None, market=None):
        return {"name": "MTM_GT_PROD"}

    def build_sales_invoice_url(self, *, company_name: str, invoice_number: str):
        return f"https://bc.example/{company_name}/{invoice_number}"


def _invoice_settings() -> InvoiceAutomationSettings:
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
        charge_mappings=(
            InvoiceChargeMapping(
                charge_name="Freight (Ocean/Truck/Air)",
                clickup_field_name="Freight (Ocean/Truck/Air)",
                clickup_field_id="field-freight",
                bc_item_number="INT000000026",
                bc_description="COORDINACION VIRTUAL DE TRANSPORTE MARITIMO",
                tax_group="NO IVA",
            ),
            InvoiceChargeMapping(
                charge_name="Destination Charges",
                clickup_field_name="Destination Charges",
                clickup_field_id="field-destination",
                bc_item_number="NAT00000028",
                bc_description="DESTINATION CHARGES",
                tax_group="IVA 12",
            ),
        ),
    )


def _invoice_task(*, status: str) -> dict[str, object]:
    return {
        "id": "task-1",
        "custom_id": "MTMLXGT-1",
        "name": "Invoice test",
        "status": {"status": status},
        "due_date": "2026-06-03",
        "custom_fields": [
            {
                "id": "owner-country",
                "name": "Owner Country/",
                "value": 0,
                "type_config": {"options": [{"name": "Guatemala", "orderindex": 0}]},
            },
            {"id": "eta", "name": "ETA", "value": "2026-06-03"},
            {"id": "currency", "name": "Invoice Currency", "value": "USD"},
            {
                "id": "invoice-status",
                "name": "Estatus de facturación (USD)/",
                "value": 0,
                "type": "drop_down",
                "type_config": {
                    "options": [
                        {"id": "status-ok", "name": "OK Finops", "orderindex": 0},
                        {"id": "status-ready", "name": "Listo para facturar", "orderindex": 1},
                        {"id": "status-invoiced", "name": "Facturada", "orderindex": 2},
                    ]
                },
            },
            {"id": "customer-id", "name": "Business Central Customer ID", "value": "customer-1"},
            {"id": "customer-number", "name": "Business Central Customer Number", "value": "C0001"},
            {"id": "invoice-number", "name": "Business Central Invoice Number", "value": None},
            {"id": "invoice-id", "name": "Business Central Invoice ID", "value": None},
            {"id": "5d67859a-1ae0-4cda-9f57-2a89bf1ff259", "name": "Invoice to Client", "value": None},
            {"id": "reference", "name": "Reference", "value": "PO-1"},
            {"id": "field-freight", "name": "Freight (Ocean/Truck/Air)", "value": "125.50"},
            {"id": "field-destination", "name": "Destination Charges", "value": "45.00"},
        ],
    }


def test_invoice_webhook_dry_run_previews_bridge_without_mutation(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    fake_clickup_clients: list[_FakeInvoiceClickUpClient] = []
    fake_bc_clients: list[_FakeInvoiceBCClient] = []

    def clickup_factory(settings):
        client = _FakeInvoiceClickUpClient(settings)
        fake_clickup_clients.append(client)
        return client

    def bc_factory(settings):
        client = _FakeInvoiceBCClient(settings)
        fake_bc_clients.append(client)
        return client

    monkeypatch.setenv("CLICKUP_ACCESS_TOKEN", "pk_test")
    monkeypatch.setenv("CLICKUP_DEFAULT_WORKSPACE_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TEAM_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", "true")
    monkeypatch.setenv("CLICKUP_INVOICE_WEBHOOK_APPLY", "false")
    monkeypatch.setattr("webhook_bridge.main.ClickUpClient", clickup_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralClient", bc_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralSettings.from_env", lambda: object())
    monkeypatch.setattr("webhook_bridge.main.InvoiceAutomationSettings.from_env", _invoice_settings)

    response = TestClient(app).post(
        "/clickup/webhooks/invoice-sync/task-1",
        headers={"Authorization": "Bearer expected-token"},
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "dry_run"
    assert payload["action"] == "would_update_status,preview_sales_invoice"
    assert payload["result"]["status"] == "dry_run_ready"
    assert payload["result"]["invoice_count"] == 2
    assert payload["result"]["invoice_groups"] == ["INT", "NAT"]
    assert payload["result"]["invoice_validation"]["status"] == "passed"
    assert payload["result"]["invoice_validation"]["expected_totals_by_group"] == {
        "INT": 125.5,
        "NAT": 45.0,
    }
    assert payload["result"]["proposed_bc_line_payloads"][0]["lineType"] == "Item"
    assert payload["result"]["proposed_bc_line_payloads"][0]["lineObjectNumber"] == "INT000000026"
    assert payload["result"]["proposed_bc_invoices"][0]["proposed_bc_payload"]["externalDocumentNumber"] == "PO-1-INT"
    assert payload["result"]["proposed_bc_invoices"][1]["proposed_bc_payload"]["externalDocumentNumber"] == "PO-1-NAT"
    assert payload["result"]["proposed_bc_invoices"][0]["proposed_bc_payload"]["paymentTermsId"] == "term-30-days"
    assert "dueDate" not in payload["result"]["proposed_bc_invoices"][0]["proposed_bc_payload"]
    assert fake_clickup_clients[0].updated_tasks == []
    assert fake_clickup_clients[0].field_updates == []
    assert fake_bc_clients[0].created_headers == []
    assert fake_bc_clients[0].created_lines == []


def test_invoice_webhook_apply_creates_invoice_and_writes_back(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    fake_clickup_clients: list[_FakeInvoiceClickUpClient] = []
    fake_bc_clients: list[_FakeInvoiceBCClient] = []

    def clickup_factory(settings):
        client = _FakeInvoiceClickUpClient(settings)
        fake_clickup_clients.append(client)
        return client

    def bc_factory(settings):
        client = _FakeInvoiceBCClient(settings)
        fake_bc_clients.append(client)
        return client

    monkeypatch.setenv("CLICKUP_ACCESS_TOKEN", "pk_test")
    monkeypatch.setenv("CLICKUP_DEFAULT_WORKSPACE_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TEAM_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", "true")
    monkeypatch.setenv("CLICKUP_INVOICE_WEBHOOK_APPLY", "true")
    monkeypatch.setattr("webhook_bridge.main.ClickUpClient", clickup_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralClient", bc_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralSettings.from_env", lambda: object())
    monkeypatch.setattr("webhook_bridge.main.InvoiceAutomationSettings.from_env", _invoice_settings)

    response = TestClient(app).post(
        "/clickup/webhooks/invoice-sync/task-1",
        headers={"Authorization": "Bearer expected-token"},
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "apply"
    assert payload["action"] == (
        "update_status,create_sales_invoice,post_sales_invoice,sync_fel_descriptions,"
        "stamp_fel_invoice,upload_invoice_pdfs,comment_invoice_details,set_facturada_status"
    )
    assert payload["result"]["status"] == "applied"
    assert fake_bc_clients[0].created_headers[0]["externalDocumentNumber"] == "PO-1-INT"
    assert fake_bc_clients[0].created_headers[1]["externalDocumentNumber"] == "PO-1-NAT"
    assert fake_bc_clients[0].created_headers[0]["paymentTermsId"] == "term-30-days"
    assert "dueDate" not in fake_bc_clients[0].created_headers[0]
    assert fake_bc_clients[0].created_lines[0]["lineObjectNumber"] == "INT000000026"
    assert fake_bc_clients[0].created_lines[1]["lineObjectNumber"] == "NAT00000028"
    assert fake_clickup_clients[0].custom_field_uploads == [
        {
            "workspace_id": "8451352",
            "field_id": "5d67859a-1ae0-4cda-9f57-2a89bf1ff259",
            "file_name": "PO-1-INT.pdf",
            "mime_type": "application/pdf",
            "id": "attachment-1",
        },
        {
            "workspace_id": "8451352",
            "field_id": "5d67859a-1ae0-4cda-9f57-2a89bf1ff259",
            "file_name": "PO-1-NAT.pdf",
            "mime_type": "application/pdf",
            "id": "attachment-2",
        },
    ]
    assert fake_clickup_clients[0].file_field_updates == [
        {
            "task_id": "task-1",
            "field_id": "5d67859a-1ae0-4cda-9f57-2a89bf1ff259",
            "attachment_ids": ["attachment-1", "attachment-2"],
        }
    ]
    assert payload["result"]["delivery"]["pdf_field_update"] == {
        "task_id": "task-1",
        "field_id": "5d67859a-1ae0-4cda-9f57-2a89bf1ff259",
        "attachment_ids": ["attachment-1", "attachment-2"],
    }
    assert payload["result"]["final_status_update"] == {
        "task_id": "task-1",
        "field_id": "invoice-status",
        "value": "status-invoiced",
    }
    assert payload["result"]["delivery"]["final_status_update"] == {
        "task_id": "task-1",
        "field_id": "invoice-status",
        "value": "status-invoiced",
    }
    assert len(fake_clickup_clients[0].comments) == 1
    assert "INT invoice" in fake_clickup_clients[0].comments[0]["comment_text"]
    assert "NAT invoice" in fake_clickup_clients[0].comments[0]["comment_text"]
    assert fake_clickup_clients[0].field_updates == [
        {"task_id": "task-1", "field_id": "invoice-status", "value": "status-ready"},
        {"task_id": "task-1", "field_id": "invoice-status", "value": "status-invoiced"},
    ]


def test_invoice_webhook_apply_blocks_before_bc_creation_when_pdf_field_missing(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    fake_clickup_clients: list[_FakeInvoiceClickUpClient] = []
    fake_bc_clients: list[_FakeInvoiceBCClient] = []

    class MissingPdfFieldClickUpClient(_FakeInvoiceClickUpClient):
        def get_task(self, *args, **kwargs):
            task = super().get_task(*args, **kwargs)
            task["custom_fields"] = [
                field
                for field in task["custom_fields"]
                if field.get("id") != "5d67859a-1ae0-4cda-9f57-2a89bf1ff259"
            ]
            return task

    def clickup_factory(settings):
        client = MissingPdfFieldClickUpClient(settings)
        fake_clickup_clients.append(client)
        return client

    def bc_factory(settings):
        client = _FakeInvoiceBCClient(settings)
        fake_bc_clients.append(client)
        return client

    monkeypatch.setenv("CLICKUP_ACCESS_TOKEN", "pk_test")
    monkeypatch.setenv("CLICKUP_DEFAULT_WORKSPACE_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TEAM_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", "true")
    monkeypatch.setenv("CLICKUP_INVOICE_WEBHOOK_APPLY", "true")
    monkeypatch.setattr("webhook_bridge.main.ClickUpClient", clickup_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralClient", bc_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralSettings.from_env", lambda: object())
    monkeypatch.setattr("webhook_bridge.main.InvoiceAutomationSettings.from_env", _invoice_settings)

    response = TestClient(app).post(
        "/clickup/webhooks/invoice-sync/task-1",
        headers={"Authorization": "Bearer expected-token"},
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "apply"
    assert payload["action"] == "update_status,validate_invoice_pdf_field,comment_invoice_error"
    assert payload["result"]["status"] == "missing_invoice_pdf_field"
    assert "Invoice to Client custom field" in payload["result"]["message"]
    assert payload["result"]["error_comment"]["task_id"] == "task-1"
    assert "ERROR EN PROCESO DE FACTURACION" in payload["result"]["error_comment"]["comment_text"]
    assert "VALIDACION DE CLICKUP" in payload["result"]["error_comment"]["comment_text"]
    assert fake_bc_clients[0].created_headers == []
    assert fake_bc_clients[0].created_lines == []
    assert fake_clickup_clients[0].custom_field_uploads == []


def test_invoice_webhook_apply_does_not_mark_facturada_when_pdf_upload_fails(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    fake_clickup_clients: list[_FakeInvoiceClickUpClient] = []
    fake_bc_clients: list[_FakeInvoiceBCClient] = []

    class FailingPdfUploadClickUpClient(_FakeInvoiceClickUpClient):
        def upload_custom_field_attachment(self, *args, **kwargs):
            raise RuntimeError("ClickUp upload failed")

    def clickup_factory(settings):
        client = FailingPdfUploadClickUpClient(settings)
        fake_clickup_clients.append(client)
        return client

    def bc_factory(settings):
        client = _FakeInvoiceBCClient(settings)
        fake_bc_clients.append(client)
        return client

    monkeypatch.setenv("CLICKUP_ACCESS_TOKEN", "pk_test")
    monkeypatch.setenv("CLICKUP_DEFAULT_WORKSPACE_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TEAM_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", "true")
    monkeypatch.setenv("CLICKUP_INVOICE_WEBHOOK_APPLY", "true")
    monkeypatch.setattr("webhook_bridge.main.ClickUpClient", clickup_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralClient", bc_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralSettings.from_env", lambda: object())
    monkeypatch.setattr("webhook_bridge.main.InvoiceAutomationSettings.from_env", _invoice_settings)

    response = TestClient(app).post(
        "/clickup/webhooks/invoice-sync/task-1",
        headers={"Authorization": "Bearer expected-token"},
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "apply"
    assert payload["result"]["status"] == "failed_post_creation"
    assert "Invoice delivery failed after creation" in payload["result"]["message"]
    assert "ClickUp upload failed" not in response.text
    assert payload["result"]["error_comment"]["task_id"] == "task-1"
    assert "ERROR EN PROCESO DE FACTURACION" in payload["result"]["error_comment"]["comment_text"]
    assert "ENTREGA DE PDF" in payload["result"]["error_comment"]["comment_text"]
    assert "Invoice delivery failed after creation" in payload["result"]["error_comment"]["comment_text"]
    assert fake_bc_clients[0].created_headers
    assert fake_clickup_clients[0].field_updates == [
        {"task_id": "task-1", "field_id": "invoice-status", "value": "status-ready"},
    ]


def test_invoice_webhook_apply_writes_spanish_comment_when_fel_stamp_fails(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    fake_clickup_clients: list[_FakeInvoiceClickUpClient] = []
    fake_bc_clients: list[_FakeInvoiceBCClient] = []

    class FailingStampBCClient(_FakeInvoiceBCClient):
        def __init__(self, settings) -> None:
            super().__init__(settings)
            self.stamp_status = "Stamp Error"

    def clickup_factory(settings):
        client = _FakeInvoiceClickUpClient(settings)
        fake_clickup_clients.append(client)
        return client

    def bc_factory(settings):
        client = FailingStampBCClient(settings)
        fake_bc_clients.append(client)
        return client

    monkeypatch.setenv("CLICKUP_ACCESS_TOKEN", "pk_test")
    monkeypatch.setenv("CLICKUP_DEFAULT_WORKSPACE_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TEAM_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", "true")
    monkeypatch.setenv("CLICKUP_INVOICE_WEBHOOK_APPLY", "true")
    monkeypatch.setattr("webhook_bridge.main.ClickUpClient", clickup_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralClient", bc_factory)
    monkeypatch.setattr("webhook_bridge.main.BusinessCentralSettings.from_env", lambda: object())
    monkeypatch.setattr("webhook_bridge.main.InvoiceAutomationSettings.from_env", _invoice_settings)
    monkeypatch.setattr("clickup_integration.invoice_sync.time.sleep", lambda _seconds: None)

    response = TestClient(app).post(
        "/clickup/webhooks/invoice-sync/task-1",
        headers={"Authorization": "Bearer expected-token"},
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "apply"
    assert payload["result"]["status"] == "failed_post_creation"
    assert payload["result"]["failed_stage"] == "stamp_fel_invoice"
    assert "Invoice finalization failed" in payload["result"]["message"]
    assert "FEL stamp was not received" not in response.text
    assert payload["result"]["error_comment"]["task_id"] == "task-1"
    assert "ERROR EN PROCESO DE FACTURACION" in payload["result"]["error_comment"]["comment_text"]
    assert "TIMBRADO FEL/SAT" in payload["result"]["error_comment"]["comment_text"]
    assert "GTFVRTEST1" in payload["result"]["error_comment"]["comment_text"]
    assert fake_bc_clients[0].created_headers
    assert fake_clickup_clients[0].custom_field_uploads == []
    assert fake_clickup_clients[0].field_updates == [
        {"task_id": "task-1", "field_id": "invoice-status", "value": "status-ready"},
    ]
