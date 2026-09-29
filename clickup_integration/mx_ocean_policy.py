"""Approved USD Mexico FCL grouping, ETA credit terms, and posting checks."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

ETA_FIELD_ID = "736ddd1d-33da-4ff8-a128-f7f3f738987d"
# Explicit source pairs: shared BC item numbers alone must never merge charges.
CHARGE_GROUPS = (
    ("c8935d06-7f43-46c5-a1ef-444314dca0e9", "eed2e3aa-781e-4bbf-86a4-3353fc7d057d", "INT000000026"),
    ("fe89391f-35e7-429e-a03c-b19f529d79e5", "fd301630-ec8b-4b30-b011-41412aa02576", "INT000000011"),
)


def applies(*, market, currency, product):
    return (market == "MX" and currency == "USD"
            and str(product.get("name") or "").strip().upper() == "OCEAN/FCL")


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def group_charges(charges):
    result = [dict(charge) for charge in charges]
    for charge in result:
        amount = charge.get("amount")
        if amount is not None and (not Decimal(str(amount)).is_finite() or amount < 0):
            raise ValueError("Mexico ocean charges must be finite, nonnegative amounts.")
    for base_id, addition_id, item in CHARGE_GROUPS:
        bases = [c for c in result if c.get("source_field_id") == base_id]
        additions = [c for c in result if c.get("source_field_id") == addition_id]
        if len(bases) > 1 or len(additions) > 1:
            raise ValueError("Duplicate Mexico ocean source mapping.")
        if not additions or not additions[0].get("amount"):
            continue
        if not bases or bases[0].get("item_number") != item:
            raise ValueError("Mexico ocean grouping requires the approved destination item mapping.")
        base, addition = bases[0], additions[0]
        if any(c.get("quantity_basis", "shipment") != "shipment" for c in (base, addition)):
            raise ValueError("Mexico ocean grouped charges require shipment quantities.")
        base["source_components"] = [
            {key: (float(c[key]) if key == "amount" and c.get(key) is not None else c.get(key))
             for key in ("charge_name", "source_field", "source_field_id", "amount")}
            for c in (base, addition) if c.get("amount")
        ]
        base["amount"] = sum((Decimal(str(c.get("amount") or 0)) for c in (base, addition)), Decimal(0))
        result.remove(addition)
    return result


def credit_policy(custom_fields, payment_terms, payment_terms_id):
    fields = [f for f in custom_fields.values() if f.get("id") == ETA_FIELD_ID]
    if len(fields) != 1 or fields[0].get("value") in (None, ""):
        raise ValueError("Mexico ocean requires the authoritative shipment ETA field; task due date is not an ETA.")
    raw = fields[0]["value"]
    try:
        if isinstance(raw, (int, float)) or str(raw).isdigit():
            timestamp = float(raw)
            if timestamp > 1_000_000_000_000:
                timestamp /= 1000
            eta = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ZoneInfo("America/Mexico_City")).date()
        else:
            eta = date.fromisoformat(str(raw))
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("Mexico ocean shipment ETA is invalid.") from exc
    if not payment_terms_id or payment_terms.get("id") != payment_terms_id:
        raise ValueError("BC payment terms identity does not match the customer.")
    formula = str(payment_terms.get("dueDateCalculation") or "").strip().upper()
    match = re.fullmatch(r"(?:([0-9]+)D|<([0-9]+)D>)", formula)
    if not match:
        raise ValueError(f"Unsupported BC credit formula {formula!r}; expected calendar days such as 30D.")
    days = int(match.group(1) or match.group(2))
    if payment_terms.get("discountDateCalculation") or payment_terms.get("discountPercent"):
        raise ValueError("ETA-based discount terms require separate review.")
    return {"basis": "shipment_eta_plus_bc_credit_days", "eta_field_id": ETA_FIELD_ID,
            "eta_date": eta.isoformat(), "timezone": "America/Mexico_City",
            "payment_terms_id": payment_terms_id, "payment_terms_code": payment_terms.get("code"),
            "due_date_calculation": formula, "credit_days": days,
            "due_date": (eta + timedelta(days=days)).isoformat()}


def vat_breakdown(lines):
    groups = {"INT": {"subtotal": Decimal(0), "vat": Decimal(0)},
              "NAT": {"subtotal": Decimal(0), "vat": Decimal(0)}}
    for line in lines:
        if line.get("lineType") == "Comment":
            continue
        group = str(line.get("item_number") or line.get("lineObjectNumber") or "")[:3]
        if group not in groups:
            raise ValueError("Mexico ocean requires an INT or NAT item for every charge.")
        amount = money(line["amount"] if "amount" in line else Decimal(str(line["quantity"])) * Decimal(str(line["unitPrice"])))
        groups[group]["subtotal"] += amount
        groups[group]["vat"] += money(amount * (Decimal("0.16") if group == "NAT" else Decimal(0)))
    return {group: {"vat_rate": 16 if group == "NAT" else 0,
                    **{k: float(v) for k, v in values.items()}} for group, values in groups.items()}


def verify_invoice(invoice, actual_lines, expected_header, expected_lines):
    """Fail before posting on date, identity, charge or BC VAT drift."""
    for key in ("dueDate", "invoiceDate", "postingDate", "paymentTermsId", "customerId", "customerNumber", "currencyCode", "externalDocumentNumber"):
        if key in expected_header and invoice.get(key) != expected_header[key]:
            raise ValueError(f"Mexico ocean BC readback mismatch: {key}.")
    def signature(line):
        return (line.get("lineObjectNumber"), str(line.get("description") or ""),
                Decimal(str(line["quantity"])), Decimal(str(line["unitPrice"])))
    expected_lines = [line for line in expected_lines if line.get("lineType") == "Item"]
    actual = [line for line in actual_lines if line.get("lineType") == "Item"]
    if any(line.get("lineType") != "Item" and money(line.get("amountExcludingTax") or 0) for line in actual_lines):
        raise ValueError("Mexico ocean BC readback contains an unexpected billable line.")
    if Counter(map(signature, actual)) != Counter(map(signature, expected_lines)):
        raise ValueError("Mexico ocean BC line readback does not match the approved grouped charges.")
    for line in actual:
        expected_amount = money(Decimal(str(line["quantity"])) * Decimal(str(line["unitPrice"])))
        rate = Decimal("16") if line["lineObjectNumber"].startswith("NAT") else Decimal(0)
        if money(line["amountExcludingTax"]) != expected_amount or Decimal(str(line["taxPercent"])) != rate:
            raise ValueError("Mexico ocean BC line amount or VAT rate mismatch.")
    breakdown = vat_breakdown(expected_lines)
    subtotal = sum((money(g["subtotal"]) for g in breakdown.values()), Decimal(0))
    vat = sum((money(g["vat"]) for g in breakdown.values()), Decimal(0))
    for key, expected in (("totalAmountExcludingTax", subtotal), ("totalTaxAmount", vat), ("totalAmountIncludingTax", subtotal + vat)):
        if money(invoice[key]) != expected:
            raise ValueError(f"Mexico ocean BC readback mismatch: {key}.")
