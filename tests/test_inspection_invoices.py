from __future__ import annotations

import json
from datetime import date

from inspection_invoices.service import issue_inspection_invoice, prepare_inspection_invoice_preview
from webhook_bridge import main as webhook_main
from webhook_bridge.main import app


PAYLOAD_FIELD_ID = "5e825df5-9a5e-45f8-87cf-0b1daa16b38f"


def _task(*, payload: dict | None = None) -> dict:
    payload = payload or {
        "schema_version": "mtm.inspection-invoice.v1",
        "task_id": "86e29y1gx",
        "bc_item": "INT000000031",
        "description": "INSPECTION AT ORIGIN",
        "po_reference": "LGDCH91C5VA702240",
        "customer_name": "MAGNA MOTORS GUATEMALA, SOCIEDAD ANONIMA",
        "customer_number": "C00095",
        "customer_id": "customer-id",
        "customer_tax_id": "98085638",
        "destination_country_code": "GT",
        "unit_price": 65,
        "quantity": 1,
        "currency": "USD",
        "inspection_date": "2026-07-03",
        "vendor": "Asia IBS",
    }
    return {
        "id": "86e29y1gx",
        "custom_id": "MTLXMGN-316",
        "name": "LGDCH91C5VA702240(DRYRUN TEST)",
        "list": {"id": "901707774763"},
        "custom_fields": [
            {"id": PAYLOAD_FIELD_ID, "name": "Invoice Payload", "value": json.dumps(payload)},
            {
                "id": "destination-country",
                "name": "Destination Country",
                "type": "drop_down",
                "value": 0,
                "type_config": {
                    "options": [
                        {"id": "guatemala", "name": "Guatemala", "orderindex": 0},
                        {"id": "el-salvador", "name": "El Salvador", "orderindex": 1},
                        {"id": "costa-rica", "name": "Costa Rica", "orderindex": 2},
                    ]
                },
            },
        ],
    }


class _BC:
    def __init__(self) -> None:
        self.headers: list[dict] = []
        self.lines: list[dict] = []
        self.posted = False
        self.stamped = False

    def _customer(self):
        return {
            "id": "customer-id",
            "number": "C00095",
            "displayName": "MAGNA MOTORS GUATEMALA SOCIEDAD ANONIMA",
            "country": "GT",
            "taxRegistrationNumber": "98085638",
            "currencyCode": "USD",
            "paymentTermsId": "term-30",
        }

    def get_customer_by_id(self, customer_id: str, *, market: str):
        assert customer_id == "customer-id"
        assert market == "GT"
        return self._customer()

    def find_entities(self, entity_name: str, **kwargs):
        if entity_name == "customers":
            assert kwargs["filters"] == "number eq 'C00095'"
            return [self._customer()]
        assert entity_name == "salesInvoices"
        return []

    def get_customer_invoicing_by_number(self, number: str, *, market: str):
        assert number == "C00095"
        assert market == "GT"
        return {"felCountryReady": True, "resolvedFelCountryCode": "GT"}

    def resolve_item_by_number(self, number: str, *, market: str):
        assert number == "INT000000031"
        assert market == "GT"
        return {"id": "item-id", "number": number, "blocked": False}

    def create_sales_invoice(self, payload: dict, *, market: str):
        self.headers.append(payload)
        return {"id": "invoice-id", "number": "DRAFT-1", **payload}

    def create_sales_invoice_line(self, invoice_id: str, payload: dict, *, market: str):
        assert invoice_id == "invoice-id"
        self.lines.append(payload)
        return {"id": f"line-{len(self.lines)}", **payload}

    def post_sales_invoice(self, invoice_id: str, *, market: str):
        assert invoice_id == "invoice-id"
        self.posted = True
        return {}

    def get_entity(self, entity_name: str, invoice_id: str, *, market: str):
        assert entity_name == "salesInvoices"
        if not self.posted:
            return {"id": invoice_id, "number": "DRAFT-1"}
        return {
            "id": invoice_id,
            "number": "GTFVR0005001",
            "externalDocumentNumber": "MTLXMGN-316-INT",
        }

    def get_posted_sales_invoice_by_external_document_number(self, *_args, **_kwargs):
        return None

    def get_posted_invoice_fel_description_by_number(self, invoice_number: str, *, market: str):
        assert invoice_number == "GTFVR0005001"
        return {
            "id": "fel-id",
            "electronicDocumentStatus": "Stamp Received" if self.stamped else "Pending",
        }

    def sync_posted_invoice_fel_line_descriptions(self, fel_id: str, *, market: str):
        assert fel_id == "fel-id"
        return {}

    def stamp_posted_invoice_fel(self, fel_id: str, *, market: str):
        assert fel_id == "fel-id"
        self.stamped = True
        return {}


def test_preview_builds_a_task_idempotent_bc_invoice(monkeypatch) -> None:
    monkeypatch.setenv("INSPECTION_INVOICE_MARKET", "GT")
    monkeypatch.setenv("INSPECTION_INVOICE_CURRENCY", "USD")
    preview = prepare_inspection_invoice_preview(task=_task(), bc_client=_BC(), today=date(2026, 7, 11))

    assert preview["status"] == "dry_run_ready"
    assert preview["proposed_bc_payload"]["externalDocumentNumber"] == "MTLXMGN-316-INT"
    assert preview["proposed_bc_payload"]["customerPurchaseOrderReference"] == "LGDCH91C5VA702240"
    assert preview["proposed_bc_payload"]["invoiceDate"] == "2026-07-11"
    assert preview["proposed_bc_line_payloads"] == [{
        "lineType": "Item",
        "lineObjectNumber": "INT000000031",
        "itemId": "item-id",
        "description": "INSPECTION AT ORIGIN",
        "quantity": 1.0,
        "unitPrice": 65.0,
        "taxCode": "NO IVA",
    }]
    assert preview["total"] == 65.0


def test_preview_rejects_a_payload_for_another_task() -> None:
    task = _task()
    payload = json.loads(task["custom_fields"][0]["value"])
    payload["task_id"] = "another-task"
    task["custom_fields"][0]["value"] = json.dumps(payload)

    result = prepare_inspection_invoice_preview(task=task, bc_client=_BC())

    assert result["status"] == "invalid_invoice_payload"
    assert "does not match" in result["message"]


def test_preview_ignores_a_cancelled_invoice_with_the_same_reference() -> None:
    class _BCCancelledInvoice(_BC):
        def find_entities(self, entity_name: str, **kwargs):
            if entity_name == "customers":
                return super().find_entities(entity_name, **kwargs)
            assert entity_name == "salesInvoices"
            return [
                {
                    "id": "cancelled-invoice-id",
                    "number": "GTFVR0004999",
                    "status": "Canceled",
                    "lastModifiedDateTime": "2026-07-21T23:00:00Z",
                }
            ]

    result = prepare_inspection_invoice_preview(task=_task(), bc_client=_BCCancelledInvoice())

    assert result["status"] == "dry_run_ready"


def test_preview_accepts_clickup_task_token_prefix() -> None:
    task = _task()
    payload = json.loads(task["custom_fields"][0]["value"])
    payload["task_id"] = "task:86e29y1gx"
    task["custom_fields"][0]["value"] = json.dumps(payload)

    result = prepare_inspection_invoice_preview(task=task, bc_client=_BC())

    assert result["status"] == "dry_run_ready"


def test_preview_rejects_a_payload_price_that_differs_from_clickup_origin_price() -> None:
    task = _task()
    task["custom_fields"].append(
        {
            "id": "origin-price",
            "name": "Previo en origen (USD)",
            "type": "currency",
            "value": "75",
        }
    )

    result = prepare_inspection_invoice_preview(task=task, bc_client=_BC())

    assert result["status"] == "inspection_price_mismatch"
    assert "65.00" in result["message"]
    assert "75.00" in result["message"]


def test_issue_creates_posts_and_stamps_after_preflight(monkeypatch) -> None:
    monkeypatch.setenv("INSPECTION_INVOICE_MARKET", "GT")
    bc = _BC()

    result = issue_inspection_invoice(task=_task(), bc_client=bc, today=date(2026, 7, 11))

    assert result["status"] == "applied"
    assert result["completed_stages"] == [
        "create_sales_invoice",
        "create_sales_invoice_lines",
        "post_sales_invoice",
        "sync_fel_descriptions",
        "stamp_fel_invoice",
    ]
    assert len(bc.headers) == 1
    assert len(bc.lines) == 1
    assert bc.lines[0]["lineType"] == "Item"
    assert result["finalized_invoices"][0]["number"] == "GTFVR0005001"


def test_issue_recovers_an_existing_stamped_invoice_for_clickup_delivery(monkeypatch) -> None:
    monkeypatch.setenv("INSPECTION_INVOICE_MARKET", "GT")

    class ExistingInvoiceBC(_BC):
        def find_entities(self, entity_name: str, **kwargs):
            if entity_name == "customers":
                return super().find_entities(entity_name, **kwargs)
            assert entity_name == "salesInvoices"
            return [
                {
                    "id": "existing-invoice-id",
                    "number": "GTFVR0005002",
                    "externalDocumentNumber": "MTLXMGN-316-INT",
                }
            ]

        def get_posted_invoice_fel_description_by_number(self, invoice_number: str, *, market: str):
            assert invoice_number == "GTFVR0005002"
            assert market == "GT"
            return {"id": "fel-id", "electronicDocumentStatus": "Stamp Received"}

    bc = ExistingInvoiceBC()
    result = issue_inspection_invoice(task=_task(), bc_client=bc)

    assert result["status"] == "applied"
    assert result["recovered_existing_invoice"] is True
    assert result["completed_stages"] == ["recover_existing_posted_invoice"]
    assert result["finalized_invoices"][0]["number"] == "GTFVR0005002"
    assert bc.headers == []
    assert bc.lines == []


def test_preview_blocks_missing_immutable_customer_identity() -> None:
    task = _task()
    payload = json.loads(task["custom_fields"][0]["value"])
    payload.pop("customer_id")
    task["custom_fields"][0]["value"] = json.dumps(payload)

    result = prepare_inspection_invoice_preview(task=task, bc_client=_BC())

    assert result["status"] == "invalid_invoice_payload"
    assert "customer_id is required" in result["message"]


def test_preview_blocks_customer_country_mismatch_before_issue() -> None:
    class CostaRicaCustomerBC(_BC):
        def _customer(self):
            return {
                "id": "customer-id",
                "number": "C00095",
                "displayName": "MAGMA AUTOMOTIVE DEALERSHIP S.A.",
                "country": "CR",
                "taxRegistrationNumber": "3101905266",
                "currencyCode": "USD",
                "paymentTermsId": "term-30",
            }

    task = _task()
    payload = json.loads(task["custom_fields"][0]["value"])
    payload.update(
        {
            "customer_tax_id": "3101905266",
            "destination_country_code": "SV",
        }
    )
    task["custom_fields"][0]["value"] = json.dumps(payload)
    task["custom_fields"][1]["value"] = 1

    result = prepare_inspection_invoice_preview(task=task, bc_client=CostaRicaCustomerBC())

    assert result["status"] == "customer_destination_country_mismatch"
    assert "CR" in result["message"]
    assert "SV" in result["message"]


def test_preview_blocks_payload_destination_that_disagrees_with_clickup() -> None:
    task = _task()
    payload = json.loads(task["custom_fields"][0]["value"])
    payload["destination_country_code"] = "SV"
    task["custom_fields"][0]["value"] = json.dumps(payload)

    result = prepare_inspection_invoice_preview(task=task, bc_client=_BC())

    assert result["status"] == "payload_destination_country_mismatch"
    assert "SV" in result["message"]
    assert "GT" in result["message"]


def test_preview_blocks_customer_tax_id_mismatch_before_issue() -> None:
    task = _task()
    payload = json.loads(task["custom_fields"][0]["value"])
    payload["customer_tax_id"] = "wrong-tax-id"
    task["custom_fields"][0]["value"] = json.dumps(payload)

    result = prepare_inspection_invoice_preview(task=task, bc_client=_BC())

    assert result["status"] == "customer_tax_id_mismatch"


def test_preview_blocks_mismatched_customer_id_and_number() -> None:
    class MismatchedIdentityBC(_BC):
        def get_customer_by_id(self, customer_id: str, *, market: str):
            assert customer_id == "customer-id"
            assert market == "GT"
            return {
                **self._customer(),
                "id": "different-customer-id",
            }

    result = prepare_inspection_invoice_preview(task=_task(), bc_client=MismatchedIdentityBC())

    assert result["status"] == "customer_identity_mismatch"


def test_preview_blocks_an_existing_reference_under_another_customer() -> None:
    class ConflictingReferenceBC(_BC):
        def find_entities(self, entity_name: str, **kwargs):
            if entity_name == "customers":
                return super().find_entities(entity_name, **kwargs)
            assert entity_name == "salesInvoices"
            assert "startswith(externalDocumentNumber" in kwargs["filters"]
            return [
                {
                    "id": "wrong-customer-invoice",
                    "number": "GTFVR0004999",
                    "customerNumber": "C00094",
                    "externalDocumentNumber": "MTLXMGN-316-INT-02",
                    "status": "Open",
                }
            ]

    result = prepare_inspection_invoice_preview(task=_task(), bc_client=ConflictingReferenceBC())

    assert result["status"] == "conflicting_invoice_reference"
    assert "C00094" in result["message"]


def test_inspection_invoice_webhook_requires_a_valid_webhook_token(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")

    response = TestClient(app).post(
        "/clickup/webhooks/inspection-invoice-sync/MTLXMGN-316",
        headers={"Authorization": "Bearer wrong-token"},
        json={},
    )

    assert response.status_code == 401


def test_inspection_invoice_webhook_prefers_dedicated_token(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "shared-token")
    monkeypatch.setenv("INSPECTION_INVOICE_WEBHOOK_TOKEN", "inspection-token")

    shared_response = TestClient(app).post(
        "/clickup/webhooks/inspection-invoice-sync",
        headers={"Authorization": "Bearer shared-token"},
        json={},
    )
    dedicated_response = TestClient(app).post(
        "/clickup/webhooks/inspection-invoice-sync",
        headers={"Authorization": "Bearer inspection-token"},
        json={},
    )

    assert shared_response.status_code == 401
    assert dedicated_response.status_code == 200
    assert dedicated_response.json() == {"status": "ignored", "reason": "missing_task_id"}


def test_inspection_invoice_readiness_is_dry_run_by_default(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("CLICKUP_ACCESS_TOKEN", "pk_test")
    monkeypatch.delenv("INSPECTION_INVOICE_WEBHOOK_APPLY", raising=False)

    response = TestClient(app).get("/clickup/webhooks/inspection-invoice-sync/readiness")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["apply_mode"] is False


def test_inspection_invoice_webhook_returns_the_live_style_dry_run(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    class _ClickUp:
        settings = type("Settings", (), {"default_workspace_id": "8451352"})()

        def get_task(self, task_id: str, **_kwargs):
            assert task_id == "MTLXMGN-316"
            return _task()

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("INSPECTION_INVOICE_WEBHOOK_APPLY", "false")
    monkeypatch.setattr(webhook_main.ClickUpSettings, "from_env", lambda: object())
    monkeypatch.setattr(webhook_main.BusinessCentralSettings, "from_env", lambda: object())
    monkeypatch.setattr(webhook_main, "ClickUpClient", lambda _settings: _ClickUp())
    monkeypatch.setattr(webhook_main, "BusinessCentralClient", lambda _settings: _BC())

    response = TestClient(app).post(
        "/clickup/webhooks/inspection-invoice-sync/MTLXMGN-316",
        headers={"Authorization": "Bearer expected-token"},
        json={},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    assert response.json()["mode"] == "dry_run"
    assert response.json()["result"]["proposed_bc_payload"]["externalDocumentNumber"] == "MTLXMGN-316-INT"


def test_inspection_invoice_webhook_marks_the_invoice_custom_field(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    captured: dict[str, object] = {}

    class _ClickUp:
        settings = type("Settings", (), {"default_workspace_id": "8451352"})()

        def get_task(self, _task_id: str, **_kwargs):
            return _task()

    def finalize(**kwargs):
        captured["mark_status"] = kwargs["mark_status"]
        return {"final_status_update": {"field_id": "invoice-status", "value": "facturada"}}

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("CLICKUP_ACCESS_TOKEN", "pk_test")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TEAM_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", "true")
    monkeypatch.setenv("INSPECTION_INVOICE_WEBHOOK_APPLY", "true")
    monkeypatch.setattr(webhook_main.ClickUpSettings, "from_env", lambda: object())
    monkeypatch.setattr(webhook_main.BusinessCentralSettings, "from_env", lambda: object())
    monkeypatch.setattr(webhook_main.InvoiceAutomationSettings, "from_env", lambda: object())
    monkeypatch.setattr(webhook_main, "ClickUpClient", lambda _settings: _ClickUp())
    monkeypatch.setattr(webhook_main, "BusinessCentralClient", lambda _settings: object())
    monkeypatch.setattr(
        webhook_main,
        "prepare_inspection_invoice_preview",
        lambda **_kwargs: {"status": "dry_run_ready"},
    )
    monkeypatch.setattr(
        webhook_main,
        "issue_inspection_invoice",
        lambda **_kwargs: {
            "status": "applied",
            "finalized_invoices": [
                {
                    "number": "GTFVR0005001",
                    "posted_invoice_after_stamp": {"id": "invoice-id", "number": "GTFVR0005001"},
                }
            ],
        },
    )
    monkeypatch.setattr(webhook_main, "finalize_clickup_issued_invoices", finalize)

    response = TestClient(app).post(
        "/clickup/webhooks/inspection-invoice-sync",
        headers={"Authorization": "Bearer expected-token"},
        json={"payload": {"id": "86e29y1gx"}},
    )

    assert response.status_code == 200
    assert captured["mark_status"] is True
    assert response.json()["final_status_update"] == {
        "field_id": "invoice-status",
        "value": "facturada",
    }


def test_inspection_invoice_webhook_recovers_duplicate_posted_invoice_delivery(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    captured: dict[str, object] = {}

    class _ClickUp:
        settings = type("Settings", (), {"default_workspace_id": "8451352"})()

        def get_task(self, _task_id: str, **_kwargs):
            return _task()

    def finalize(**kwargs):
        captured["recovered_existing_invoice"] = kwargs["invoice_result"].get(
            "recovered_existing_invoice"
        )
        return {"final_status_update": {"field_id": "invoice-status", "value": "facturada"}}

    monkeypatch.setenv("CLICKUP_WEBHOOK_TOKEN", "expected-token")
    monkeypatch.setenv("CLICKUP_ACCESS_TOKEN", "pk_test")
    monkeypatch.setenv("CLICKUP_WEBHOOK_TEAM_ID", "8451352")
    monkeypatch.setenv("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", "true")
    monkeypatch.setenv("INSPECTION_INVOICE_WEBHOOK_APPLY", "true")
    monkeypatch.setattr(webhook_main.ClickUpSettings, "from_env", lambda: object())
    monkeypatch.setattr(webhook_main.BusinessCentralSettings, "from_env", lambda: object())
    monkeypatch.setattr(webhook_main.InvoiceAutomationSettings, "from_env", lambda: object())
    monkeypatch.setattr(webhook_main, "ClickUpClient", lambda _settings: _ClickUp())
    monkeypatch.setattr(webhook_main, "BusinessCentralClient", lambda _settings: object())
    monkeypatch.setattr(
        webhook_main,
        "prepare_inspection_invoice_preview",
        lambda **_kwargs: {"status": "duplicate_invoice"},
    )
    monkeypatch.setattr(
        webhook_main,
        "issue_inspection_invoice",
        lambda **_kwargs: {
            "status": "applied",
            "recovered_existing_invoice": True,
            "finalized_invoices": [
                {
                    "number": "GTFVR0005002",
                    "posted_invoice_after_stamp": {"id": "invoice-id", "number": "GTFVR0005002"},
                }
            ],
        },
    )
    monkeypatch.setattr(webhook_main, "finalize_clickup_issued_invoices", finalize)

    response = TestClient(app).post(
        "/clickup/webhooks/inspection-invoice-sync",
        headers={"Authorization": "Bearer expected-token"},
        json={"payload": {"id": "86e29y1gx"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    assert captured["recovered_existing_invoice"] is True
