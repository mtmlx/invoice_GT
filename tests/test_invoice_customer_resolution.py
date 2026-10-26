from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from clickup_integration import invoice_customer_resolution as crm_resolution
from clickup_integration.invoice_sync import _resolve_invoice_customer, prepare_clickup_bc_sales_invoice_preview
from test_clickup_invoice_sync import FakeBCInvoiceClient, make_clickup_summary, make_settings


BC_ID = "c12adf07-6bea-f011-8542-002248a99c7e"


def dropdown(name):
    return {"type": "drop_down", "value": 1, "type_config": {"options": [{"name": name, "orderindex": 1}]}}


@pytest.fixture
def setup(monkeypatch):
    crm = {
        "id": "crm-1", "name": "SOLAR GUATE", "status": {"status": "current customer"},
        "custom_fields": [
            {"name": "Clientes/", **dropdown("SolarGuat")},
            {"name": "Owner Country/", **dropdown("Guatemala")},
            {"name": "Business Central Customer ID", "value": BC_ID},
            {"name": "Business Central Customer Number", "value": "C00086"},
            {"name": "Customer Tax ID", "value": "7925914-6"},
        ],
    }
    card = {"id": BC_ID, "number": "C00086", "displayName": "ENERGIA SOLAR DE GUATEMALA",
            "taxRegistrationNumber": "79259146"}
    calls = []

    def get_task(task_id):
        calls.append(("crm", task_id))
        return deepcopy(crm)

    def get_card(customer_id, *, market):
        calls.append(("bc", customer_id, market))
        return deepcopy(card)

    monkeypatch.setattr(crm_resolution, "ClickUpClient", lambda settings: SimpleNamespace(get_task=get_task))
    fields = {"Invoice to (Consignee's name)": dropdown("SolarGuat"),
              "Consignee’s relationship to CRM": {"id": crm_resolution.CONSIGNEE_CRM_FIELD_ID,
                                                   "value": [{"id": "crm-1"}]}}
    client = SimpleNamespace(get_customer_by_id=get_card, resolve_customer_by_name=lambda *a, **k: None)
    settings = replace(make_settings(), bc_customer_name_field_names=("Invoice to (Consignee's name)",))
    return SimpleNamespace(crm=crm, card=card, calls=calls, fields=fields, client=client, settings=settings)


def resolve(s, **kwargs):
    return _resolve_invoice_customer(custom_fields=s.fields, bc_client=s.client, market=kwargs.pop("market", "GT"),
                                     config=s.settings, customer_id=kwargs.pop("customer_id", ""),
                                     customer_number=kwargs.pop("customer_number", ""))


def test_linked_crm_resolves_bc_id_and_formatted_nit(setup):
    result = resolve(setup)
    assert result["status"] == "resolved"
    assert result["source"] == "linked_clickup_crm"
    assert result["customer_id"] == BC_ID
    assert result["customer_number"] == "C00086"
    assert result["crm_task_id"] == "crm-1"
    assert setup.calls == [("crm", "crm-1"), ("bc", BC_ID, "GT")]


@pytest.mark.parametrize("explicit", [{"customer_id": "explicit-id"}, {"customer_number": "C00056"}])
def test_explicit_shipment_fields_keep_priority(setup, explicit):
    assert resolve(setup, **explicit)["status"] == "explicit"
    assert setup.calls == []


def test_mexico_keeps_existing_matching(setup):
    assert resolve(setup, market="MX")["status"] == "not_found"
    assert setup.calls == []


def test_no_relationship_preserves_name_fallback(setup):
    setup.fields.pop("Consignee’s relationship to CRM")
    setup.client.resolve_customer_by_name = lambda *a, **k: setup.card
    assert resolve(setup)["source"] == "clickup_customer_name"
    assert setup.calls == []


def test_third_party_bill_to_does_not_use_consignee_id(setup):
    setup.fields["Invoice to (Consignee's name)"] = dropdown("Third party")
    third_party = {"id": "third-party-id", "number": "C00056"}
    setup.client.resolve_customer_by_name = lambda *a, **k: third_party
    result = resolve(setup)
    assert result["customer_id"] == "third-party-id"
    assert setup.calls == [("crm", "crm-1")]


def test_missing_crm_bc_id_preserves_name_fallback(setup):
    setup.crm["custom_fields"][2]["value"] = ""
    setup.client.resolve_customer_by_name = lambda *a, **k: setup.card
    assert resolve(setup)["source"] == "clickup_customer_name"


@pytest.mark.parametrize("case", ["multiple_links", "bad_link", "bad_uuid", "wrong_market", "wrong_number",
                                 "wrong_nit", "missing_bc_nit", "wrong_bc_id", "missing_bc", "clickup_unavailable",
                                 "bc_unavailable", "conflicting_crm_tax_ids"])
def test_invalid_linked_identity_blocks_instead_of_guessing(setup, monkeypatch, case):
    if case == "multiple_links":
        setup.fields["Consignee’s relationship to CRM"]["value"].append({"id": "crm-2"})
    elif case == "bad_link":
        setup.fields["Consignee’s relationship to CRM"]["value"] = ["crm-1"]
    elif case == "bad_uuid":
        setup.crm["custom_fields"][2]["value"] = "invalid-id"
    elif case == "wrong_market":
        setup.crm["custom_fields"][1] = {"name": "Owner Country/", **dropdown("Mexico")}
    elif case == "wrong_number":
        setup.card["number"] = "C00056"
    elif case == "wrong_nit":
        setup.card["taxRegistrationNumber"] = "11111111"
    elif case == "missing_bc_nit":
        setup.card.pop("taxRegistrationNumber")
    elif case == "wrong_bc_id":
        setup.card["id"] = "another-id"
    elif case == "missing_bc":
        setup.card.clear()
    elif case == "conflicting_crm_tax_ids":
        setup.crm["custom_fields"].append({"name": "Tax ID", "value": "11111111"})
    elif case == "clickup_unavailable":
        monkeypatch.setattr(crm_resolution, "ClickUpClient", lambda settings: (_ for _ in ()).throw(RuntimeError("offline")))
    elif case == "bc_unavailable":
        setup.client.get_customer_by_id = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline"))
    setup.client.resolve_customer_by_name = lambda *a, **k: pytest.fail("Invalid CRM identity must not fall back")
    assert resolve(setup)["status"] == "invalid_linked_crm_customer"


def test_preview_uses_linked_id_and_keeps_payment_terms_without_mutation(setup):
    summary = make_clickup_summary(status="Listo para facturar")
    summary["custom_fields"].pop("Business Central Customer ID")
    summary["custom_fields"].pop("Business Central Customer Number")
    summary["custom_fields"].update(setup.fields)
    bc = FakeBCInvoiceClient()
    original = bc.get_customer_by_id

    def get_card(customer_id, *, market):
        return {**original(customer_id, market=market), "taxRegistrationNumber": "79259146"}

    # Keep the existing invoice test's C00067 fixture while exercising the CRM path.
    setup.crm["custom_fields"][3]["value"] = "C00067"
    bc.get_customer_by_id = get_card
    before = deepcopy(summary)
    result = prepare_clickup_bc_sales_invoice_preview(clickup_summary=summary, bc_client=bc, settings=setup.settings)
    assert result["status"] == "dry_run_ready"
    assert result["customer_id"] == BC_ID
    assert result["proposed_bc_payload"]["paymentTermsId"] == "term-30-days"
    assert result["customer_resolution"]["source"] == "linked_clickup_crm"
    assert not bc.created_headers and not bc.created_lines
    assert summary == before


def test_preview_stops_invalid_crm_before_invoice_creation(setup):
    summary = make_clickup_summary(status="Listo para facturar")
    summary["custom_fields"].pop("Business Central Customer ID")
    summary["custom_fields"].pop("Business Central Customer Number")
    summary["custom_fields"].update(setup.fields)
    setup.crm["custom_fields"][2]["value"] = "not-a-uuid"
    bc = FakeBCInvoiceClient()
    result = prepare_clickup_bc_sales_invoice_preview(clickup_summary=summary, bc_client=bc, settings=setup.settings)
    assert result["status"] == "customer_resolution_failed"
    assert not bc.created_headers and not bc.created_lines
