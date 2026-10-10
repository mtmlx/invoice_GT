"""No-API regressions for guards that must run before any cancellation."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import replace_gt_invoices_once as full
from scripts import replace_gt_invoice_split_int_charges_once as split


def source_preview(*, extra_charges=(), status="duplicate_invoice"):
    charges = [
        ("Freight (Ocean/Truck/Air)", 16800.0),
        ("Emergency Surcharge", 2400.0),
        *extra_charges,
    ]
    lines = [
        {"lineType": "Item", "quantity": 1, "unitPrice": amount}
        for _, amount in charges
    ]
    sources = [
        {"charge_name": name, "amount": amount, "invoice_group": "INT"}
        for name, amount in charges
    ]
    return {
        "status": status,
        "reference": "MTMLXGT-CANARY",
        "invoice_validation": {"status": "passed", "errors": []},
        "proposed_bc_invoices": [{
            "invoice_group": "INT",
            "proposed_bc_payload": {
                "externalDocumentNumber": "MTMLXGT-CANARY-INT",
                "currencyCode": "USD", "customerNumber": "C00118",
            },
            "proposed_bc_line_payloads": lines,
            "line_sources": sources,
            "total": sum(amount for _, amount in charges),
        }],
    }


def invalid_preview(kind):
    preview = source_preview()
    if kind == "invalid_charge_data":
        preview["status"] = kind
    elif kind == "failed_validation":
        preview["invoice_validation"]["status"] = "failed"
    elif kind == "missing_validation":
        preview.pop("invoice_validation")
    elif kind == "validation_errors":
        preview["invoice_validation"]["errors"] = ["unreconciled source amount"]
    elif kind == "empty_proposals":
        preview["proposed_bc_invoices"] = []
    elif kind == "empty_lines":
        preview["proposed_bc_invoices"][0]["proposed_bc_line_payloads"] = []
    elif kind == "comment_only":
        preview["proposed_bc_invoices"][0]["proposed_bc_line_payloads"] = [
            {"lineType": "Comment", "description": "Shipment metadata only"}
        ]
    elif kind == "missing_header":
        preview["proposed_bc_invoices"][0]["proposed_bc_payload"] = {}
    else:
        raise AssertionError(kind)
    return preview


def prepare_main(monkeypatch, module, preview):
    settings = SimpleNamespace(supported_market="GT")
    fake_clickup = SimpleNamespace(get_task=lambda *args, **kwargs: {})
    fake_bc = SimpleNamespace(
        find_entities=lambda *args, **kwargs: [],
        get_posted_sales_invoice_by_number=lambda *args, **kwargs: {
            "externalDocumentNumber": "MTMLXGT-CANARY-INT",
        }
    )
    monkeypatch.setattr(module.ClickUpSettings, "from_env", lambda: object())
    monkeypatch.setattr(module.BusinessCentralSettings, "from_env", lambda: object())
    monkeypatch.setattr(module.InvoiceAutomationSettings, "from_env", lambda: settings)
    monkeypatch.setattr(module, "ClickUpClient", lambda settings: fake_clickup)
    monkeypatch.setattr(module, "BusinessCentralClient", lambda settings: fake_bc)
    monkeypatch.setattr(module, "summarize_task_for_customer_mapping", lambda task: {})
    monkeypatch.setattr(
        module, "prepare_clickup_bc_sales_invoice_preview", lambda **kwargs: deepcopy(preview)
    )
    if module is full:
        monkeypatch.setattr(module, "_force_ready_invoice_status", lambda summary, settings: summary)
    else:
        monkeypatch.setattr(module, "_force_supported_market_for_one_off", lambda summary, settings: summary)
        monkeypatch.setattr(module, "_has_invoice_status_field", lambda summary, settings: False)
        monkeypatch.setattr(module, "_force_ready_invoice_status_for_one_off", lambda summary, settings: summary)
    cancellations = []

    def cancel(**kwargs):
        cancellations.append(kwargs)
        pytest.fail("An invalid preflight reached cancellation")

    monkeypatch.setattr(module, "cancel_invoice_if_needed", cancel)
    monkeypatch.setattr(module.sys, "argv", [
        "controlled-replacement", "--task-id", "MTMLXGT-CANARY",
        "--old-invoice", "GTFVRTEST",
    ])
    return cancellations


@pytest.mark.parametrize("module", [full, split])
@pytest.mark.parametrize("kind", [
    "invalid_charge_data", "failed_validation", "missing_validation", "validation_errors",
    "empty_proposals", "empty_lines", "comment_only", "missing_header",
])
def test_invalid_main_preflight_never_cancels(monkeypatch, module, kind):
    cancellations = prepare_main(monkeypatch, module, invalid_preview(kind))
    with pytest.raises(ValueError, match="blocked before cancellation"):
        module.main()
    assert cancellations == []


@pytest.mark.parametrize("status", ["dry_run_ready", "duplicate_invoice"])
def test_valid_full_preflight_and_approved_split_remain_ready(status):
    preview = source_preview(status=status)
    full._require_valid_replacement_preflight(preview, invoice_group="INT")
    result = split._build_split_int_preview(preview)
    assert result["status"] == "dry_run_ready"
    assert result["invoice_validation"]["status"] == "passed"
    assert result["invoice_validation"]["expected_total"] == 19200.0
    assert [proposal["total"] for proposal in result["proposed_bc_invoices"]] == [16800.0, 2400.0]
    assert {source["charge_name"] for source in result["line_sources"]} == {
        "Freight (Ocean/Truck/Air)", "Emergency Surcharge",
    }


@pytest.mark.parametrize("extra", [
    (("Priority Loading", 6000.0),),
    (("OBS", 1980.0),),
    (("Priority Loading", 6000.0), ("OBS", 1980.0)),
])
def test_uncovered_split_charges_are_not_dropped(extra):
    result = split._build_split_int_preview(source_preview(extra_charges=extra))
    assert result["status"] == "unexpected_split_charges"
    assert result["unexpected_int_charges"] == sorted(name for name, _ in extra)
    assert "refusing to omit" in result["message"]
    # The rejected result retains the complete source proposal for review.
    assert result["proposed_bc_invoices"][0]["total"] == 19200.0 + sum(amount for _, amount in extra)


@pytest.mark.parametrize("extra", [
    (("Priority Loading", 6000.0),), (("OBS", 1980.0),),
])
def test_split_main_blocks_uncovered_charges_before_cancellation(monkeypatch, extra):
    cancellations = prepare_main(monkeypatch, split, source_preview(extra_charges=extra))
    with pytest.raises(ValueError, match="unexpected_split_charges"):
        split.main()
    assert cancellations == []


def test_split_main_blocks_missing_required_charge_before_cancellation(monkeypatch):
    preview = source_preview()
    invoice = preview["proposed_bc_invoices"][0]
    invoice["proposed_bc_line_payloads"].pop()
    invoice["line_sources"].pop()
    cancellations = prepare_main(monkeypatch, split, preview)
    with pytest.raises(ValueError, match="missing_split_charge"):
        split.main()
    assert cancellations == []


def test_full_preflight_blocks_requested_group_not_in_proposals():
    with pytest.raises(ValueError, match="group NAT is missing"):
        full._require_valid_replacement_preflight(source_preview(), invoice_group="NAT")


@pytest.mark.parametrize("module", [full, split])
@pytest.mark.parametrize("conflict", ["total", "currency", "customerNumber"])
def test_replacement_reference_conflict_blocks_main_before_cancellation(monkeypatch, module, conflict):
    cancellations = prepare_main(monkeypatch, module, source_preview())
    row = {
        "number": "GTFVR-OTHER", "status": "Open", "currencyCode": "USD",
        "customerNumber": "C00118", "externalDocumentNumber": "MTMLXGT-CANARY-INT",
        "totalAmountIncludingTax": 19200.0 if module is full else 2400.0,
    }
    if module is split:
        row["externalDocumentNumber"] += "-2"
    if conflict == "total":
        row["totalAmountIncludingTax"] = 999.0
    elif conflict == "currency":
        row["currencyCode"] = "GTQ"
    else:
        row["customerNumber"] = "WRONG-CUSTOMER"
    fake_bc = SimpleNamespace(
        get_posted_sales_invoice_by_number=lambda *args, **kwargs: {"externalDocumentNumber": "MTMLXGT-CANARY-INT"},
        find_entities=lambda *args, **kwargs: [row] if row["externalDocumentNumber"] in kwargs["filters"] else [],
    )
    monkeypatch.setattr(module, "BusinessCentralClient", lambda settings: fake_bc)
    with pytest.raises(ValueError, match="blocked before cancellation"):
        module.main()
    assert cancellations == []


def test_only_explicit_original_invoice_is_exempt_from_reference_collision():
    preview = source_preview()
    old = {
        "number": "GTFVRTEST", "status": "Open",
        "externalDocumentNumber": "MTMLXGT-CANARY-INT",
        "totalAmountIncludingTax": 1, "currencyCode": "GTQ", "customerNumber": "OLD-CUSTOMER",
    }
    bc = SimpleNamespace(find_entities=lambda *args, **kwargs: [old])
    full._require_replacement_reference_preflight(
        preview=preview, bc=bc, market="GT", old_invoice_numbers=["GTFVRTEST"],
    )
    with pytest.raises(ValueError, match="blocked before cancellation"):
        full._require_replacement_reference_preflight(
            preview=preview, bc=bc, market="GT", old_invoice_numbers=["GTFVR-DIFFERENT"],
        )


def test_matching_active_replacement_reference_can_be_reviewed_without_cancellation():
    row = {
        "number": "GTFVR-REPLACEMENT", "status": "Open",
        "externalDocumentNumber": "MTMLXGT-CANARY-INT",
        "totalAmountIncludingTax": 19200, "currencyCode": "USD", "customerNumber": "C00118",
    }
    bc = SimpleNamespace(find_entities=lambda *args, **kwargs: [row])
    full._require_replacement_reference_preflight(
        preview=source_preview(), bc=bc, market="GT", old_invoice_numbers=["GTFVRTEST"],
        allow_matching_replacements=True,
    )


def test_full_main_blocks_other_matching_active_invoice_before_cancellation(monkeypatch):
    cancellations = prepare_main(monkeypatch, full, source_preview())
    row = {
        "number": "GTFVROTHER", "status": "Open",
        "externalDocumentNumber": "MTMLXGT-CANARY-INT",
        "totalAmountIncludingTax": 19200, "currencyCode": "USD", "customerNumber": "C00118",
    }
    monkeypatch.setattr(full, "BusinessCentralClient", lambda settings: SimpleNamespace(
        find_entities=lambda *args, **kwargs: [row],
    ))
    with pytest.raises(ValueError, match="GTFVROTHER.*not an original"):
        full.main()
    assert cancellations == []


def test_split_main_blocks_multiple_surviving_matching_invoices_before_cancellation(monkeypatch):
    cancellations = prepare_main(monkeypatch, split, source_preview())
    rows = [{
        "number": number, "status": "Open", "currencyCode": "USD", "customerNumber": "C00118",
        "externalDocumentNumber": "MTMLXGT-CANARY-INT-2", "totalAmountIncludingTax": 2400,
    } for number in ("GTFVR-REPLACEMENT-1", "GTFVR-REPLACEMENT-2")]
    fake_bc = SimpleNamespace(
        get_posted_sales_invoice_by_number=lambda *args, **kwargs: {"externalDocumentNumber": "MTMLXGT-CANARY-INT"},
        find_entities=lambda *args, **kwargs: rows if "MTMLXGT-CANARY-INT-2" in kwargs["filters"] else [],
    )
    monkeypatch.setattr(split, "BusinessCentralClient", lambda settings: fake_bc)
    with pytest.raises(ValueError, match="more than one active replacement"):
        split.main()
    assert cancellations == []
