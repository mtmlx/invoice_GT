from __future__ import annotations

import os
import re
import time
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from business_central_client.client import BusinessCentralClient

from inspection_invoices.canonical import (
    InspectionInvoicePayload,
    InspectionInvoicePayloadError,
    load_inspection_invoice_payload_from_task,
)


DEFAULT_MAGNA_INSPECTIONS_LIST_ID = "901707774763"
DEFAULT_INSPECTION_PRICE_FIELD_NAMES = ("Previo en origen (USD)",)


def prepare_inspection_invoice_preview(
    *,
    task: dict[str, Any],
    bc_client: BusinessCentralClient,
    today: date | None = None,
) -> dict[str, Any]:
    """Create a validated BC draft/line preview without changing BC or ClickUp."""
    try:
        payload = load_inspection_invoice_payload_from_task(
            task,
            field_id=_payload_field_id(),
        )
    except InspectionInvoicePayloadError as exc:
        return _blocked("invalid_invoice_payload", str(exc), task)

    configured_list_id = _env("INSPECTION_INVOICE_CLICKUP_LIST_ID", DEFAULT_MAGNA_INSPECTIONS_LIST_ID)
    task_list_id = str((task.get("list") or {}).get("id") or "").strip()
    if configured_list_id and task_list_id != configured_list_id:
        return _blocked(
            "unexpected_list",
            f"Inspection invoice automation only accepts ClickUp list {configured_list_id}.",
            task,
            payload=payload,
        )

    market = _env("INSPECTION_INVOICE_MARKET", "GT").upper()
    if payload.market and payload.market != market:
        return _blocked(
            "unsupported_market",
            f"Payload market {payload.market} does not match configured inspection market {market}.",
            task,
            payload=payload,
            market=market,
        )

    expected_currency = _env("INSPECTION_INVOICE_CURRENCY", "USD").upper()
    if payload.currency != expected_currency:
        return _blocked(
            "unsupported_currency",
            f"Inspection invoices support {expected_currency}; payload currency is {payload.currency}.",
            task,
            payload=payload,
            market=market,
        )

    source_price = _inspection_source_price(task)
    if source_price is not None and source_price != payload.unit_price:
        return _blocked(
            "inspection_price_mismatch",
            "Invoice Payload unit_price "
            f"{payload.unit_price:.2f} does not match ClickUp Previo en origen (USD) "
            f"{source_price:.2f}.",
            task,
            payload=payload,
            market=market,
        )

    try:
        customer = _resolve_customer(payload=payload, bc_client=bc_client, market=market)
    except CustomerIdentityError as exc:
        return _blocked("customer_identity_mismatch", str(exc), task, payload=payload, market=market)
    if not customer:
        return _blocked(
            "missing_bc_customer",
            "Business Central customer was not found from the required immutable customer identity.",
            task,
            payload=payload,
            market=market,
        )

    customer_number = str(customer.get("number") or "").strip().upper()
    customer_country = _customer_country_code(customer)
    if not customer_country:
        return _blocked(
            "missing_customer_country",
            f"BC customer {customer_number or customer.get('id')} does not have a country code.",
            task,
            payload=payload,
            market=market,
        )
    if customer_country != payload.destination_country_code:
        return _blocked(
            "customer_destination_country_mismatch",
            "BC customer "
            f"{customer_number or customer.get('id')} is in {customer_country}, but the payload destination is "
            f"{payload.destination_country_code}.",
            task,
            payload=payload,
            market=market,
        )
    customer_tax_id = _normalized_tax_id(customer.get("taxRegistrationNumber"))
    if not customer_tax_id or customer_tax_id != _normalized_tax_id(payload.customer_tax_id):
        return _blocked(
            "customer_tax_id_mismatch",
            f"BC customer {customer_number or customer.get('id')} does not match the payload customer_tax_id.",
            task,
            payload=payload,
            market=market,
        )

    customer_currency = str(customer.get("currencyCode") or "").strip().upper()
    if customer_currency and customer_currency != payload.currency:
        return _blocked(
            "customer_currency_mismatch",
            f"BC customer {customer.get('number') or customer.get('id')} uses {customer_currency}, not {payload.currency}.",
            task,
            payload=payload,
            market=market,
        )
    payment_terms_id = str(customer.get("paymentTermsId") or "").strip()
    if not payment_terms_id:
        return _blocked(
            "missing_payment_terms",
            "Business Central customer does not have Payment Terms configured.",
            task,
            payload=payload,
            market=market,
        )
    fel = _validate_gt_customer_fel(
        customer=customer,
        bc_client=bc_client,
        market=market,
        expected_country_code=payload.destination_country_code,
    )
    if fel["status"] != "ready":
        return _blocked(fel["status"], fel["message"], task, payload=payload, market=market)

    item = bc_client.resolve_item_by_number(payload.bc_item, market=market)
    if not item:
        return _blocked(
            "missing_bc_item",
            f"BC item {payload.bc_item} was not found in market {market}.",
            task,
            payload=payload,
            market=market,
        )
    if item.get("blocked") is True:
        return _blocked(
            "blocked_bc_item",
            f"BC item {payload.bc_item} is blocked.",
            task,
            payload=payload,
            market=market,
        )

    external_document_number = _idempotency_reference(task, payload)
    existing = _find_existing_invoice_family(
        bc_client=bc_client,
        market=market,
        external_document_number_prefix=external_document_number,
    )
    if existing:
        existing_customer_number = str(existing.get("customerNumber") or "").strip().upper()
        if existing_customer_number and existing_customer_number != customer_number:
            return _blocked(
                "conflicting_invoice_reference",
                "An invoice already exists for this inspection task under BC customer "
                f"{existing_customer_number}, not the resolved customer {customer_number}.",
                task,
                payload=payload,
                market=market,
            )
        return {
            "status": "duplicate_invoice",
            "message": "A Business Central invoice already exists for this inspection task.",
            "task_id": task.get("id"),
            "market": market,
            "external_document_number": external_document_number,
            "existing_invoice": existing,
        }

    invoice_date = _invoice_date(payload=payload, today=today)
    header_payload = {
        "customerId": customer["id"],
        "customerNumber": customer_number,
        "currencyCode": payload.currency,
        "externalDocumentNumber": external_document_number,
        "customerPurchaseOrderReference": payload.po_reference[:35],
        "invoiceDate": invoice_date.isoformat(),
        "postingDate": invoice_date.isoformat(),
        "paymentTermsId": payment_terms_id,
    }
    line_payloads = [
        {
            "lineType": "Item",
            "lineObjectNumber": item.get("number") or payload.bc_item,
            "itemId": item["id"],
            "description": payload.description,
            "quantity": float(payload.quantity),
            "unitPrice": float(payload.unit_price),
            "taxCode": _env("INSPECTION_INVOICE_TAX_CODE", "NO IVA"),
        }
    ]
    return {
        "status": "dry_run_ready",
        "task_id": task.get("id"),
        "custom_task_id": task.get("custom_id"),
        "market": market,
        "currency": payload.currency,
        "customer": {
            "id": customer.get("id"),
            "number": customer_number or None,
            "name": customer.get("displayName") or customer.get("name"),
            "payment_terms_id": payment_terms_id,
        },
        "item": {"id": item.get("id"), "number": item.get("number") or payload.bc_item},
        "payload": _payload_summary(payload),
        "proposed_bc_payload": header_payload,
        "proposed_bc_line_payloads": line_payloads,
        "total": float(payload.line_amount),
        "fel": fel,
    }


def issue_inspection_invoice(
    *,
    task: dict[str, Any],
    bc_client: BusinessCentralClient,
    today: date | None = None,
) -> dict[str, Any]:
    """Create, post, and FEL-stamp a preflighted inspection sales invoice."""
    preview = prepare_inspection_invoice_preview(task=task, bc_client=bc_client, today=today)
    if preview.get("status") == "duplicate_invoice":
        recovered = _recover_existing_stamped_invoice(preview=preview, bc_client=bc_client)
        if recovered is not None:
            return recovered
    if preview.get("status") != "dry_run_ready":
        return {**preview, "completed_stages": []}

    market = str(preview["market"])
    completed_stages: list[str] = []
    try:
        created = bc_client.create_sales_invoice(preview["proposed_bc_payload"], market=market)
        completed_stages.append("create_sales_invoice")
        invoice_id = str(created.get("id") or "").strip()
        if not invoice_id:
            raise ValueError("Business Central created an invoice without an id.")
        for line in preview["proposed_bc_line_payloads"]:
            bc_client.create_sales_invoice_line(invoice_id, line, market=market)
        completed_stages.append("create_sales_invoice_lines")
        bc_client.post_sales_invoice(invoice_id, market=market)
        completed_stages.append("post_sales_invoice")
        posted = _wait_for_posted_invoice(bc_client=bc_client, created=created, market=market)
        invoice_number = str(posted.get("number") or "").strip()
        fel_row = _wait_for_fel_row(bc_client=bc_client, invoice_number=invoice_number, market=market)
        if str(fel_row.get("electronicDocumentStatus") or "").strip().lower() != "stamp received":
            bc_client.sync_posted_invoice_fel_line_descriptions(fel_row["id"], market=market)
            completed_stages.append("sync_fel_descriptions")
            fel_row = _wait_for_fel_row(bc_client=bc_client, invoice_number=invoice_number, market=market)
            if str(fel_row.get("electronicDocumentStatus") or "").strip().lower() != "stamp received":
                bc_client.stamp_posted_invoice_fel(fel_row["id"], market=market)
                completed_stages.append("stamp_fel_invoice")
                fel_row = _wait_for_stamp(bc_client=bc_client, invoice_number=invoice_number, market=market)
        return {
            "status": "applied",
            "market": market,
            "preview": preview,
            "created_invoices": [{**created, "invoice_group": "INT"}],
            "finalized_invoices": [
                {
                    "invoice_group": "INT",
                    "number": invoice_number,
                    "externalDocumentNumber": posted.get("externalDocumentNumber"),
                    "posted_invoice_after_stamp": posted,
                    "custom_api_row_after_stamp": fel_row,
                }
            ],
            "completed_stages": completed_stages,
        }
    except Exception as exc:  # noqa: BLE001 - return a recoverable operation result to the webhook.
        return {
            "status": "failed_post_creation",
            "message": str(exc),
            "market": market,
            "preview": preview,
            "completed_stages": completed_stages,
            "failed_stage": completed_stages[-1] if completed_stages else "create_sales_invoice",
        }


def _recover_existing_stamped_invoice(
    *,
    preview: dict[str, Any],
    bc_client: BusinessCentralClient,
) -> dict[str, Any] | None:
    """Allow ClickUp delivery to resume after a prior successful BC issue."""
    existing = preview.get("existing_invoice")
    if not isinstance(existing, dict):
        return None

    invoice_id = str(existing.get("id") or "").strip()
    invoice_number = str(existing.get("number") or "").strip()
    market = str(preview.get("market") or "").strip().upper()
    if not invoice_id or not invoice_number.startswith("GTFVR") or not market:
        return None

    fel_row = bc_client.get_posted_invoice_fel_description_by_number(
        invoice_number,
        market=market,
    )
    if str((fel_row or {}).get("electronicDocumentStatus") or "").strip().lower() != "stamp received":
        return None

    external_document_number = str(
        existing.get("externalDocumentNumber") or preview.get("external_document_number") or ""
    ).strip()
    return {
        "status": "applied",
        "market": market,
        "preview": preview,
        "created_invoices": [],
        "finalized_invoices": [
            {
                "invoice_group": "INT",
                "number": invoice_number,
                "externalDocumentNumber": external_document_number,
                "posted_invoice_after_stamp": existing,
                "custom_api_row_after_stamp": fel_row,
            }
        ],
        "completed_stages": ["recover_existing_posted_invoice"],
        "recovered_existing_invoice": True,
    }


def _resolve_customer(
    *, payload: InspectionInvoicePayload, bc_client: BusinessCentralClient, market: str
) -> dict[str, Any] | None:
    """Resolve both immutable identifiers and refuse all name-based fallbacks."""
    by_id = bc_client.get_customer_by_id(payload.customer_id, market=market)
    escaped_customer_number = payload.customer_number.replace("'", "''")
    by_number_rows = bc_client.find_entities(
        "customers", filters=f"number eq '{escaped_customer_number}'", top=2, market=market
    )
    if len(by_number_rows) != 1:
        raise CustomerIdentityError(
            f"BC customer number {payload.customer_number} did not resolve to exactly one customer."
        )
    by_number = by_number_rows[0]
    if not by_id:
        raise CustomerIdentityError(f"BC customer id {payload.customer_id} was not found.")

    payload_customer_id = payload.customer_id.strip().lower()
    resolved_ids = {
        str(by_id.get("id") or "").strip().lower(),
        str(by_number.get("id") or "").strip().lower(),
    }
    resolved_numbers = {
        str(by_id.get("number") or "").strip().upper(),
        str(by_number.get("number") or "").strip().upper(),
    }
    if (
        not payload_customer_id
        or payload_customer_id not in resolved_ids
        or resolved_ids != {payload_customer_id}
        or resolved_numbers != {payload.customer_number}
    ):
        raise CustomerIdentityError(
            "Payload customer_id and customer_number do not resolve to the same BC customer."
        )
    return by_id


def _validate_gt_customer_fel(
    *,
    customer: dict[str, Any],
    bc_client: BusinessCentralClient,
    market: str,
    expected_country_code: str,
) -> dict[str, Any]:
    customer_number = str(customer.get("number") or "").strip()
    row = bc_client.get_customer_invoicing_by_number(customer_number, market=market)
    if not row:
        return {"status": "missing_customer_invoicing_row", "message": "BC customer invoicing data is unavailable."}
    if row.get("felCountryReady") is not True or not str(row.get("resolvedFelCountryCode") or "").strip():
        return {"status": "missing_fel_country_source", "message": "BC customer is not FEL country ready."}
    resolved_fel_country = str(row.get("resolvedFelCountryCode") or "").strip().upper()
    if resolved_fel_country != expected_country_code:
        return {
            "status": "fel_destination_country_mismatch",
            "message": "BC FEL country "
            f"{resolved_fel_country} does not match payload destination {expected_country_code}.",
        }
    return {"status": "ready", "resolved_fel_country": resolved_fel_country}


def _find_existing_invoice_family(
    *, bc_client: BusinessCentralClient, market: str, external_document_number_prefix: str
) -> dict[str, Any] | None:
    escaped_prefix = external_document_number_prefix.replace("'", "''")
    rows = bc_client.find_entities(
        "salesInvoices",
        filters=f"startswith(externalDocumentNumber, '{escaped_prefix}')",
        top=20,
        market=market,
    )
    active_rows = [
        row
        for row in rows
        if str(row.get("status") or "").strip().lower() not in {"canceled", "cancelled"}
    ]
    if not active_rows:
        return None
    active_rows.sort(
        key=lambda row: str(row.get("lastModifiedDateTime") or row.get("postingDate") or ""),
        reverse=True,
    )
    return active_rows[0]


def _idempotency_reference(task: dict[str, Any], payload: InspectionInvoicePayload) -> str:
    task_reference = str(task.get("custom_id") or task.get("id") or payload.task_id).strip()
    return f"{task_reference}-INT"


def _inspection_source_price(task: dict[str, Any]) -> Decimal | None:
    """Return the populated ClickUp origin-inspection price, when available."""
    configured_id = _env("INSPECTION_INVOICE_PRICE_FIELD_ID", "")
    configured_names = {
        _normalize_field_name(name)
        for name in _env_csv(
            "INSPECTION_INVOICE_PRICE_FIELD_NAMES",
            default=DEFAULT_INSPECTION_PRICE_FIELD_NAMES,
        )
    }
    for field in task.get("custom_fields") or []:
        field_id = str(field.get("id") or "").strip()
        field_name = _normalize_field_name(str(field.get("name") or ""))
        if (configured_id and field_id == configured_id) or field_name in configured_names:
            return _decimal_or_none(field.get("value"))
    return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    normalized = re.sub(r"[^0-9.\-]", "", str(value))
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _normalize_field_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _invoice_date(*, payload: InspectionInvoicePayload, today: date | None) -> date:
    use_inspection_date = _env_bool("INSPECTION_INVOICE_USE_INSPECTION_DATE_AS_POSTING_DATE", default=False)
    return payload.inspection_date if use_inspection_date else (today or date.today())


def _wait_for_posted_invoice(
    *, bc_client: BusinessCentralClient, created: dict[str, Any], market: str
) -> dict[str, Any]:
    invoice_id = str(created.get("id") or "").strip()
    reference = str(created.get("externalDocumentNumber") or "").strip()
    for _ in range(3):
        invoice = bc_client.get_entity("salesInvoices", invoice_id, market=market)
        number = str((invoice or {}).get("number") or "").strip().upper()
        if invoice and number.startswith("GTFVR"):
            return invoice
        if reference:
            invoice = _find_existing_invoice_family(
                bc_client=bc_client,
                market=market,
                external_document_number_prefix=reference,
            )
            if invoice:
                return invoice
        time.sleep(2)
    raise ValueError("Business Central did not return the posted inspection invoice.")


def _wait_for_fel_row(
    *, bc_client: BusinessCentralClient, invoice_number: str, market: str
) -> dict[str, Any]:
    for _ in range(5):
        row = bc_client.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
        if row:
            return row
        time.sleep(2)
    raise ValueError("Business Central did not return a FEL row for the inspection invoice.")


def _wait_for_stamp(*, bc_client: BusinessCentralClient, invoice_number: str, market: str) -> dict[str, Any]:
    last_row: dict[str, Any] | None = None
    for _ in range(6):
        row = _wait_for_fel_row(bc_client=bc_client, invoice_number=invoice_number, market=market)
        last_row = row
        if str(row.get("electronicDocumentStatus") or "").strip().lower() == "stamp received":
            return row
        time.sleep(2)
    raise ValueError(
        "FEL stamp was not received for the inspection invoice. "
        f"Status: {(last_row or {}).get('electronicDocumentStatus') or 'unknown'}."
    )


def _payload_field_id() -> str:
    return _env("INSPECTION_INVOICE_PAYLOAD_FIELD_ID", "5e825df5-9a5e-45f8-87cf-0b1daa16b38f")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _env_csv(name: str, *, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    values = tuple(value.strip() for value in raw_value.split(",") if value.strip())
    return values or default


def _env_bool(name: str, *, default: bool) -> bool:
    raw_value = os.getenv(name, str(default)).strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _payload_summary(payload: InspectionInvoicePayload) -> dict[str, Any]:
    summary = asdict(payload)
    summary["unit_price"] = float(payload.unit_price)
    summary["quantity"] = float(payload.quantity)
    summary["line_amount"] = float(payload.line_amount)
    summary["inspection_date"] = payload.inspection_date.isoformat()
    return summary


class CustomerIdentityError(ValueError):
    """Raised when immutable customer identifiers cannot be proven consistent."""


def _customer_country_code(customer: dict[str, Any]) -> str | None:
    raw_value = str(customer.get("country") or customer.get("countryRegionCode") or "").strip()
    if not raw_value:
        return None
    aliases = {
        "GT": "GT",
        "GUATEMALA": "GT",
        "SV": "SV",
        "EL SALVADOR": "SV",
        "CR": "CR",
        "COSTA RICA": "CR",
        "MX": "MX",
        "MEXICO": "MX",
        "US": "US",
        "USA": "US",
        "UNITED STATES": "US",
    }
    return aliases.get(" ".join(raw_value.upper().replace(".", "").split()))


def _normalized_tax_id(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _blocked(
    status: str,
    message: str,
    task: dict[str, Any],
    *,
    payload: InspectionInvoicePayload | None = None,
    market: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "message": message, "task_id": task.get("id")}
    if market:
        result["market"] = market
    if payload:
        result["payload"] = _payload_summary(payload)
    return result
