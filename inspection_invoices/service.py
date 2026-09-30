from __future__ import annotations

import os
import logging
import time
from dataclasses import asdict
from datetime import date
from typing import Any

from business_central_client.client import BusinessCentralClient

from inspection_invoices.canonical import (
    InspectionInvoicePayload,
    InspectionInvoicePayloadError,
    load_inspection_invoice_payload_from_task,
)


DEFAULT_MAGNA_INSPECTIONS_LIST_ID = "901707774763"


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

    customer = _resolve_customer(payload=payload, bc_client=bc_client, market=market)
    if not customer:
        return _blocked(
            "missing_bc_customer",
            f"Business Central customer {payload.customer_name!r} was not found in market {market}.",
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
    fel = _validate_gt_customer_fel(customer=customer, bc_client=bc_client, market=market)
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
    customer_number = str(customer.get("number") or "").strip()
    existing = _find_existing_invoice(
        bc_client=bc_client,
        market=market,
        external_document_number=external_document_number,
        customer_number=customer_number or None,
    )
    if existing:
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
            "lineType": "Comment",
            "description": f"MTM INSPECTION DATE {payload.inspection_date.isoformat()}",
        },
        {
            "lineType": "Item",
            "lineObjectNumber": item.get("number") or payload.bc_item,
            "itemId": item["id"],
            "description": payload.description,
            "quantity": float(payload.quantity),
            "unitPrice": float(payload.unit_price),
            "taxCode": _env("INSPECTION_INVOICE_TAX_CODE", "NO IVA"),
        },
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
    except Exception:  # noqa: BLE001 - return a recoverable operation result to the webhook.
        logging.getLogger(__name__).exception("Inspection invoice processing failed task=%s", task.get("id"))
        return {
            "status": "failed_post_creation",
            "message": "Inspection invoice processing failed. Review recorded stages before retrying.",
            "market": market,
            "preview": preview,
            "completed_stages": completed_stages,
            "failed_stage": completed_stages[-1] if completed_stages else "create_sales_invoice",
        }


def _resolve_customer(
    *, payload: InspectionInvoicePayload, bc_client: BusinessCentralClient, market: str
) -> dict[str, Any] | None:
    if payload.customer_id:
        customer = bc_client.get_customer_by_id(payload.customer_id, market=market)
        if customer:
            return customer
    if payload.customer_number:
        escaped_customer_number = payload.customer_number.replace("'", "''")
        rows = bc_client.find_entities(
            "customers", filters=f"number eq '{escaped_customer_number}'", top=2, market=market
        )
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise ValueError(f"More than one BC customer matched {payload.customer_number}.")
    return bc_client.resolve_customer_by_name(payload.customer_name, market=market)


def _validate_gt_customer_fel(
    *, customer: dict[str, Any], bc_client: BusinessCentralClient, market: str
) -> dict[str, Any]:
    if market != "GT":
        return {"status": "ready"}
    customer_number = str(customer.get("number") or "").strip()
    row = bc_client.get_customer_invoicing_by_number(customer_number, market=market)
    if not row:
        return {"status": "missing_customer_invoicing_row", "message": "BC customer invoicing data is unavailable."}
    if row.get("felCountryReady") is not True or not str(row.get("resolvedFelCountryCode") or "").strip():
        return {"status": "missing_fel_country_source", "message": "BC customer is not FEL country ready."}
    return {"status": "ready", "resolved_fel_country": row.get("resolvedFelCountryCode")}


def _find_existing_invoice(
    *, bc_client: BusinessCentralClient, market: str, external_document_number: str, customer_number: str | None
) -> dict[str, Any] | None:
    escaped_reference = external_document_number.replace("'", "''")
    filters = [f"externalDocumentNumber eq '{escaped_reference}'"]
    if customer_number:
        escaped_customer_number = customer_number.replace("'", "''")
        filters.append(f"customerNumber eq '{escaped_customer_number}'")
    rows = bc_client.find_entities("salesInvoices", filters=" and ".join(filters), top=5, market=market)
    return rows[0] if rows else None


def _idempotency_reference(task: dict[str, Any], payload: InspectionInvoicePayload) -> str:
    task_reference = str(task.get("custom_id") or task.get("id") or payload.task_id).strip()
    return f"{task_reference}-INT"


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
            invoice = bc_client.get_posted_sales_invoice_by_external_document_number(reference, market=market)
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
