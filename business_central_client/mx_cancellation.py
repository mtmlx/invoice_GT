"""Explicit, resumable Mexico cancellation. Does not create, stamp or email invoices.

The BC operation ledger is authoritative. A timeout never triggers a resubmission.
Polling is caller-driven; this module does not schedule background work.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from .client import BusinessCentralClient


@dataclass(frozen=True)
class MxReplacement:
    invoice_number: str
    replacement_number: str
    original_uuid: str
    replacement_uuid: str
    external_document_number: str
    amount_including_vat: Decimal
    replacement_due_date: date

    def payload(self) -> dict[str, Any]:
        original = str(UUID(self.original_uuid)).upper()
        replacement = str(UUID(self.replacement_uuid)).upper()
        if self.invoice_number == self.replacement_number or original == replacement:
            raise ValueError("An invoice cannot substitute itself")
        if not self.external_document_number or not self.invoice_number or not self.replacement_number:
            raise ValueError("Exact invoice and shipment identities are required")
        amount = Decimal(str(self.amount_including_vat))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Expected total must be a positive, exact cent amount")
        return {
            "substitutionInvoiceNumber": self.replacement_number,
            "expectedOriginalUuid": original,
            "expectedReplacementUuid": replacement,
            "expectedExternalDocumentNumber": self.external_document_number,
            "expectedAmountIncludingVat": float(amount),
            "expectedReplacementDueDate": self.replacement_due_date.isoformat(),
        }


def _validate_operation(row: dict[str, Any], plan: MxReplacement) -> None:
    expected = {
        "invoiceNumber": plan.invoice_number,
        "replacementNumber": plan.replacement_number,
        "externalDocumentNumber": plan.external_document_number,
        "replacementDueDate": plan.replacement_due_date.isoformat(),
    }
    if any(row.get(key) != value for key, value in expected.items()):
        raise ValueError("BC cancellation does not match the approved replacement")
    for key, value in (("originalUuid", plan.original_uuid), ("replacementUuid", plan.replacement_uuid)):
        if str(UUID(row[key])) != str(UUID(value)):
            raise ValueError("BC cancellation UUID differs from the approved replacement")
    if Decimal(str(row["amount"])) != Decimal(str(plan.amount_including_vat)):
        raise ValueError("BC cancellation total differs from the approved amount")


def advance_mx_cancellation(
    bc: BusinessCentralClient, plan: MxReplacement, *, apply: bool = False,
    finalize_accounting: bool = False,
) -> dict[str, Any]:
    """Advance at most one request/status step and optionally confirmed accounting.

    Both booleans default false. Existing operations are never submitted again,
    including Rejected and Unknown. Unknown with no operation after a timeout is
    reported to the caller, not retried in this invocation.
    """
    payload = plan.payload()
    operation = bc.get_mx_cancellation(plan.invoice_number)
    if operation:
        _validate_operation(operation, plan)
    if not apply:
        return {"status": "review_only", "operation": operation, "request": payload}
    if not operation:
        original = bc.get_posted_invoice_fel_description_by_number(plan.invoice_number, market="MX")
        if not original or str(UUID(original["fiscalInvoiceNumberPac"])) != str(UUID(plan.original_uuid)):
            raise ValueError("Original fiscal identity changed before cancellation")
        try:
            bc.request_mx_cancellation(original["id"], payload)
        except Exception:
            # Suppress raw provider response/exception bodies; inspect only BC state.
            operation = bc.get_mx_cancellation(plan.invoice_number)
            if operation:
                _validate_operation(operation, plan)
            return {"status": "request_outcome_uncertain", "operation": operation}
    operation = bc.get_mx_cancellation(plan.invoice_number)
    if not operation:
        return {"status": "request_outcome_uncertain", "operation": None}
    _validate_operation(operation, plan)
    if operation["state"] != "Completed":
        bc.refresh_mx_cancellation(operation["id"])
        operation = bc.get_mx_cancellation(plan.invoice_number)
        if not operation:
            raise ValueError("BC cancellation disappeared during verification")
        _validate_operation(operation, plan)
    if operation["state"] == "Confirmed" and finalize_accounting:
        try:
            bc.finalize_mx_cancellation(operation["id"])
        except Exception:
            operation = bc.get_mx_cancellation(plan.invoice_number)
            if operation:
                _validate_operation(operation, plan)
            return {"status": "accounting_outcome_uncertain", "operation": operation}
        operation = bc.get_mx_cancellation(plan.invoice_number)
        if not operation:
            raise ValueError("BC cancellation disappeared after accounting finalization")
        _validate_operation(operation, plan)
    if operation["state"] == "Completed":
        invoice = bc.get_posted_sales_invoice_by_number(plan.invoice_number, market="MX")
        if not invoice or str(invoice.get("status", "")).lower() not in {"canceled", "cancelled"}:
            raise ValueError("Original BC cancellation is not verified")
        if Decimal(str(invoice.get("remainingAmount"))) != 0 or not operation.get("creditMemoNumber"):
            raise ValueError("Original accounting reversal is not verified")
    return {"status": str(operation["state"]).lower(), "operation": operation}
