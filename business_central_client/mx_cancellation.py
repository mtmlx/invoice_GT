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
    original_amount_including_vat: Decimal | None = None
    final_invoice_number: str | None = None
    final_uuid: str | None = None
    final_amount_including_vat: Decimal | None = None
    final_due_date: date | None = None

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
        payload = {
            "substitutionInvoiceNumber": self.replacement_number,
            "expectedOriginalUuid": original,
            "expectedReplacementUuid": replacement,
            "expectedExternalDocumentNumber": self.external_document_number,
            "expectedAmountIncludingVat": float(amount),
            "expectedReplacementDueDate": self.replacement_due_date.isoformat(),
        }
        if self.original_amount_including_vat is not None:
            original_amount = Decimal(str(self.original_amount_including_vat))
            if not original_amount.is_finite() or original_amount <= 0 or original_amount != original_amount.quantize(Decimal("0.01")):
                raise ValueError("Expected original total must be a positive, exact cent amount")
            payload["expectedOriginalAmountIncludingVat"] = float(original_amount)
        final_fields = (self.final_invoice_number, self.final_uuid,
                        self.final_amount_including_vat, self.final_due_date)
        if any(value is not None for value in final_fields):
            if not all(value is not None for value in final_fields) or self.original_amount_including_vat is None:
                raise ValueError("Chain recovery requires a complete final identity and original amount")
            final_uuid = str(UUID(self.final_uuid)).upper()
            if not self.final_invoice_number or self.final_invoice_number in {self.invoice_number, self.replacement_number} or final_uuid in {original, replacement}:
                raise ValueError("Chain recovery requires three distinct invoice identities")
            final_amount = Decimal(str(self.final_amount_including_vat))
            if not final_amount.is_finite() or final_amount <= 0 or final_amount != final_amount.quantize(Decimal("0.01")):
                raise ValueError("Expected final total must be a positive, exact cent amount")
            payload.update({
                "finalInvoiceNumber": self.final_invoice_number,
                "expectedFinalUuid": final_uuid,
                "expectedFinalAmountIncludingVat": float(final_amount),
                "expectedFinalDueDate": self.final_due_date.isoformat(),
            })
        return payload


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
    expected_original = plan.original_amount_including_vat or plan.amount_including_vat
    actual_original = row.get("originalAmount") or row["amount"]
    if Decimal(str(actual_original)) != Decimal(str(expected_original)):
        raise ValueError("BC cancellation original total differs from the approved amount")
    if plan.final_invoice_number is None:
        if row.get("finalInvoiceNumber"):
            raise ValueError("BC cancellation is bound to an unapproved final chain invoice")
    else:
        if (row.get("finalInvoiceNumber") != plan.final_invoice_number or
                row.get("finalDueDate") != plan.final_due_date.isoformat()):
            raise ValueError("BC cancellation final chain identity differs from the approved invoice")
        if str(UUID(row["finalUuid"])) != str(UUID(plan.final_uuid)):
            raise ValueError("BC cancellation final UUID differs from the approved invoice")
        if Decimal(str(row["finalAmount"])) != Decimal(str(plan.final_amount_including_vat)):
            raise ValueError("BC cancellation final total differs from the approved amount")


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
    if operation["state"] != "Completed" or plan.final_invoice_number is not None:
        bc.refresh_mx_cancellation(operation["id"])
        operation = bc.get_mx_cancellation(plan.invoice_number)
        if not operation:
            raise ValueError("BC cancellation disappeared during verification")
        _validate_operation(operation, plan)
        if plan.final_invoice_number is not None and operation.get("finalSatStatus") != "Vigente":
            raise ValueError("The final corrected chain CFDI is not confirmed active by SAT")
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
