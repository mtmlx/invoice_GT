"""Regression canary for the two omitted MTMLXGT-32106 INT charges.

ClickUp amounts are shipment totals, not per-container rates. Exact-item
historical BC examples used quantity one; the user-approved rule now follows
ocean freight: container quantity with total/count unit prices and NO IVA.
These tests only build previews/use fakes, never production fiscal documents.
"""

from copy import deepcopy
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from clickup_integration.invoice_sync import (
    InvoiceAutomationSettings,
    apply_clickup_bc_sales_invoice,
    issue_clickup_bc_sales_invoice,
    _validate_guarded_duplicate_retry,
    load_invoice_charge_mappings,
    prepare_clickup_bc_sales_invoice_preview,
)
from test_clickup_invoice_sync import FakeBCInvoiceClient, make_settings, make_clickup_summary


MAPPING_PATH = Path(__file__).resolve().parents[1] / "config/invoice_charge_mappings/gt.json"
PRIORITY_ID = "005071cb-d840-4adf-8a30-6e726c375e2c"
OBS_ID = "a6f9e8c1-f92d-4088-89fe-7be84473802d"
EXPECTED = {
    PRIORITY_ID: ("Priority Loading", "INT000000035", "PRIORITY LOADING", "6000.00"),
    OBS_ID: ("OBS", "INT000000034", "OBS", "1980.00"),
}
CANARY_AMOUNTS = {
    "c8935d06-7f43-46c5-a1ef-444314dca0e9": "16800.00",
    "eed2e3aa-781e-4bbf-86a4-3353fc7d057d": "2400.00",
    "4ad13e04-a8c6-4069-9e6b-f5e4b6b734bd": "3600.00",
    "dc5001e4-cb9c-4cab-a306-b97faa323753": "600.00",
    "4c3fb385-15cf-4250-9e3c-e1c93434cb7f": "600.00",
    PRIORITY_ID: "6000.00",
    OBS_ID: "1980.00",
}


def canary():
    mappings = load_invoice_charge_mappings(MAPPING_PATH)
    settings = replace(make_settings(), charge_mappings=mappings)
    summary = make_clickup_summary(status="Listo para facturar")
    summary["custom_id"] = "MTMLXGT-32106"
    summary["custom_fields"]["Number of Containers"] = {"value": "6"}
    for mapping in mappings:
        summary["custom_fields"][mapping.clickup_field_name] = {
            "id": mapping.clickup_field_id,
            "value": CANARY_AMOUNTS.get(mapping.clickup_field_id, "0"),
        }
    # Costs and operational currency fields must not become billable lines.
    summary["custom_fields"]["- Cost OBS"] = {"id": "cost-obs-not-sales", "value": "1404"}
    summary["custom_fields"]["- Cost Freight"] = {"id": "cost-freight-not-sales", "value": "14244"}
    return summary, settings


def preview(summary, settings, client=None):
    return prepare_clickup_bc_sales_invoice_preview(
        clickup_summary=summary,
        bc_client=client or FakeBCInvoiceClient(),
        settings=settings,
        today=date(2026, 10, 9),
    )


def test_exact_gt_mapping_and_runtime_configuration(monkeypatch):
    monkeypatch.setenv("CLICKUP_INVOICE_MARKET", "GT")
    monkeypatch.setenv("CLICKUP_INVOICE_GT_CHARGE_MAPPING_PATH", str(MAPPING_PATH))
    settings = InvoiceAutomationSettings.from_env()
    assert len(settings.charge_mappings) == 22
    by_id = {mapping.clickup_field_id: mapping for mapping in settings.charge_mappings}
    assert len(by_id) == 22
    for field_id, (name, item, description, _) in EXPECTED.items():
        mapping = by_id[field_id]
        assert mapping.charge_name == mapping.clickup_field_name == name
        assert mapping.bc_item_number == item
        assert mapping.bc_description == description
        assert mapping.tax_group == "NO IVA"
        assert mapping.quantity_basis == "container_count"
        assert mapping.require_field_id is True


def test_full_canary_includes_seven_sales_fields_and_preserves_totals():
    summary, settings = canary()
    result = preview(summary, settings)
    assert result["status"] == "dry_run_ready"
    assert len(result["line_sources"]) == 7
    assert {line["source_field_id"] for line in result["line_sources"]} == set(CANARY_AMOUNTS)
    assert result["invoice_groups"] == ["INT", "NAT"]
    assert result["invoice_validation"]["status"] == "passed"
    assert result["invoice_validation"]["expected_total"] == 31980.00
    assert result["invoice_validation"]["line_payload_total"] == 31980.00
    assert result["invoice_validation"]["expected_totals_by_group"] == {"INT": 27180.00, "NAT": 4800.00}
    by_item = {line["lineObjectNumber"]: line for line in result["proposed_bc_line_payloads"]}
    for _, (_, item, description, amount) in EXPECTED.items():
        assert by_item[item]["description"] == description
        assert by_item[item]["quantity"] == 6
        assert by_item[item]["unitPrice"] == float(amount) / 6
        assert by_item[item]["quantity"] * by_item[item]["unitPrice"] == float(amount)
    assert by_item["INT000000026"]["quantity"] == 6
    assert by_item["INT000000026"]["unitPrice"] == 2800
    assert result["proposed_bc_payload"]["paymentTermsId"] == "term-30-days"
    assert "dueDate" not in result["proposed_bc_payload"]


def test_exact_uuid_matching_survives_renamed_field_and_excludes_cost_fields():
    summary, settings = canary()
    for name in ("Priority Loading", "OBS"):
        summary["custom_fields"][f"Renamed {name}"] = summary["custom_fields"].pop(name)
    result = preview(summary, settings)
    assert result["status"] == "dry_run_ready"
    assert result["invoice_validation"]["expected_total"] == 31980.00
    assert len(result["line_sources"]) == 7


@pytest.mark.parametrize("count", [1, 2, 3, 6, 7])
def test_new_charges_follow_freight_count_without_multiplying_source_total(count):
    summary, settings = canary()
    summary["custom_fields"]["Number of Containers"]["value"] = str(count)
    result = preview(summary, settings)
    assert result["status"] == "dry_run_ready"
    sources = {source["item_number"]: source for source in result["line_sources"]}
    for field_id, (_, item, _, amount) in EXPECTED.items():
        line = sources[item]
        assert line["source_field_id"] == field_id
        assert line["quantity"] == sources["INT000000026"]["quantity"] == count
        assert line["quantity_basis"] == "container_count"
        assert line["unit_price"] == pytest.approx(float(amount) / count)
        assert round(line["quantity"] * line["unit_price"], 2) == float(amount)
    assert result["invoice_validation"]["expected_total"] == 31980


@pytest.mark.parametrize("name", ["Priority Loading", "OBS"])
@pytest.mark.parametrize("count", [None, "0", "-1", "1.5", "invalid"])
def test_new_container_charges_block_without_valid_count_or_list(name, count):
    summary, settings = canary()
    for mapping in settings.charge_mappings:
        if mapping.clickup_field_name != name:
            summary["custom_fields"][mapping.clickup_field_name]["value"] = "0"
    summary["custom_fields"]["Number of Containers"]["value"] = count
    client = FakeBCInvoiceClient()
    result = apply_clickup_bc_sales_invoice(
        clickup_summary=summary, bc_client=client, settings=settings, today=date(2026, 10, 9)
    )
    assert result["status"] == "invalid_line_quantity"
    assert "Number of Containers is missing or invalid" in result["message"]
    assert client.created_headers == client.created_lines == []


def test_new_charges_share_freight_container_list_fallback():
    summary, settings = canary()
    summary["custom_fields"].pop("Number of Containers")
    summary["custom_fields"]["Container(s) number(s)/"] = {"value": "ONEU4568905, ONEU4520938"}
    result = preview(summary, settings)
    assert result["status"] == "dry_run_ready"
    by_item = {source["item_number"]: source for source in result["line_sources"]}
    assert by_item["INT000000026"]["quantity"] == 2
    assert by_item["INT000000035"]["quantity"] == 2
    assert by_item["INT000000035"]["unit_price"] == 3000
    assert by_item["INT000000034"]["quantity"] == 2
    assert by_item["INT000000034"]["unit_price"] == 990


def test_new_charges_preserve_freight_air_product_quantity_fallback():
    summary, settings = canary()
    summary["custom_fields"]["Product/"] = {
        "type": "drop_down", "value": 6,
        "type_config": {"options": [{"id": "product-air", "name": "AIR", "orderindex": 6}]},
    }
    summary["custom_fields"]["Agent's Reference"] = {"value": "AWB-READINESS-ONLY"}
    summary["custom_fields"].pop("Number of Containers")
    result = preview(summary, settings)
    assert result["status"] == "dry_run_ready"
    sources = {source["item_number"]: source for source in result["line_sources"]}
    for _, item, _, amount in EXPECTED.values():
        assert sources[item]["quantity"] == sources["INT000000026"]["quantity"] == 1
        assert sources[item]["unit_price"] == float(amount)
        assert sources[item]["quantity_basis"] == "shipment"


@pytest.mark.parametrize("name", ["Priority Loading", "OBS"])
@pytest.mark.parametrize("field_id", [None, "unapproved-same-name-field"])
def test_new_charges_refuse_name_only_matching_before_creation(name, field_id):
    summary, settings = canary()
    summary["custom_fields"][name]["id"] = field_id
    client = FakeBCInvoiceClient()
    result = apply_clickup_bc_sales_invoice(
        clickup_summary=summary, bc_client=client, settings=settings, today=date(2026, 10, 9)
    )
    assert result["status"] == "invalid_charge_data"
    assert "refusing name-only mapping" in result["message"]
    assert client.created_headers == client.created_lines == []


def test_mapping_loader_rejects_non_boolean_identity_gate(tmp_path):
    payload = json.loads(MAPPING_PATH.read_text())
    payload["mappings"][0]["require_field_id"] = "false"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="require_field_id must be a boolean"):
        load_invoice_charge_mappings(path)


@pytest.mark.parametrize("value", [None, "", "0", "0.00"])
def test_zero_or_unpopulated_new_fields_do_not_create_lines(value):
    summary, settings = canary()
    for name in ("Priority Loading", "OBS"):
        summary["custom_fields"][name]["value"] = value
    result = preview(summary, settings)
    assert result["status"] == "dry_run_ready"
    assert len(result["line_sources"]) == 5
    assert result["invoice_validation"]["expected_total"] == 24000.00


@pytest.mark.parametrize("name", ["Priority Loading", "OBS"])
def test_invalid_new_charge_fails_before_any_document_creation(name):
    summary, settings = canary()
    summary["custom_fields"][name]["value"] = "not-an-amount"
    client = FakeBCInvoiceClient()
    result = apply_clickup_bc_sales_invoice(
        clickup_summary=summary, bc_client=client, settings=settings, today=date(2026, 10, 9)
    )
    assert result["status"] == "invalid_charge_data"
    assert client.created_headers == client.created_lines == []


def test_existing_invoice_remains_a_duplicate_stop_with_both_new_lines():
    summary, settings = canary()

    class StampedInvoiceClient(FakeBCInvoiceClient):
        def get_posted_invoice_fel_description_by_number(self, number, *, market=None):
            assert number == "GTFVR0005109" and market == "GT"
            return {"number": number, "electronicDocumentStatus": "Stamp Received"}

    client = StampedInvoiceClient(existing_invoices=[{
        "id": "existing-int-id", "number": "GTFVR0005109", "status": "Open",
        "externalDocumentNumber": "PO-7788-INT",
    }])
    result = apply_clickup_bc_sales_invoice(
        clickup_summary=summary, bc_client=client, settings=settings, today=date(2026, 10, 9)
    )
    assert result["status"] == "duplicate_invoice"
    assert len(result["line_sources"]) == 7
    assert result["invoice_validation"]["expected_total"] == 31980.00
    assert client.created_headers == client.created_lines == []


def test_mapping_change_does_not_relax_customer_specific_int_split_hold():
    summary, settings = canary()
    # ALB's approved two-INT rule cannot silently absorb additional INT charges.
    settings = replace(settings, int_split_customer_numbers=("C00067",))
    result = preview(deepcopy(summary), settings)
    assert result["status"] == "customer_int_split_validation_failed"


def test_issuance_cannot_reuse_old_incomplete_invoices():
    summary, settings = canary()

    class OldInvoiceClient(FakeBCInvoiceClient):
        def get_posted_invoice_fel_description_by_number(self, number, *, market=None):
            return {"number": number, "electronicDocumentStatus": "Stamp Received"}

    client = OldInvoiceClient(existing_invoices=[{
        "id": "old-int", "number": "GTFVR0005109", "status": "Open",
        "externalDocumentNumber": "PO-7788-INT", "totalAmountIncludingTax": 19200,
    }])
    result = issue_clickup_bc_sales_invoice(
        clickup_summary=summary, bc_client=client, settings=settings, today=date(2026, 10, 9)
    )
    assert result["status"] == "duplicate_invoice_requires_review"
    assert result["failed_stage"] == "verify_existing_invoice_lines"
    assert result["completed_stages"] == []
    assert client.created_headers == client.created_lines == []


@pytest.mark.parametrize("problem", [None, "missing_item", "amount", "tax", "discount", "read_failure", "quantity", "duplicate_line"])
def test_guarded_retry_verifies_lines_and_allows_matching_recovery(problem):
    summary, settings = canary()
    result = preview(summary, settings)
    invoice = {"id": "full-int", "number": "GTFVR-FULL", "invoice_group": "INT",
               "totalAmountIncludingTax": 27180}
    lines = [{"lineObjectNumber": item, "quantity": 6, "unitPrice": float(amount) / 6, "taxPercent": 0}
             for _, item, _, amount in EXPECTED.values()]
    if problem == "missing_item":
        lines.pop()
    elif problem == "amount":
        lines[0]["unitPrice"] = 5999
    elif problem == "tax":
        lines[0]["taxPercent"] = 12
    elif problem == "discount":
        lines[0]["discountPercent"] = 1
    elif problem == "quantity":
        lines[0]["quantity"] = 1
        lines[0]["unitPrice"] *= 6
    elif problem == "duplicate_line":
        lines.append(dict(lines[0]))

    class LinesClient:
        def get_posted_sales_invoice_lines(self, invoice_id, *, market=None):
            assert invoice_id == "full-int" and market == "GT"
            if problem == "read_failure":
                raise ValueError("Readback unavailable")
            return lines

    validation = _validate_guarded_duplicate_retry(
        result=result, invoices=[invoice], config=settings, bc_client=LinesClient()
    )
    assert validation["status"] == ("passed" if problem is None else "failed")


@pytest.mark.parametrize("problem", [None, "stale_total", "mixed_tax", "missing_proposal"])
def test_guarded_all_group_cannot_bypass_review(problem):
    summary, settings = canary()
    settings = replace(settings, split_invoice_by_item_prefix=False)
    if problem != "mixed_tax":
        for mapping in settings.charge_mappings:
            if mapping.bc_item_number.startswith("NAT"):
                summary["custom_fields"][mapping.clickup_field_name]["value"] = "0"
    result = preview(summary, settings)
    assert result["invoice_groups"] == ["ALL"]
    invoice = {"id": "all-invoice", "number": "GTFVR-ALL", "invoice_group": "ALL",
               "totalAmountIncludingTax": result["proposed_bc_invoices"][0]["total"]}
    if problem == "stale_total":
        invoice["totalAmountIncludingTax"] = 1
    elif problem == "missing_proposal":
        result["proposed_bc_invoices"] = []

    class LinesClient:
        def get_posted_sales_invoice_lines(self, invoice_id, *, market=None):
            assert problem is None, "Invalid recovery must stop before further line reads"
            return [{"lineObjectNumber": item, "quantity": 6, "unitPrice": float(amount) / 6, "taxPercent": 0}
                    for _, item, _, amount in EXPECTED.values()]

    validation = _validate_guarded_duplicate_retry(
        result=result, invoices=[invoice], config=settings, bc_client=LinesClient()
    )
    assert validation["status"] == ("passed" if problem is None else "failed")
