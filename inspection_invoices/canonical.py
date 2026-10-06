from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


CANONICAL_SCHEMA_VERSION = "mtm.inspection-invoice.v1"
DEFAULT_INVOICE_PAYLOAD_FIELD_ID = "5e825df5-9a5e-45f8-87cf-0b1daa16b38f"


class InspectionInvoicePayloadError(ValueError):
    """Raised when an inspection invoice payload is absent or cannot be issued safely."""


@dataclass(frozen=True)
class InspectionInvoicePayload:
    task_id: str
    bc_item: str
    description: str
    po_reference: str
    customer_name: str
    unit_price: Decimal
    quantity: Decimal
    currency: str
    inspection_date: date
    destination_country_code: str
    customer_number: str
    customer_id: str
    customer_tax_id: str
    market: str | None = None
    vendor: str | None = None
    quote_reference: str | None = None
    linked_quote_task_id: str | None = None

    @property
    def line_amount(self) -> Decimal:
        return self.unit_price * self.quantity


def load_inspection_invoice_payload_from_task(
    task: dict[str, Any],
    *,
    field_id: str = DEFAULT_INVOICE_PAYLOAD_FIELD_ID,
) -> InspectionInvoicePayload:
    field = next(
        (
            item
            for item in task.get("custom_fields") or []
            if str(item.get("id") or "") == field_id
        ),
        None,
    )
    if not field or field.get("value") in (None, ""):
        raise InspectionInvoicePayloadError(
            f"Inspection Invoice Payload field {field_id} is empty or unavailable."
        )

    value = field["value"]
    if isinstance(value, dict):
        raw_payload = value
    elif isinstance(value, str):
        try:
            raw_payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise InspectionInvoicePayloadError("Invoice Payload is not valid JSON.") from exc
    else:
        raise InspectionInvoicePayloadError("Invoice Payload must be a JSON object.")

    if not isinstance(raw_payload, dict):
        raise InspectionInvoicePayloadError("Invoice Payload must be a JSON object.")
    return parse_inspection_invoice_payload(raw_payload, task=task)


def parse_inspection_invoice_payload(
    raw_payload: dict[str, Any],
    *,
    task: dict[str, Any],
) -> InspectionInvoicePayload:
    if raw_payload.get("schema_version") != CANONICAL_SCHEMA_VERSION:
        raise InspectionInvoicePayloadError(
            f"schema_version must be {CANONICAL_SCHEMA_VERSION}."
        )

    task_id = _required_text(raw_payload, "task_id")
    expected_task_id = str(task.get("id") or "").strip()
    # ClickUp dynamic tokens can be serialized as task:<id>. Preserve the task
    # binding while accepting that transport-only prefix.
    if task_id.removeprefix("task:") != expected_task_id:
        raise InspectionInvoicePayloadError("task_id does not match the ClickUp task.")

    try:
        inspection_date = date.fromisoformat(_required_text(raw_payload, "inspection_date"))
    except ValueError as exc:
        raise InspectionInvoicePayloadError("inspection_date must use YYYY-MM-DD.") from exc

    unit_price = _positive_decimal(raw_payload.get("unit_price"), "unit_price")
    quantity = _positive_decimal(raw_payload.get("quantity"), "quantity")
    currency = _required_text(raw_payload, "currency").upper()
    market = _optional_text(raw_payload.get("market"))
    if market:
        market = market.upper()

    return InspectionInvoicePayload(
        task_id=task_id,
        bc_item=_required_text(raw_payload, "bc_item").upper(),
        description=_required_text(raw_payload, "description"),
        po_reference=_required_text(raw_payload, "po_reference"),
        customer_name=_required_text(raw_payload, "customer_name"),
        unit_price=unit_price,
        quantity=quantity,
        currency=currency,
        inspection_date=inspection_date,
        destination_country_code=_country_code(
            _required_text(raw_payload, "destination_country_code"),
            "destination_country_code",
        ),
        customer_number=_required_text(raw_payload, "customer_number").upper(),
        customer_id=_required_text(raw_payload, "customer_id"),
        customer_tax_id=_required_text(raw_payload, "customer_tax_id"),
        market=market,
        vendor=_optional_text(raw_payload.get("vendor")),
        quote_reference=_optional_text(raw_payload.get("quote_reference")),
        linked_quote_task_id=_optional_text(raw_payload.get("linked_quote_task_id")),
    )


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = _optional_text(payload.get(key))
    if not value:
        raise InspectionInvoicePayloadError(f"{key} is required.")
    return value


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _positive_decimal(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InspectionInvoicePayloadError(f"{label} must be a positive number.") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise InspectionInvoicePayloadError(f"{label} must be a positive number.")
    return parsed


def _country_code(value: str, label: str) -> str:
    """Normalize the limited country values accepted by this invoice workflow."""
    normalized = " ".join(value.strip().upper().replace(".", "").split())
    aliases = {
        "GT": "GT",
        "GUATEMALA": "GT",
        "SV": "SV",
        "EL SALVADOR": "SV",
        "CR": "CR",
        "COSTA RICA": "CR",
        "HN": "HN",
        "HONDURAS": "HN",
        "PA": "PA",
        "PANAMA": "PA",
        "PANAMÁ": "PA",
        "MX": "MX",
        "MEXICO": "MX",
        "US": "US",
        "USA": "US",
        "UNITED STATES": "US",
    }
    country_code = aliases.get(normalized)
    if not country_code:
        raise InspectionInvoicePayloadError(
            f"{label} must use a supported ISO country code (for example, GT, SV, or CR)."
        )
    return country_code
