import copy

import pytest

from clickup_integration.tagomago_boundary import generic_invoice_tagomago_blocker


def ocean_result():
    return {
        "market": "MX", "currency": "USD", "customer_number": "C00067",
        "source_list_id": "901703461634",
        "shipment_metadata": {"product": "OCEAN/FCL"},
        "proposed_bc_invoices": [{"proposed_bc_payload": {
            "customerNumber": "C00067", "currencyCode": "USD",
        }}],
    }


def source():
    return {
        "market": "MX", "list": {"id": "901703461634"},
        "custom_fields": {
            "Business Central Customer Number": {"value": "C00067"},
            "Product/": {"value": 0, "type_config": {
                "options": [{"orderindex": 0, "name": "OCEAN/FCL"}],
            }},
        },
    }


def test_all_ocean_lifecycle_boundaries_accept_verified_usd_source():
    for summary, result in [(source(), None), (source(), ocean_result()), (None, ocean_result())]:
        assert generic_invoice_tagomago_blocker(
            clickup_summary=summary, invoice_result=result, market="MX",
        ) is None


@pytest.mark.parametrize("key,value", [
    ("source_list_id", "901704095060"), ("source_list_id", ""),
    ("currency", "MXN"), ("market", "GT"),
    ("shipment_metadata", {"product": "WAREHOUSE"}),
])
def test_ocean_exception_rejects_wrong_or_missing_provenance(key, value):
    result = ocean_result()
    result[key] = value
    assert generic_invoice_tagomago_blocker(invoice_result=result, market="MX")


@pytest.mark.parametrize("field,value", [
    ("currencyCode", "MXN"), ("billToCustomerNumber", "C00999"),
    ("lineObjectNumber", "NAT00000012"),
])
def test_duplicate_and_delivery_records_cannot_conflict_with_ocean_preview(field, value):
    result = ocean_result()
    result["existing_invoice"] = {field: value, "customerNumber": "C00067"}
    assert generic_invoice_tagomago_blocker(invoice_result=result, market="MX")


@pytest.mark.parametrize("list_id", ["901704342223", "901704095060"])
def test_warehouse_and_distribution_stay_blocked_even_with_ocean_result(list_id):
    summary = source()
    summary["list"]["id"] = list_id
    assert generic_invoice_tagomago_blocker(
        clickup_summary=summary, invoice_result=ocean_result(), market="MX",
    )["matched_by"] == "restricted_tagomago_source"


def test_source_currency_conflict_stays_blocked():
    summary = source()
    summary["custom_fields"]["Currency"] = {"value": "MXN"}
    assert generic_invoice_tagomago_blocker(
        clickup_summary=summary, invoice_result=ocean_result(), market="MX",
    )


def test_unknown_tagomago_source_stays_blocked():
    result = copy.deepcopy(ocean_result())
    result.pop("source_list_id")
    assert generic_invoice_tagomago_blocker(invoice_result=result, market="MX")


def test_restricted_source_blocks_creation_and_posting_before_bc_calls():
    from clickup_integration.invoice_sync import apply_clickup_bc_sales_invoice, issue_clickup_bc_sales_invoice
    from test_clickup_invoice_sync import make_mx_settings
    summary = source()
    summary["list"]["id"] = "901704342223"
    for operation in (apply_clickup_bc_sales_invoice, issue_clickup_bc_sales_invoice):
        result = operation(clickup_summary=summary, settings=make_mx_settings(), bc_client=object())
        assert result["status"] == "blocked"
        assert result["created_invoices"] == []


def test_restricted_source_blocks_delivery_before_upload_or_comment():
    from clickup_integration.invoice_delivery import finalize_clickup_issued_invoices
    from test_clickup_invoice_sync import make_mx_settings
    summary = source()
    summary["list"]["id"] = "901704095060"
    with pytest.raises(ValueError, match="dedicated approval-ledger lifecycle"):
        finalize_clickup_issued_invoices(clickup_summary=summary, invoice_result=ocean_result(),
            clickup=object(), bc_client=object(), settings=make_mx_settings(), workspace_id="8451352")
