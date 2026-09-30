from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from business_central_client.mx_cancellation import MxReplacement, advance_mx_cancellation


PLAN = MxReplacement(
    "B0003376", "B_REPLACEMENT", "3a0c1f39-b96c-4d60-b615-e3be40d3128c",
    "a0c8d000-0000-4000-8000-000000000001", "UW-26-ES-002", Decimal("15307.20"), date(2026, 11, 3),
)


def row(state="Pending"):
    return {
        "id": "a0c8d000-0000-4000-8000-000000000002", "state": state,
        "invoiceNumber": PLAN.invoice_number, "replacementNumber": PLAN.replacement_number,
        "originalUuid": PLAN.original_uuid, "replacementUuid": PLAN.replacement_uuid,
        "externalDocumentNumber": PLAN.external_document_number, "amount": "15307.20",
        "replacementDueDate": "2026-11-03", "creditMemoNumber": "CR_CORRECTION",
    }


class FakeBC:
    def __init__(self, operation=None, sat_state="Pending"):
        self.operation = deepcopy(operation)
        self.sat_state = sat_state
        self.calls = []
        self.request_timeout = False
        self.finalize_timeout = False
        self.bad_balance = False

    def get_mx_cancellation(self, number):
        assert number == PLAN.invoice_number
        return deepcopy(self.operation)

    def get_posted_invoice_fel_description_by_number(self, number, *, market):
        assert market == "MX"
        return {"id": "original-row", "fiscalInvoiceNumberPac": PLAN.original_uuid}

    def request_mx_cancellation(self, invoice_id, payload):
        self.calls.append("request")
        assert payload["expectedAmountIncludingVat"] == 15307.2
        self.operation = row("Unknown" if self.request_timeout else "Pending")
        if self.request_timeout:
            raise TimeoutError("must never be blindly retried")

    def refresh_mx_cancellation(self, operation_id):
        self.calls.append("query")
        self.operation["state"] = self.sat_state

    def finalize_mx_cancellation(self, operation_id):
        self.calls.append("finalize")
        self.operation["state"] = "Completed"
        if self.finalize_timeout:
            raise TimeoutError("server committed but client timed out")

    def get_posted_sales_invoice_by_number(self, number, *, market):
        return {"status": "Canceled", "remainingAmount": 10 if self.bad_balance else 0}


def test_default_review_is_read_only():
    bc = FakeBC()
    assert advance_mx_cancellation(bc, PLAN)["status"] == "review_only"
    assert bc.calls == []


@pytest.mark.parametrize("state", ["Pending", "Rejected", "Unknown"])
def test_nonconfirmed_never_reverses_accounting(state):
    bc = FakeBC(sat_state=state)
    assert advance_mx_cancellation(bc, PLAN, apply=True, finalize_accounting=True)["status"] == state.lower()
    assert bc.calls == ["request", "query"]


def test_confirmation_requires_explicit_accounting_step():
    bc = FakeBC(row(), sat_state="Confirmed")
    assert advance_mx_cancellation(bc, PLAN, apply=True)["status"] == "confirmed"
    assert bc.calls == ["query"]


def test_confirmed_completion_and_repeat_are_idempotent():
    bc = FakeBC(row(), sat_state="Confirmed")
    assert advance_mx_cancellation(bc, PLAN, apply=True, finalize_accounting=True)["status"] == "completed"
    assert advance_mx_cancellation(bc, PLAN, apply=True, finalize_accounting=True)["status"] == "completed"
    assert bc.calls == ["query", "finalize"]


@pytest.mark.parametrize("state", ["Pending", "Unknown", "Rejected", "Confirmed"])
def test_existing_operation_is_never_resubmitted(state):
    bc = FakeBC(row(state), sat_state=state)
    advance_mx_cancellation(bc, PLAN, apply=True)
    assert bc.calls == ["query"]


def test_request_timeout_reads_operation_and_stops():
    bc = FakeBC()
    bc.request_timeout = True
    result = advance_mx_cancellation(bc, PLAN, apply=True, finalize_accounting=True)
    assert result["status"] == "request_outcome_uncertain"
    assert result["operation"]["state"] == "Unknown"
    assert bc.calls == ["request"]
    bc.sat_state = "Pending"
    advance_mx_cancellation(bc, PLAN, apply=True, finalize_accounting=True)
    assert bc.calls == ["request", "query"]


def test_accounting_timeout_is_reconciled_without_duplicate_credit():
    bc = FakeBC(row(), sat_state="Confirmed")
    bc.finalize_timeout = True
    assert advance_mx_cancellation(bc, PLAN, apply=True, finalize_accounting=True)["status"] == "accounting_outcome_uncertain"
    assert advance_mx_cancellation(bc, PLAN, apply=True, finalize_accounting=True)["status"] == "completed"
    assert bc.calls == ["query", "finalize"]


@pytest.mark.parametrize("key,value", [
    ("invoiceNumber", "WRONG"), ("replacementNumber", "WRONG"),
    ("externalDocumentNumber", "ANOTHER_SHIPMENT"), ("amount", "15307.21"),
    ("replacementDueDate", "2026-10-23"),
    ("originalUuid", "a0c8d000-0000-4000-8000-000000000003"),
    ("replacementUuid", "a0c8d000-0000-4000-8000-000000000003"),
])
def test_identity_mismatch_stops_before_any_mutation(key, value):
    existing = row()
    existing[key] = value
    bc = FakeBC(existing)
    with pytest.raises(ValueError):
        advance_mx_cancellation(bc, PLAN, apply=True, finalize_accounting=True)
    assert bc.calls == []


def test_completed_flag_does_not_override_outstanding_balance():
    bc = FakeBC(row("Completed"))
    bc.bad_balance = True
    with pytest.raises(ValueError, match="accounting reversal"):
        advance_mx_cancellation(bc, PLAN, apply=True)


@pytest.mark.parametrize("amount", ["0", "-1", "1.001", "NaN", "Infinity"])
def test_invalid_amounts_fail_before_reads(amount):
    from dataclasses import replace
    with pytest.raises(ValueError):
        replace(PLAN, amount_including_vat=Decimal(amount)).payload()


def test_self_substitution_is_rejected():
    from dataclasses import replace
    with pytest.raises(ValueError, match="substitute itself"):
        replace(PLAN, replacement_uuid=PLAN.original_uuid).payload()
