from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from clickup_integration import mx_ocean_policy as policy
from clickup_integration.invoice_sync import (
    load_invoice_charge_mappings, prepare_clickup_bc_sales_invoice_preview,
    apply_clickup_bc_sales_invoice, issue_clickup_bc_sales_invoice,
)
from test_clickup_invoice_sync import FakeMXBCInvoiceClient, make_mx_settings, make_mx_clickup_summary

AMOUNTS = [4115.85, 1876.83, 3332, 941.46, 400, 0, 0, 0, 0, 207, 250, 100, 121.95, 250, 3135, 0, 0, 0]


def fixture():
    mappings = load_invoice_charge_mappings(Path(__file__).resolve().parents[1] / "config/invoice_charge_mappings/mx.json")
    settings = replace(make_mx_settings(), charge_mappings=mappings, split_invoice_by_item_prefix=False)
    summary = make_mx_clickup_summary(status="Listo para facturar")
    summary["custom_fields"]["Product/"] = {"value": "OCEAN/FCL"}
    summary["custom_fields"]["ETA/"] = {"id": policy.ETA_FIELD_ID, "value": "1791108000000"}
    for mapping, amount in zip(mappings, AMOUNTS):
        summary["custom_fields"][mapping.clickup_field_name] = {"id": mapping.clickup_field_id, "value": str(amount)}
    return summary, settings


class OceanBC(FakeMXBCInvoiceClient):
    def __init__(self):
        super().__init__(payment_terms_code="30 DÍAS")
        self.events = []
        self.change_before_post = False
        self.reads_after_patch = 0
        self.duplicate = False

    def get_entity(self, entity_name, entity_id, **kwargs):
        if entity_name == "paymentTerms":
            return {"id": entity_id, "code": "30 DÍAS", "dueDateCalculation": "30D", "discountPercent": 0}
        invoice = deepcopy(super().get_entity(entity_name, entity_id, **kwargs))
        lines = self.get_posted_sales_invoice_lines(entity_id, **kwargs)
        breakdown = policy.vat_breakdown(lines)
        invoice.update({"@odata.etag": 'W/"revision-1"',
                        "totalAmountExcludingTax": sum(x["subtotal"] for x in breakdown.values()),
                        "totalTaxAmount": sum(x["vat"] for x in breakdown.values())})
        invoice["totalAmountIncludingTax"] = invoice["totalAmountExcludingTax"] + invoice["totalTaxAmount"]
        if "patch" in self.events:
            self.reads_after_patch += 1
            if self.change_before_post and self.reads_after_patch == 2:
                invoice["dueDate"] = "2026-10-29"
        return invoice

    def set_mx_payment_fields(self, invoice_id, **kwargs):
        result = super().set_mx_payment_fields(invoice_id, **kwargs)
        self.events.append("terms")
        self.draft_invoices[invoice_id]["dueDate"] = "2026-10-29"
        return result

    def patch_entity(self, entity, entity_id, payload, **kwargs):
        assert self.events == ["terms"]
        assert len([line for line in self.created_lines if line["lineType"] == "Item"]) == 9
        assert kwargs["if_match"] == 'W/"revision-1"'
        self.events.append("patch")
        self.draft_invoices[entity_id].update(payload)
        return self.draft_invoices[entity_id]

    def get_posted_sales_invoice_lines(self, invoice_id, **kwargs):
        return [{**line, "amountExcludingTax": float(policy.money(Decimal(str(line["quantity"])) * Decimal(str(line["unitPrice"])))),
                 "taxPercent": 16 if line["lineObjectNumber"].startswith("NAT") else 0}
                for line in self.created_lines if line["lineType"] == "Item"]

    def find_entities(self, entity_name, **kwargs):
        if entity_name == "salesInvoices" and self.duplicate:
            return [{"id": "original", "number": "B0003376", "externalDocumentNumber": "UW-26-ES-001"}]
        return super().find_entities(entity_name, **kwargs)


def preview(summary=None, settings=None, bc=None):
    default_summary, default_settings = fixture()
    return prepare_clickup_bc_sales_invoice_preview(clickup_summary=summary or default_summary,
        settings=settings or default_settings, bc_client=bc or OceanBC(), today=date(2026, 9, 29))


def test_exact_grouped_charges_tax_and_due_date():
    result = preview()
    assert result["status"] == "dry_run_ready", result
    lines = result["proposed_bc_line_payloads"]
    assert len(lines) == 9
    assert [line["unitPrice"] for line in lines[:2]] == [4515.85, 2818.29]
    assert result["invoice_validation"]["expected_total"] == 14730.09
    assert len(result["line_sources"][0]["source_components"]) == 2
    assert len(result["line_sources"][1]["source_components"]) == 2
    assert result["mx_ocean_policy"]["vat_breakdown"] == {
        "INT": {"vat_rate": 0, "subtotal": 10873.14, "vat": 0.0},
        "NAT": {"vat_rate": 16, "subtotal": 3856.95, "vat": 617.11}}
    header = result["proposed_bc_payload"]
    assert result["eta_date"] == "2026-10-04"
    assert header["dueDate"] == "2026-11-03"
    assert header["invoiceDate"] == header["postingDate"] == "2026-09-29"
    assert header["paymentTermsId"] == "mx-term-ppd"


@pytest.mark.parametrize("amounts,subtotal,vat,total", [
    ([2823.53, 335.29, 0, 0, 0, 0, 0, 76.47, 0, 185, 0, 0, 205.88, 765, 3941.18, 0, 0, 0], 8332.35, 785.93, 9118.28),
    (AMOUNTS, 14730.09, 617.11, 15347.20),
    ([3881, 2331, 2583, 888, 0, 0, 0, 0, 0, 293, 219, 58, 115, 201, 3105, 0, 0, 0], 13674, 591.68, 14265.68),
    ([3708.75, 1731.90, 5357.85, 577.30, 0, 0, 0, 32.20, 0, 171.35, 247.25, 172.50, 115, 0, 3105, 0, 0, 0], 15219.10, 582.36, 15801.46),
], ids=["CIMX30100066", "UW-26-ES-002", "UW-26-FR-002", "UW-26-ES-003"])
def test_four_shipments_destination_customs_vat(amounts, subtotal, vat, total):
    summary, settings = fixture()
    for mapping, amount in zip(settings.charge_mappings, amounts):
        summary["custom_fields"][mapping.clickup_field_name]["value"] = str(amount)
    result = preview(summary, settings)
    assert result["status"] == "dry_run_ready", result
    breakdown = result["mx_ocean_policy"]["vat_breakdown"]
    assert result["invoice_validation"]["expected_total"] == subtotal
    assert sum(policy.money(group["vat"]) for group in breakdown.values()) == policy.money(vat)
    assert policy.money(subtotal) + policy.money(vat) == policy.money(total)
    destination = [line for line in result["proposed_bc_line_payloads"]
                   if line["lineObjectNumber"] == "NAT00000030"]
    assert len(destination) == (1 if amounts[13] else 0)
    assert not any(line["lineObjectNumber"] == "INT000000016"
                   for line in result["proposed_bc_line_payloads"])


def test_destination_customs_zero_vat_readback_blocks_posting():
    class WrongCustomsVAT(OceanBC):
        def get_posted_sales_invoice_lines(self, invoice_id, **kwargs):
            lines = super().get_posted_sales_invoice_lines(invoice_id, **kwargs)
            for line in lines:
                if line["lineObjectNumber"] == "NAT00000030":
                    line["taxPercent"] = 0
            return lines
    summary, settings = fixture()
    bc = WrongCustomsVAT()
    result = issue_clickup_bc_sales_invoice(clickup_summary=summary, settings=settings, bc_client=bc)
    assert result["failed_stage"] == "verify_mx_ocean_draft"
    assert not bc.posted_invoices and not bc.mx_stamp_calls


def test_shared_item_does_not_merge_other_fees():
    summary, settings = fixture()
    summary["custom_fields"]["Food Grade Container"]["value"] = "99"
    summary["custom_fields"]["Overweight"]["value"] = "12"
    result = preview(summary, settings)
    assert len(result["proposed_bc_line_payloads"]) == 11
    assert result["line_sources"][0]["amount"] == 4515.85


def test_addition_without_base_amount_uses_base_item():
    summary, settings = fixture()
    summary["custom_fields"].pop("Origin Charges")
    result = preview(summary, settings)
    origin = next(line for line in result["line_sources"] if line["item_number"] == "INT000000011")
    assert origin["amount"] == 941.46
    assert len(origin["source_components"]) == 1


@pytest.mark.parametrize("raw", [None, "", "garbage"])
def test_missing_invalid_eta_cannot_use_task_due_date(raw):
    summary, settings = fixture()
    summary["custom_fields"]["ETA/"]["value"] = raw
    assert preview(summary, settings)["status"] == "mx_ocean_policy_failed"


@pytest.mark.parametrize("formula", ["", "1M", "CM+30D", "-30D", "<30D", "30D>"])
def test_unsupported_terms_fail_closed(formula):
    summary, _ = fixture()
    with pytest.raises(ValueError):
        policy.credit_policy(summary["custom_fields"], {"id": "term", "dueDateCalculation": formula}, "term")


@pytest.mark.parametrize("eta,days,expected", [("2026-12-20", "30D", "2027-01-19"), ("2028-02-01", "<30D>", "2028-03-02"), ("2026-10-04", "0D", "2026-10-04")])
def test_calendar_terms(eta, days, expected):
    result = policy.credit_policy({"eta": {"id": policy.ETA_FIELD_ID, "value": eta}},
                                  {"id": "term", "dueDateCalculation": days}, "term")
    assert result["due_date"] == expected


@pytest.mark.parametrize("market,currency,product", [("GT", "USD", "OCEAN/FCL"), ("MX", "MXN", "OCEAN/FCL"), ("MX", "USD", "AIR"), ("MX", "USD", "WAREHOUSE")])
def test_policy_scope(market, currency, product):
    assert not policy.applies(market=market, currency=currency, product={"name": product})


def test_apply_overrides_recalculated_due_date_after_terms_and_lines():
    summary, settings = fixture()
    bc = OceanBC()
    result = apply_clickup_bc_sales_invoice(clickup_summary=summary, settings=settings, bc_client=bc, today=date(2026, 9, 29))
    assert result["status"] == "applied", result
    assert bc.events == ["terms", "patch"]
    assert result["created_invoices"][0]["dueDate"] == "2026-11-03"
    assert not bc.posted_invoices


def test_date_changed_after_apply_stops_before_post():
    summary, settings = fixture()
    bc = OceanBC()
    bc.change_before_post = True
    result = issue_clickup_bc_sales_invoice(clickup_summary=summary, settings=settings, bc_client=bc, today=date(2026, 9, 29))
    assert result["failed_stage"] == "verify_mx_ocean_before_post"
    assert not bc.posted_invoices and not bc.mx_stamp_calls


def test_original_duplicate_is_never_reused_or_reissued():
    summary, settings = fixture()
    bc = OceanBC()
    bc.duplicate = True
    result = issue_clickup_bc_sales_invoice(clickup_summary=summary, settings=settings, bc_client=bc, today=date(2026, 9, 29))
    assert result["status"] == "duplicate_invoice", result
    assert not bc.created_headers and not bc.posted_invoices and not bc.mx_stamp_calls and not bc.sync_fel_calls


@pytest.mark.parametrize("drift", ["vat", "amount", "description", "dueDate", "paymentTermsId", "totalTaxAmount"])
def test_readback_rejects_drift(drift):
    result = preview()
    header = result["proposed_bc_payload"]
    expected = result["proposed_bc_line_payloads"]
    bc = OceanBC()
    bc.created_lines = expected
    actual = bc.get_posted_sales_invoice_lines("id")
    invoice = {**header, "totalAmountExcludingTax":14730.09, "totalTaxAmount":617.11, "totalAmountIncludingTax":15347.20}
    if drift == "vat": actual[0]["taxPercent"] = 16
    elif drift == "amount": actual[0]["amountExcludingTax"] = 1
    elif drift == "description": actual[0]["description"] = "wrong charge"
    else: invoice[drift] = "0"
    with pytest.raises(ValueError):
        policy.verify_invoice(invoice, actual, header, expected)


def test_terms_code_conflict_blocks_before_creating():
    summary, settings = fixture()
    bc = OceanBC()
    bc.payment_terms_code = "60 DÍAS"
    result = apply_clickup_bc_sales_invoice(clickup_summary=summary, settings=settings, bc_client=bc)
    assert result["status"] == "mx_ocean_policy_failed"
    assert not bc.created_headers


def test_posted_date_drift_stops_before_stamp():
    class DriftBC(OceanBC):
        def post_sales_invoice(self, invoice_id, **kwargs):
            response = super().post_sales_invoice(invoice_id, **kwargs)
            self.posted_invoices[invoice_id]["dueDate"] = "2026-10-29"
            return response
    summary, settings = fixture()
    bc = DriftBC()
    result = issue_clickup_bc_sales_invoice(clickup_summary=summary, settings=settings, bc_client=bc)
    assert result["failed_stage"] == "verify_mx_ocean_after_post"
    assert bc.posted_invoices and not bc.mx_stamp_calls and not bc.sync_fel_calls


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity"])
def test_invalid_charge_blocks_before_creating(amount):
    summary, settings = fixture()
    summary["custom_fields"]["Emergency Surcharge"]["value"] = amount
    bc = OceanBC()
    result = apply_clickup_bc_sales_invoice(clickup_summary=summary, settings=settings, bc_client=bc)
    assert result["status"] == "mx_ocean_policy_failed"
    assert not bc.created_headers


def test_valid_policy_reaches_stamp_only_after_verified_post():
    summary, settings = fixture()
    bc = OceanBC()
    result = issue_clickup_bc_sales_invoice(clickup_summary=summary, settings=settings, bc_client=bc)
    assert result["status"] == "applied", result
    assert len(bc.mx_stamp_calls) == 1
    assert result["posted_invoices"][0]["dueDate"] == "2026-11-03"


def test_unsupported_item_prefix_returns_blocked_preview():
    summary, settings = fixture()
    settings = replace(settings, charge_mappings=tuple(
        replace(m, bc_item_number="OTHER001") if m.charge_name == "Cargo Maritime Insurance" else m
        for m in settings.charge_mappings))
    result = preview(summary, settings)
    assert result["status"] == "mx_ocean_policy_failed"
