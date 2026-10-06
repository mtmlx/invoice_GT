from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

from business_central_client.client import BusinessCentralClient
from business_central_client.config import Settings as BusinessCentralSettings
from clickup_integration.bc_sync import apply_clickup_to_bc_customer_sync
from clickup_integration.client import ClickUpClient
from clickup_integration.config import ClickUpSettings
from clickup_integration.create_preview import apply_clickup_bc_customer_create
from clickup_integration.invoice_delivery import (
    finalize_clickup_issued_invoices,
    send_issued_invoice_customer_emails,
    should_send_invoice_customer_email,
    validate_invoice_pdf_field_on_task,
)
from clickup_integration.invoice_sync import (
    InvoiceAutomationSettings,
    issue_clickup_bc_sales_invoice,
    prepare_clickup_bc_sales_invoice_preview,
    prepare_clickup_invoice_status_transition,
)
from clickup_integration.mapping import summarize_task_for_customer_mapping
from clickup_integration.matcher import match_clickup_customer_to_bc
from clickup_integration.demurrage_invoice_sync import (
    DemurrageInvoiceSettings,
    build_clickup_demurrage_invoice_context,
    issue_clickup_bc_demurrage_invoice,
    prepare_clickup_bc_demurrage_invoice_preview,
)
from clickup_integration.storage_invoice_sync import (
    StorageInvoiceSettings,
    build_clickup_storage_invoice_context,
    issue_clickup_bc_storage_invoice,
    prepare_clickup_bc_storage_invoice_preview,
)
from clickup_integration.writeback import prepare_clickup_bc_writeback
from inspection_invoices.service import (
    issue_inspection_invoice,
    prepare_inspection_invoice_preview,
)

app = FastAPI(title="ClickUp to Business Central Customer Bridge")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
EMAIL_LOGO_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "mtm-logix-email-logo-porcelain-v1.png"
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/assets/mtm-logix-email-logo-porcelain-v1.png", include_in_schema=False)
def mtm_invoice_email_logo() -> FileResponse:
    return FileResponse(
        EMAIL_LOGO_PATH,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/clickup/webhooks/invoice-sync/readiness")
def invoice_sync_readiness() -> dict[str, Any]:
    try:
        settings = InvoiceAutomationSettings.from_env()
    except Exception as exc:
        logger.exception("Invoice bridge readiness check failed.")
        return {
            "status": "not_ready",
            "message": str(exc),
        }
    missing_runtime_config = [
        name
        for name in ("CLICKUP_WEBHOOK_TOKEN", "CLICKUP_ACCESS_TOKEN")
        if not os.getenv(name, "").strip()
    ]

    return {
        "status": "not_ready" if missing_runtime_config else "ready",
        "missing_runtime_config": missing_runtime_config,
        "market": settings.supported_market,
        "currency": settings.supported_currency,
        "apply_mode": _env_bool("CLICKUP_INVOICE_WEBHOOK_APPLY", default=False),
        "customer_email_enabled": should_send_invoice_customer_email(settings.supported_market),
        "ready_status": settings.ready_status,
        "ok_finops_status": settings.ok_finops_status,
        "charge_mapping_count": len(settings.charge_mappings),
        "line_type": "Item" if settings.charge_mappings else "Account",
    }


@app.get("/clickup/webhooks/storage-invoice-sync/readiness")
def storage_invoice_sync_readiness() -> dict[str, Any]:
    try:
        invoice_settings = InvoiceAutomationSettings.from_env()
        storage_settings = StorageInvoiceSettings.from_env()
    except Exception as exc:
        logger.exception("Storage invoice bridge readiness check failed.")
        return {"status": "not_ready", "message": str(exc)}
    missing_runtime_config = [
        name for name in ("CLICKUP_ACCESS_TOKEN",) if not os.getenv(name, "").strip()
    ]
    if not _storage_invoice_webhook_token():
        missing_runtime_config.append(
            "CLICKUP_STORAGE_INVOICE_WEBHOOK_TOKEN or CLICKUP_WEBHOOK_TOKEN"
        )
    return {
        "status": "not_ready" if missing_runtime_config else "ready",
        "missing_runtime_config": missing_runtime_config,
        "apply_mode": _env_bool("CLICKUP_STORAGE_INVOICE_WEBHOOK_APPLY", default=False),
        "customer_email_enabled": should_send_invoice_customer_email(invoice_settings.supported_market),
        "market": invoice_settings.supported_market,
        "currency": invoice_settings.supported_currency,
        "required_invoice_status": storage_settings.required_invoice_status,
        "amount_field_id": storage_settings.amount_field_id,
        "days_field_id": storage_settings.days_field_id,
        "container_count_field_id": storage_settings.container_count_field_id,
        "cut_field_id": storage_settings.cut_field_id,
        "invoiced_field_id": storage_settings.invoiced_field_id,
        "aggregation_mode": "parent_subtasks",
        "bc_item_number": storage_settings.item_number,
        "daily_rate": float(storage_settings.daily_rate),
        "reference_suffix": storage_settings.reference_suffix,
    }


@app.get("/clickup/webhooks/demurrage-invoice-sync/readiness")
def demurrage_invoice_sync_readiness() -> dict[str, Any]:
    try:
        invoice_settings = InvoiceAutomationSettings.from_env()
        demurrage_settings = DemurrageInvoiceSettings.from_env()
    except Exception as exc:
        logger.exception("Demurrage invoice bridge readiness check failed.")
        return {"status": "not_ready", "message": str(exc)}
    missing_runtime_config = [
        name for name in ("CLICKUP_ACCESS_TOKEN",) if not os.getenv(name, "").strip()
    ]
    if not _demurrage_invoice_webhook_token():
        missing_runtime_config.append(
            "CLICKUP_DEMURRAGE_INVOICE_WEBHOOK_TOKEN or CLICKUP_WEBHOOK_TOKEN"
        )
    return {
        "status": "not_ready" if missing_runtime_config else "ready",
        "missing_runtime_config": missing_runtime_config,
        "apply_mode": _env_bool("CLICKUP_DEMURRAGE_INVOICE_WEBHOOK_APPLY", default=False),
        "customer_email_enabled": should_send_invoice_customer_email(invoice_settings.supported_market),
        "market": invoice_settings.supported_market,
        "currency": invoice_settings.supported_currency,
        "required_invoice_status": demurrage_settings.required_invoice_status,
        "amount_field_id": demurrage_settings.amount_field_id,
        "days_field_id": demurrage_settings.days_field_id,
        "container_count_field_id": demurrage_settings.container_count_field_id,
        "cut_field_id": demurrage_settings.cut_field_id,
        "invoiced_field_id": demurrage_settings.invoiced_field_id,
        "invoice_attachment_field_id": demurrage_settings.invoice_attachment_field_id,
        "rate_mode": "derived_per_shipment",
        "bc_item_number": demurrage_settings.item_number,
        "reference_suffix": demurrage_settings.reference_suffix,
    }


@app.get("/clickup/webhooks/inspection-invoice-sync/readiness")
def inspection_invoice_sync_readiness() -> dict[str, Any]:
    missing_runtime_config = [
        name for name in ("CLICKUP_ACCESS_TOKEN",) if not os.getenv(name, "").strip()
    ]
    if not _inspection_invoice_webhook_token():
        missing_runtime_config.append(
            "INSPECTION_INVOICE_WEBHOOK_TOKEN or CLICKUP_WEBHOOK_TOKEN"
        )
    return {
        "status": "not_ready" if missing_runtime_config else "ready",
        "release": "inspection-invoice-v8",
        "missing_runtime_config": missing_runtime_config,
        "apply_mode": _env_bool("INSPECTION_INVOICE_WEBHOOK_APPLY", default=False),
        "customer_email_enabled": should_send_invoice_customer_email(os.getenv("INSPECTION_INVOICE_MARKET", "GT").strip().upper() or "GT"),
        "market": os.getenv("INSPECTION_INVOICE_MARKET", "GT").strip().upper() or "GT",
        "currency": os.getenv("INSPECTION_INVOICE_CURRENCY", "USD").strip().upper() or "USD",
        "payload_field_id": os.getenv(
            "INSPECTION_INVOICE_PAYLOAD_FIELD_ID",
            "5e825df5-9a5e-45f8-87cf-0b1daa16b38f",
        ).strip(),
        "list_id": os.getenv("INSPECTION_INVOICE_CLICKUP_LIST_ID", "901707774763").strip(),
    }


@app.post("/whatsapp/webhooks/inbound")
async def whatsapp_inbound(
    request: Request,
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
) -> dict[str, Any]:
    try:
        from whatsapp_integration.booking_intake import BookingTarget, process_whatsapp_booking_intake
        from whatsapp_integration.config import WhatsAppSettings
        from whatsapp_integration.provider import (
            normalize_twilio_inbound,
            validate_twilio_request_signature,
        )
        from whatsapp_integration.router import route_customer_message

        form_payload = await _safe_form_urlencoded(request)

        settings = WhatsAppSettings.from_env(
            require_booking=True,
            require_twilio_auth=False,
        )
        if settings.twilio_validate_signature:
            if not settings.twilio_auth_token:
                raise HTTPException(status_code=500, detail="TWILIO_AUTH_TOKEN is not configured.")
            if not validate_twilio_request_signature(
                url=settings.twilio_validate_url or str(request.url),
                params=form_payload,
                provided_signature=x_twilio_signature,
                auth_token=settings.twilio_auth_token,
            ):
                raise HTTPException(status_code=401, detail="Invalid Twilio signature.")

        event = normalize_twilio_inbound(form_payload)
        clickup = ClickUpClient(ClickUpSettings.from_env())
        route = route_customer_message(event, settings, clickup=clickup)
        logger.info(
            "WhatsApp route decision source=%s reason=%s message_id=%s customer_phone=%s list_id=%s customer_task_id=%s customer_task_custom_id=%s",
            route.source,
            route.reason,
            event.get("message_id"),
            event.get("customer_phone"),
            route.list_id,
            route.customer_task_id,
            route.customer_task_custom_id,
        )
        if route.route != "booking_intake":
            result = {
                "status": "ignored",
                "reason": route.reason or "unsupported_route",
                "route": route.route,
                "route_source": route.source,
                "message_id": event.get("message_id"),
            }
            _log_webhook_result(task_id=None, result=result)
            return result
        if not route.list_id:
            raise HTTPException(
                status_code=500,
                detail="No ClickUp target list resolved for WhatsApp intake.",
            )

        result = process_whatsapp_booking_intake(
            event=event,
            clickup=clickup,
            settings=settings,
            target=BookingTarget(
                list_id=route.list_id,
                customer_name=route.customer_name,
                customer_task_id=route.customer_task_id,
                customer_task_name=route.customer_task_name,
                customer_task_custom_id=route.customer_task_custom_id,
                route_source=route.source,
            ),
        )
        _log_webhook_result(task_id=result.get("task_id"), result=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - exercised in runtime logs
        logger.exception("WhatsApp inbound webhook failed.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/clickup/webhooks/customer-sync")
@app.post("/clickup/webhooks/customer-sync{webhook_path:path}")
@app.post("/clickup/webhooks/customer-sync/{webhook_path:path}")
async def clickup_customer_sync(
    request: Request,
    webhook_path: str = "",
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    expected_token = os.getenv("CLICKUP_WEBHOOK_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=500, detail="CLICKUP_WEBHOOK_TOKEN is not configured.")
    provided_token = _extract_webhook_token(
        x_webhook_token=x_webhook_token,
        authorization=authorization,
    )
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token.")

    payload = await _safe_json(request)
    task_id = extract_task_id(payload) or extract_task_id_from_path(
        request.url.path,
        base_path="/clickup/webhooks/customer-sync",
    )
    if not task_id:
        result = {
            "status": "ignored",
            "reason": "missing_task_id",
        }
        _log_webhook_result(task_id=None, result=result)
        return result

    try:
        clickup = ClickUpClient(ClickUpSettings.from_env())
        bc = BusinessCentralClient(BusinessCentralSettings.from_env())

        team_id = _resolve_clickup_team_id(clickup)
        use_custom_task_ids = _env_bool("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", default=True)
        logger.info(
            "Processing ClickUp webhook task_id=%s custom_task_ids=%s team_id=%s",
            task_id,
            use_custom_task_ids,
            team_id,
        )
        task = _fetch_clickup_task_for_webhook(
            clickup=clickup,
            task_id=task_id,
            custom_task_ids=use_custom_task_ids,
            team_id=team_id,
        )
        if task is None:
            logger.warning(
                "Ignoring webhook task_id=%s because the ClickUp task could not be fetched.",
                task_id,
            )
            result = {
                "status": "ignored",
                "reason": "task_lookup_failed",
                "task_id": task_id,
                "custom_task_ids": use_custom_task_ids,
                "team_id": team_id,
            }
            _log_webhook_result(task_id=task_id, result=result)
            return result
        summary = summarize_task_for_customer_mapping(task)

        if not summary.get("sync_eligible"):
            result = {
                "status": "ignored",
                "reason": "not_current_customer",
                "task_id": summary.get("task_id"),
                "custom_id": summary.get("custom_id"),
                "task_status": summary.get("status"),
            }
            _log_webhook_result(task_id=task_id, result=result)
            return result

        custom_fields = summary.get("custom_fields") or {}
        bc_customer_id = (custom_fields.get("Business Central Customer ID") or {}).get("value")
        bc_match_status = _resolve_clickup_match_status(custom_fields)

        if bc_customer_id and bc_match_status == "Confirmed":
            result = apply_clickup_to_bc_customer_sync(
                clickup_summary=summary,
                bc_client=bc,
            )
            response = {
                "status": "processed",
                "action": "update_existing_customer",
                "result": result,
            }
            _log_webhook_result(task_id=task_id, result=response)
            return response

        match_result = match_clickup_customer_to_bc(clickup_summary=summary, bc_client=bc)
        if match_result.get("status") == "likely_match":
            writeback = prepare_clickup_bc_writeback(
                clickup_summary=summary,
                match_result=match_result,
                bc_client=bc,
            )
            _apply_clickup_customer_writeback(clickup=clickup, writeback=writeback)
            response = {
                "status": "processed",
                "action": "link_existing_customer",
                "result": {
                    "status": "applied",
                    "message": "Linked the existing Business Central customer back into ClickUp.",
                    "match_result": match_result,
                    "writeback": writeback,
                },
            }
            _log_webhook_result(task_id=task_id, result=response)
            return response

        result = apply_clickup_bc_customer_create(
            clickup_summary=summary,
            current_match_result=match_result,
            bc_client=bc,
        )
        if result.get("status") != "applied":
            response = {
                "status": "processed",
                "action": "create_blocked",
                "result": result,
            }
            _log_webhook_result(task_id=task_id, result=response)
            return response

        writeback = result["writeback"]
        _apply_clickup_customer_writeback(clickup=clickup, writeback=writeback)
        response = {
            "status": "processed",
            "action": "create_customer_and_writeback",
            "result": result,
        }
        _log_webhook_result(task_id=task_id, result=response)
        return response
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - exercised in runtime logs
        logger.exception("ClickUp customer webhook failed for task_id=%s", task_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/clickup/webhooks/invoice-sync")
@app.post("/clickup/webhooks/invoice-sync{webhook_path:path}")
@app.post("/clickup/webhooks/invoice-sync/{webhook_path:path}")
async def clickup_invoice_sync(
    request: Request,
    webhook_path: str = "",
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    expected_token = os.getenv("CLICKUP_WEBHOOK_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=500, detail="CLICKUP_WEBHOOK_TOKEN is not configured.")
    provided_token = _extract_webhook_token(
        x_webhook_token=x_webhook_token,
        authorization=authorization,
    )
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token.")

    payload = await _safe_json(request)
    task_id = extract_task_id(payload) or extract_task_id_from_path(
        request.url.path,
        base_path="/clickup/webhooks/invoice-sync",
    )
    if not task_id:
        result = {
            "status": "ignored",
            "reason": "missing_task_id",
        }
        _log_webhook_result(task_id=None, result=result)
        return result

    try:
        clickup = ClickUpClient(ClickUpSettings.from_env())
        bc = BusinessCentralClient(BusinessCentralSettings.from_env())
        settings = InvoiceAutomationSettings.from_env()

        team_id = _resolve_clickup_team_id(clickup)
        use_custom_task_ids = _env_bool("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", default=True)
        logger.info(
            "Processing ClickUp invoice webhook task_id=%s custom_task_ids=%s team_id=%s",
            task_id,
            use_custom_task_ids,
            team_id,
        )
        task = _fetch_clickup_task_for_webhook(
            clickup=clickup,
            task_id=task_id,
            custom_task_ids=use_custom_task_ids,
            team_id=team_id,
        )
        if task is None:
            result = {
                "status": "ignored",
                "reason": "task_lookup_failed",
                "task_id": task_id,
                "custom_task_ids": use_custom_task_ids,
                "team_id": team_id,
            }
            _log_webhook_result(task_id=task_id, result=result)
            return result

        summary = summarize_task_for_customer_mapping(task)
        actions: list[str] = []
        apply_mode = _env_bool("CLICKUP_INVOICE_WEBHOOK_APPLY", default=False)
        transition_result = prepare_clickup_invoice_status_transition(
            clickup_summary=summary,
            settings=settings,
        )
        if transition_result.get("status") == "ready_to_update" and apply_mode:
            if transition_result.get("status_field_id") and transition_result.get("target_status_option_id"):
                clickup.set_task_custom_field_value(
                    summary["task_id"],
                    transition_result["status_field_id"],
                    transition_result["target_status_option_id"],
                )
            else:
                clickup.update_task(
                    summary["task_id"],
                    status=settings.ready_status,
                    custom_task_ids=use_custom_task_ids,
                    team_id=team_id,
                )
            actions.append("update_status")
            logger.info(
                "Updated ClickUp task_id=%s status from %s to %s",
                summary.get("task_id"),
                summary.get("status"),
                settings.ready_status,
            )
            summary = _with_updated_custom_field_value(
                {**summary, "status": settings.ready_status},
                field_id=transition_result.get("status_field_id"),
                value=transition_result.get("target_status_option_id"),
            )
        elif transition_result.get("status") == "ready_to_update":
            actions.append("would_update_status")
            summary = _with_updated_custom_field_value(
                {**summary, "status": settings.ready_status},
                field_id=transition_result.get("status_field_id"),
                value=transition_result.get("target_status_option_id"),
            )

        invoice_result: dict[str, Any] | None = None
        if transition_result.get("status") in {"ready_to_update", "already_ready_to_invoice"}:
            if apply_mode:
                invoice_result = issue_clickup_bc_sales_invoice(
                    clickup_summary=summary,
                    bc_client=bc,
                    settings=settings,
                )
                actions.extend(invoice_result.get("completed_stages") or ["create_sales_invoice"])
                if invoice_result.get("status") == "applied":
                    invoice_result, customer_email_action = _deliver_customer_email_if_enabled(
                        clickup=clickup,
                        bc_client=bc,
                        clickup_summary=summary,
                        invoice_result=invoice_result,
                        settings=settings,
                    )
                    if customer_email_action == "sent":
                        actions.append("send_customer_email_from_bc")
                    elif customer_email_action == "failed" and invoice_result.get("error_comment"):
                        actions.append("comment_invoice_error")

                    if invoice_result.get("status") != "applied":
                        pass
                    else:
                        try:
                            delivery_result = finalize_clickup_issued_invoices(
                                clickup=clickup,
                                bc_client=bc,
                                clickup_summary=summary,
                                invoice_result=invoice_result,
                                settings=settings,
                                workspace_id=team_id,
                                mark_status=True,
                            )
                        except Exception as exc:
                            logger.exception(
                                "ClickUp invoice delivery failed after BC invoice creation task_id=%s",
                                summary.get("task_id"),
                            )
                            invoice_result = {
                                **invoice_result,
                                "status": "failed_post_creation",
                                "failed_stage": "entrega_clickup",
                                "message": str(exc),
                            }
                            error_comment = _write_invoice_error_comment(
                                clickup=clickup,
                                clickup_summary=summary,
                                stage="entrega_clickup",
                                invoice_result=invoice_result,
                            )
                            if error_comment:
                                invoice_result = {**invoice_result, "error_comment": error_comment}
                                actions.append("comment_invoice_error")
                        else:
                            invoice_result = {
                                **invoice_result,
                                "delivery": delivery_result,
                                "final_status_update": delivery_result.get("final_status_update"),
                            }
                            actions.append("upload_invoice_pdfs")
                            actions.append("comment_invoice_details")
                            actions.append("set_facturada_status")
                elif invoice_result.get("status") not in {"applied", "dry_run_ready"}:
                    error_comment = _write_invoice_error_comment(
                        clickup=clickup,
                        clickup_summary=summary,
                        stage=invoice_result.get("failed_stage") or "creacion_bc",
                        invoice_result=invoice_result,
                    )
                    if error_comment:
                        invoice_result = {**invoice_result, "error_comment": error_comment}
                        actions.append("comment_invoice_error")
            else:
                invoice_result = prepare_clickup_bc_sales_invoice_preview(
                    clickup_summary=summary,
                    bc_client=bc,
                    settings=settings,
                )
                actions.append("preview_sales_invoice")
            response = {
                "status": "processed",
                "mode": "apply" if apply_mode else "dry_run",
                "action": ",".join(actions),
                "transition": transition_result
                if actions and ("update_status" in actions or "would_update_status" in actions)
                else None,
                "result": invoice_result,
            }
            _log_webhook_result(task_id=task_id, result=response)
            return response

        response = {
            "status": "ignored",
            "reason": transition_result.get("status"),
            "task_id": summary.get("task_id"),
            "task_status": summary.get("status"),
            "market": summary.get("market"),
            "result": transition_result,
        }
        _log_webhook_result(task_id=task_id, result=response)
        return response
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - exercised in runtime logs
        logger.exception("ClickUp invoice webhook failed for task_id=%s", task_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/clickup/webhooks/storage-invoice-sync")
@app.post("/clickup/webhooks/storage-invoice-sync{webhook_path:path}")
@app.post("/clickup/webhooks/storage-invoice-sync/{webhook_path:path}")
async def clickup_storage_invoice_sync(
    request: Request,
    webhook_path: str = "",
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    expected_token = _storage_invoice_webhook_token()
    if not expected_token:
        raise HTTPException(status_code=500, detail="Storage invoice webhook token is not configured.")
    provided_token = _extract_webhook_token(
        x_webhook_token=x_webhook_token,
        authorization=authorization,
    )
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token.")

    payload = await _safe_json(request)
    task_id = extract_task_id(payload) or extract_task_id_from_path(
        request.url.path,
        base_path="/clickup/webhooks/storage-invoice-sync",
    )
    if not task_id:
        return {"status": "ignored", "reason": "missing_task_id"}

    clickup: ClickUpClient | None = None
    summary: dict[str, Any] | None = None
    issued: dict[str, Any] | None = None
    error_stage = "validacion_clickup"
    try:
        clickup = ClickUpClient(ClickUpSettings.from_env())
        bc = BusinessCentralClient(BusinessCentralSettings.from_env())
        invoice_settings = InvoiceAutomationSettings.from_env()
        storage_settings = StorageInvoiceSettings.from_env()
        team_id = _resolve_clickup_team_id(clickup)
        use_custom_task_ids = _env_bool("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", default=True)
        task = _fetch_clickup_task_for_webhook(
            clickup=clickup,
            task_id=task_id,
            custom_task_ids=use_custom_task_ids,
            team_id=team_id,
        )
        if task is None:
            return {"status": "ignored", "reason": "task_lookup_failed", "task_id": task_id}

        parent_value = task.get("parent")
        parent_id = (
            str(parent_value.get("id") or "").strip()
            if isinstance(parent_value, dict)
            else str(parent_value or "").strip()
        )
        invoice_task_id = parent_id or str(task.get("id") or "").strip()
        invoice_task = _fetch_clickup_task_for_webhook(
            clickup=clickup,
            task_id=invoice_task_id,
            custom_task_ids=False,
            team_id=team_id,
            include_subtasks=True,
        )
        if parent_id and invoice_task is None:
            blocked = {
                "status": "parent_lookup_failed",
                "message": "No fue posible resolver la tarea madre para validar la factura de almacenaje.",
            }
            error_comment = _write_invoice_error_comment(
                clickup=clickup,
                clickup_summary={
                    "task_id": str(task.get("id") or task_id),
                    "custom_id": task.get("custom_id"),
                    "name": task.get("name"),
                },
                stage="validacion_clickup",
                invoice_result=blocked,
            )
            result = {
                "status": "blocked",
                "mode": "dry_run",
                "reason": "parent_lookup_failed",
                "task_id": task.get("id"),
                "parent_task_id": parent_id,
                "error_comment": error_comment,
            }
            _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
            return result
        summary = build_clickup_storage_invoice_context(
            requested_task=task,
            invoice_task=invoice_task or task,
            storage_settings=storage_settings,
        )
        preview = prepare_clickup_bc_storage_invoice_preview(
            clickup_summary=summary,
            bc_client=bc,
            invoice_settings=invoice_settings,
            storage_settings=storage_settings,
        )
        apply_mode = _env_bool("CLICKUP_STORAGE_INVOICE_WEBHOOK_APPLY", default=False)
        preview_status = str(preview.get("status") or "").strip()
        apply_eligible_statuses = {"dry_run_ready", "duplicate_invoice"}
        if not apply_mode or preview_status not in apply_eligible_statuses:
            if preview_status not in {"dry_run_ready", "duplicate_invoice", "fully_invoiced"}:
                error_comment = _write_invoice_error_comment(
                    clickup=clickup,
                    clickup_summary=summary,
                    stage="validacion_clickup",
                    invoice_result=preview,
                )
                if error_comment:
                    preview = {**preview, "error_comment": error_comment}
            result = {
                "status": "processed" if preview_status in apply_eligible_statuses else "blocked",
                "mode": "dry_run",
                "task_id": task.get("id"),
                "invoice_owner_task_id": summary.get("task_id"),
                "result": preview,
            }
            _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
            return result

        error_stage = "creacion_bc"
        issued = issue_clickup_bc_storage_invoice(
            clickup_summary=summary,
            bc_client=bc,
            invoice_settings=invoice_settings,
            storage_settings=storage_settings,
        )
        if issued.get("status") != "applied":
            error_comment = _write_invoice_error_comment(
                clickup=clickup,
                clickup_summary=summary,
                stage=issued.get("failed_stage") or "creacion_bc",
                invoice_result=issued,
            )
            if error_comment:
                issued = {**issued, "error_comment": error_comment}
            result = {
                "status": "failed",
                "mode": "apply",
                "task_id": task.get("id"),
                "result": issued,
            }
            _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
            return result

        actions = list(issued.get("completed_stages") or [])
        if should_send_invoice_customer_email(str(issued.get("market") or invoice_settings.supported_market)):
            error_stage = "envio_cliente"
            customer_email_delivery = send_issued_invoice_customer_emails(
                bc_client=bc,
                invoice_result=issued,
                settings=invoice_settings,
            )
            issued = {**issued, "customer_email_delivery": customer_email_delivery}
            actions.append("send_customer_email_from_bc")

        error_stage = "entrega_clickup"
        delivery = finalize_clickup_issued_invoices(
            clickup=clickup,
            bc_client=bc,
            clickup_summary=summary,
            invoice_result=issued,
            settings=invoice_settings,
            workspace_id=team_id,
            mark_status=False,
        )
        actions.extend(("upload_invoice_pdf", "comment_invoice_details", "retain_facturada_status"))
        result = {
            "status": "processed",
            "mode": "apply",
            "action": ",".join(actions),
            "task_id": task.get("id"),
            "invoice_owner_task_id": summary.get("task_id"),
            "result": issued,
            "delivery": delivery,
            "final_status_update": None,
        }
        _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Storage invoice webhook failed task_id=%s", task_id)
        if clickup is not None and summary is not None:
            failed_result = {
                **(issued or {}),
                "status": "failed_post_creation" if issued else "failed",
                "failed_stage": error_stage,
                "message": str(exc),
            }
            _write_invoice_error_comment(
                clickup=clickup,
                clickup_summary=summary,
                stage=error_stage,
                invoice_result=failed_result,
            )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/clickup/webhooks/demurrage-invoice-sync")
@app.post("/clickup/webhooks/demurrage-invoice-sync{webhook_path:path}")
@app.post("/clickup/webhooks/demurrage-invoice-sync/{webhook_path:path}")
async def clickup_demurrage_invoice_sync(
    request: Request,
    webhook_path: str = "",
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    expected_token = _demurrage_invoice_webhook_token()
    if not expected_token:
        raise HTTPException(status_code=500, detail="Demurrage invoice webhook token is not configured.")
    provided_token = _extract_webhook_token(
        x_webhook_token=x_webhook_token,
        authorization=authorization,
    )
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token.")

    payload = await _safe_json(request)
    task_id = extract_task_id(payload) or extract_task_id_from_path(
        request.url.path,
        base_path="/clickup/webhooks/demurrage-invoice-sync",
    )
    if not task_id:
        return {"status": "ignored", "reason": "missing_task_id"}

    clickup: ClickUpClient | None = None
    summary: dict[str, Any] | None = None
    issued: dict[str, Any] | None = None
    error_stage = "validacion_clickup"
    try:
        clickup = ClickUpClient(ClickUpSettings.from_env())
        bc = BusinessCentralClient(BusinessCentralSettings.from_env())
        invoice_settings = InvoiceAutomationSettings.from_env()
        demurrage_settings = DemurrageInvoiceSettings.from_env()
        team_id = _resolve_clickup_team_id(clickup)
        use_custom_task_ids = _env_bool("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", default=True)
        task = _fetch_clickup_task_for_webhook(
            clickup=clickup,
            task_id=task_id,
            custom_task_ids=use_custom_task_ids,
            team_id=team_id,
        )
        if task is None:
            return {"status": "ignored", "reason": "task_lookup_failed", "task_id": task_id}

        parent_value = task.get("parent")
        parent_id = (
            str(parent_value.get("id") or "").strip()
            if isinstance(parent_value, dict)
            else str(parent_value or "").strip()
        )
        invoice_task_id = parent_id or str(task.get("id") or "").strip()
        invoice_task = _fetch_clickup_task_for_webhook(
            clickup=clickup,
            task_id=invoice_task_id,
            custom_task_ids=False,
            team_id=team_id,
            include_subtasks=True,
        )
        if parent_id and invoice_task is None:
            blocked = {
                "status": "parent_lookup_failed",
                "message": "No fue posible resolver la tarea madre para validar la factura de demoras.",
            }
            error_comment = _write_invoice_error_comment(
                clickup=clickup,
                clickup_summary={
                    "task_id": str(task.get("id") or task_id),
                    "custom_id": task.get("custom_id"),
                    "name": task.get("name"),
                },
                stage="validacion_clickup",
                invoice_result=blocked,
            )
            result = {
                "status": "blocked",
                "mode": "dry_run",
                "reason": "parent_lookup_failed",
                "task_id": task.get("id"),
                "parent_task_id": parent_id,
                "error_comment": error_comment,
            }
            _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
            return result

        summary = build_clickup_demurrage_invoice_context(
            requested_task=task,
            invoice_task=invoice_task or task,
            demurrage_settings=demurrage_settings,
        )
        preview = prepare_clickup_bc_demurrage_invoice_preview(
            clickup_summary=summary,
            bc_client=bc,
            invoice_settings=invoice_settings,
            demurrage_settings=demurrage_settings,
        )
        apply_mode = _env_bool("CLICKUP_DEMURRAGE_INVOICE_WEBHOOK_APPLY", default=False)
        preview_status = str(preview.get("status") or "").strip()
        apply_eligible_statuses = {"dry_run_ready", "duplicate_invoice"}
        if not apply_mode or preview_status not in apply_eligible_statuses:
            if preview_status not in {"dry_run_ready", "duplicate_invoice", "fully_invoiced"}:
                error_comment = _write_invoice_error_comment(
                    clickup=clickup,
                    clickup_summary=summary,
                    stage="validacion_clickup",
                    invoice_result=preview,
                )
                if error_comment:
                    preview = {**preview, "error_comment": error_comment}
            result = {
                "status": "processed" if preview_status in apply_eligible_statuses else "blocked",
                "mode": "dry_run",
                "task_id": task.get("id"),
                "invoice_owner_task_id": summary.get("task_id"),
                "result": preview,
            }
            _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
            return result

        error_stage = "validacion_clickup"
        validate_invoice_pdf_field_on_task(summary)
        error_stage = "creacion_bc"
        issued = issue_clickup_bc_demurrage_invoice(
            clickup_summary=summary,
            bc_client=bc,
            invoice_settings=invoice_settings,
            demurrage_settings=demurrage_settings,
        )
        if issued.get("status") != "applied":
            error_comment = _write_invoice_error_comment(
                clickup=clickup,
                clickup_summary=summary,
                stage=issued.get("failed_stage") or "creacion_bc",
                invoice_result=issued,
            )
            if error_comment:
                issued = {**issued, "error_comment": error_comment}
            result = {
                "status": "failed",
                "mode": "apply",
                "task_id": task.get("id"),
                "result": issued,
            }
            _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
            return result

        actions = list(issued.get("completed_stages") or [])
        if should_send_invoice_customer_email(str(issued.get("market") or invoice_settings.supported_market)):
            error_stage = "envio_cliente"
            customer_email_delivery = send_issued_invoice_customer_emails(
                bc_client=bc,
                invoice_result=issued,
                settings=invoice_settings,
            )
            issued = {**issued, "customer_email_delivery": customer_email_delivery}
            actions.append("send_customer_email_from_bc")

        error_stage = "entrega_clickup"
        delivery = finalize_clickup_issued_invoices(
            clickup=clickup,
            bc_client=bc,
            clickup_summary=summary,
            invoice_result=issued,
            settings=invoice_settings,
            workspace_id=team_id,
            mark_status=False,
        )
        actions.extend(("upload_invoice_pdf", "comment_invoice_details", "retain_facturada_status"))
        result = {
            "status": "processed",
            "mode": "apply",
            "action": ",".join(actions),
            "task_id": task.get("id"),
            "invoice_owner_task_id": summary.get("task_id"),
            "result": issued,
            "delivery": delivery,
            "final_status_update": None,
        }
        _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Demurrage invoice webhook failed task_id=%s", task_id)
        if clickup is not None and summary is not None:
            failed_result = {
                **(issued or {}),
                "status": "failed_post_creation" if issued else "failed",
                "failed_stage": error_stage,
                "message": str(exc),
            }
            _write_invoice_error_comment(
                clickup=clickup,
                clickup_summary=summary,
                stage=error_stage,
                invoice_result=failed_result,
            )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/clickup/webhooks/inspection-invoice-sync")
@app.post("/clickup/webhooks/inspection-invoice-sync{webhook_path:path}")
@app.post("/clickup/webhooks/inspection-invoice-sync/{webhook_path:path}")
async def clickup_inspection_invoice_sync(
    request: Request,
    webhook_path: str = "",
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    expected_token = _inspection_invoice_webhook_token()
    if not expected_token:
        raise HTTPException(status_code=500, detail="Inspection invoice webhook token is not configured.")
    provided_token = _extract_webhook_token(
        x_webhook_token=x_webhook_token,
        authorization=authorization,
    )
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token.")

    payload = await _safe_json(request)
    task_id = extract_task_id(payload) or extract_task_id_from_path(
        request.url.path,
        base_path="/clickup/webhooks/inspection-invoice-sync",
    )
    if not task_id:
        return {"status": "ignored", "reason": "missing_task_id"}

    try:
        clickup = ClickUpClient(ClickUpSettings.from_env())
        bc = BusinessCentralClient(BusinessCentralSettings.from_env())
        team_id = _resolve_clickup_team_id(clickup)
        use_custom_task_ids = _env_bool("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", default=True)
        task = _fetch_clickup_task_for_webhook(
            clickup=clickup,
            task_id=task_id,
            custom_task_ids=use_custom_task_ids,
            team_id=team_id,
        )
        if task is None:
            return {"status": "ignored", "reason": "task_lookup_failed", "task_id": task_id}

        preview = prepare_inspection_invoice_preview(task=task, bc_client=bc)
        apply_mode = _env_bool("INSPECTION_INVOICE_WEBHOOK_APPLY", default=False)
        if not apply_mode or preview.get("status") != "dry_run_ready":
            result = {
                "status": "processed" if preview.get("status") == "dry_run_ready" else "blocked",
                "mode": "dry_run",
                "task_id": task.get("id"),
                "result": preview,
            }
            _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
            return result

        issued = issue_inspection_invoice(task=task, bc_client=bc)
        if issued.get("status") != "applied":
            result = {"status": "failed", "mode": "apply", "task_id": task.get("id"), "result": issued}
            _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
            return result

        summary = summarize_task_for_customer_mapping(task)
        invoice_settings = InvoiceAutomationSettings.from_env()
        issued, customer_email_action = _deliver_customer_email_if_enabled(
            clickup=clickup,
            bc_client=bc,
            clickup_summary=summary,
            invoice_result=issued,
            settings=invoice_settings,
        )
        if customer_email_action == "failed":
            result = {
                "status": "failed",
                "mode": "apply",
                "task_id": task.get("id"),
                "result": issued,
            }
            _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
            return result

        delivery = finalize_clickup_issued_invoices(
            clickup=clickup,
            bc_client=bc,
            clickup_summary=summary,
            invoice_result=issued,
            settings=invoice_settings,
            workspace_id=team_id,
            mark_status=False,
        )
        writeback = _write_inspection_invoice_number(clickup=clickup, task=task, issued=issued)
        final_status = _mark_inspection_invoice_complete(clickup=clickup, task=task)
        result = {
            "status": "processed",
            "mode": "apply",
            "task_id": task.get("id"),
            "result": issued,
            "delivery": delivery,
            "writeback": writeback,
            "final_status_update": final_status,
            "customer_email_action": customer_email_action,
        }
        _log_webhook_result(task_id=str(task.get("id") or task_id), result=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Inspection invoice webhook failed task_id=%s", task_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/clickup/webhooks/invoice-delivery-recovery")
@app.post("/clickup/webhooks/invoice-sync/deliver-posted")
async def clickup_invoice_deliver_posted(
    request: Request,
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    expected_token = os.getenv("CLICKUP_WEBHOOK_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=500, detail="CLICKUP_WEBHOOK_TOKEN is not configured.")
    provided_token = _extract_webhook_token(
        x_webhook_token=x_webhook_token,
        authorization=authorization,
    )
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token.")

    payload = await _safe_json(request)
    task_id = extract_task_id(payload)
    invoice_numbers = _extract_invoice_numbers(payload)
    if not task_id or not invoice_numbers:
        raise HTTPException(
            status_code=400,
            detail="task_id and invoice_numbers are required for posted-invoice delivery recovery.",
        )

    try:
        clickup = ClickUpClient(ClickUpSettings.from_env())
        bc = BusinessCentralClient(BusinessCentralSettings.from_env())
        settings = InvoiceAutomationSettings.from_env()
        market = settings.supported_market
        team_id = _resolve_clickup_team_id(clickup)
        use_custom_task_ids = _env_bool("CLICKUP_WEBHOOK_CUSTOM_TASK_IDS", default=True)
        task = _fetch_clickup_task_for_webhook(
            clickup=clickup,
            task_id=task_id,
            custom_task_ids=use_custom_task_ids,
            team_id=team_id,
        )
        if task is None:
            raise HTTPException(status_code=404, detail=f"ClickUp task was not found: {task_id}")

        summary = summarize_task_for_customer_mapping(task)
        finalized_invoices = []
        for invoice_number in invoice_numbers:
            posted_invoice = bc.get_posted_sales_invoice_by_number(invoice_number, market=market)
            if not posted_invoice:
                raise HTTPException(
                    status_code=404,
                    detail=f"Business Central posted invoice was not found: {invoice_number}",
                )
            fel_row = bc.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
            fel_status = str((fel_row or {}).get("electronicDocumentStatus") or "").strip()
            if fel_status.lower() != "stamp received":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Invoice {invoice_number} cannot be delivered because FEL status is "
                        f"{fel_status or 'not available'}."
                    ),
                )

            reference = str(posted_invoice.get("externalDocumentNumber") or "").upper()
            invoice_group = (
                "INT-2"
                if "-INT-2" in reference
                else "INT"
                if "-INT" in reference
                else "NAT"
                if "-NAT" in reference
                else "ALL"
            )
            finalized_invoices.append(
                {
                    "invoice_group": invoice_group,
                    "number": invoice_number,
                    "externalDocumentNumber": posted_invoice.get("externalDocumentNumber"),
                    "posted_invoice_after_stamp": {
                        **posted_invoice,
                        "invoice_group": invoice_group,
                    },
                    "custom_api_row_after_stamp": fel_row,
                    "delivery_recovery": True,
                }
            )

        invoice_result = {
            "status": "applied",
            "market": market,
            "task_id": summary.get("task_id"),
            "finalized_invoices": finalized_invoices,
            "created_invoices": [],
            "completed_stages": ["deliver_existing_posted_invoice"],
        }
        invoice_result, customer_email_action = _deliver_customer_email_if_enabled(
            clickup=clickup,
            bc_client=bc,
            clickup_summary=summary,
            invoice_result=invoice_result,
            settings=settings,
        )
        if customer_email_action == "failed":
            response = {
                "status": "failed",
                "action": "comment_invoice_error"
                if invoice_result.get("error_comment")
                else "customer_email_failed",
                "result": invoice_result,
            }
            _log_webhook_result(task_id=task_id, result=response)
            return response

        delivery_result = finalize_clickup_issued_invoices(
            clickup=clickup,
            bc_client=bc,
            clickup_summary=summary,
            invoice_result=invoice_result,
            settings=settings,
            workspace_id=team_id,
            mark_status=True,
        )
        result = {
            **invoice_result,
            "status": "delivered",
            "delivery": delivery_result,
            "final_status_update": delivery_result.get("final_status_update"),
        }
        response = {
            "status": "processed",
            "action": ",".join(
                action
                for action in (
                    "send_customer_email_from_bc" if customer_email_action == "sent" else "",
                    "deliver_existing_posted_invoices",
                )
                if action
            ),
            "result": result,
        }
        _log_webhook_result(task_id=task_id, result=response)
        return response
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - exercised in runtime recovery
        logger.exception("ClickUp posted invoice delivery recovery failed for task_id=%s", task_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _deliver_customer_email_if_enabled(
    *,
    clickup: ClickUpClient,
    bc_client: BusinessCentralClient,
    clickup_summary: dict[str, Any],
    invoice_result: dict[str, Any],
    settings: InvoiceAutomationSettings,
) -> tuple[dict[str, Any], str]:
    market = str(invoice_result.get("market") or settings.supported_market or "").strip().upper()
    if market not in {"GT", "MX"}:
        return invoice_result, "not_applicable"
    if not should_send_invoice_customer_email(market):
        return invoice_result, "disabled"

    try:
        customer_email_delivery = send_issued_invoice_customer_emails(
            bc_client=bc_client,
            invoice_result=invoice_result,
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "Business Central customer email failed after invoice creation market=%s task_id=%s",
            market, clickup_summary.get("task_id"),
        )
        failed_result = {
            **invoice_result,
            "status": "failed_post_creation",
            "failed_stage": "envio_cliente",
            "message": str(exc),
        }
        error_comment = _write_invoice_error_comment(
            clickup=clickup,
            clickup_summary=clickup_summary,
            stage="envio_cliente",
            invoice_result=failed_result,
        )
        if error_comment:
            failed_result = {**failed_result, "error_comment": error_comment}
        return failed_result, "failed"

    return {
        **invoice_result,
        "customer_email_delivery": customer_email_delivery,
    }, "sent"


def _write_invoice_error_comment(
    *,
    clickup: ClickUpClient,
    clickup_summary: dict[str, Any],
    stage: str,
    invoice_result: dict[str, Any],
) -> dict[str, Any] | None:
    comment_text = _build_invoice_error_comment(
        clickup_summary=clickup_summary,
        stage=stage,
        invoice_result=invoice_result,
    )
    try:
        ensure_with_mentions = getattr(clickup, "ensure_task_comment_with_mentions", None)
        if callable(ensure_with_mentions):
            ensured = ensure_with_mentions(
                clickup_summary["task_id"],
                comment_text=comment_text,
                user_ids=_invoice_error_reviewer_user_ids(),
                notify_all=False,
            )
            if isinstance(ensured, dict) and isinstance(ensured.get("comment"), dict):
                return ensured["comment"]
            return ensured

        # Compatibility fallback for older ClickUp adapters. Current production
        # uses the native-mention path above; this still preserves the complete
        # error explanation if an older adapter is supplied during a rollout.
        return clickup.create_task_comment(
            clickup_summary["task_id"],
            comment_text=comment_text,
            notify_all=False,
        )
    except Exception:
        logger.exception(
            "Could not write Spanish invoice error comment task_id=%s stage=%s",
            clickup_summary.get("task_id"),
            stage,
        )
        return None


def _build_invoice_error_comment(
    *,
    clickup_summary: dict[str, Any],
    stage: str,
    invoice_result: dict[str, Any],
) -> str:
    stage_label = {
        "validacion_clickup": "VALIDACION DE CLICKUP ANTES DE CREAR LA FACTURA",
        "creacion_bc": "CREACION DE LA FACTURA EN BUSINESS CENTRAL",
        "create_sales_invoice": "CREACION DE LA FACTURA EN BUSINESS CENTRAL",
        "post_sales_invoice": "REGISTRO/POSTEO DE LA FACTURA EN BUSINESS CENTRAL",
        "sync_fel_descriptions": "SINCRONIZACION DE DESCRIPCIONES FEL",
        "stamp_fel_invoice": "TIMBRADO FEL/SAT",
        "envio_cliente": "ENVIO DE FACTURA AL CLIENTE DESDE BUSINESS CENTRAL",
        "entrega_clickup": "ENTREGA DE PDF Y REFERENCIAS EN CLICKUP",
    }.get(stage, stage.replace("_", " ").upper())
    status = str(invoice_result.get("status") or "error").strip()
    message = _truncate_comment_value(str(invoice_result.get("message") or "Sin detalle tecnico."))
    reference = str(
        invoice_result.get("reference")
        or clickup_summary.get("custom_id")
        or clickup_summary.get("name")
        or clickup_summary.get("task_id")
        or ""
    ).strip()
    invoice_numbers = _invoice_numbers_from_result(invoice_result)
    invoice_line = f"\nFACTURAS BC: {', '.join(invoice_numbers)}" if invoice_numbers else ""

    return (
        "ERROR EN PROCESO DE FACTURACION\n"
        f"TAREA: {reference or 'NO DISPONIBLE'}\n"
        f"ETAPA: {stage_label}\n"
        f"ESTADO DEL PROCESO: {status}\n"
        f"DETALLE: {message}"
        f"{invoice_line}\n\n"
        "REVISION ASIGNADA: CONSUELO DE VELASQUEZ Y CARLOS HUERTA.\n"
        "ACCION REQUERIDA: REVISAR EL DETALLE Y CORREGIR LA CAUSA. NO REINTENTAR LA EMISION "
        "SIN AUDITAR PRIMERO BUSINESS CENTRAL, FEL/SAT, CORREO Y CLICKUP. SI LA CAUSA REQUIERE "
        "UN CAMBIO DE CODIGO, CORREGIRLO Y SINCRONIZAR LA MISMA REVISION EN LOCAL, AWS Y GITHUB "
        "ANTES DE REEJECUTAR. LA AUTOMATIZACION NO DEBE CONSIDERARSE COMPLETA HASTA QUE "
        "LOS PDF ESTEN EN EL CAMPO INVOICE TO CLIENT, EL COMENTARIO CON REFERENCIAS BC EXISTA "
        "Y EL ESTATUS QUEDE EN FACTURADA."
    )


def _invoice_error_reviewer_user_ids() -> tuple[int, ...]:
    raw_value = os.getenv(
        "CLICKUP_INVOICE_ERROR_REVIEWER_USER_IDS",
        "89253188,61521165",
    )
    reviewer_ids: list[int] = []
    for raw_id in raw_value.split(","):
        candidate = raw_id.strip()
        if not candidate:
            continue
        try:
            reviewer_id = int(candidate)
        except ValueError:
            logger.error("Ignoring invalid ClickUp invoice error reviewer id: %s", candidate)
            continue
        if reviewer_id > 0 and reviewer_id not in reviewer_ids:
            reviewer_ids.append(reviewer_id)
    return tuple(reviewer_ids) or (89253188, 61521165)


def _invoice_numbers_from_result(invoice_result: dict[str, Any]) -> list[str]:
    numbers: list[str] = []
    for key in ("created_invoices", "posted_invoices"):
        for invoice in invoice_result.get(key) or []:
            if isinstance(invoice, dict) and invoice.get("number"):
                numbers.append(str(invoice["number"]))
    for invoice in invoice_result.get("finalized_invoices") or []:
        if not isinstance(invoice, dict):
            continue
        number = invoice.get("number") or (invoice.get("posted_invoice_after_stamp") or {}).get("number")
        if number:
            numbers.append(str(number))
    return list(dict.fromkeys(numbers))


def _truncate_comment_value(value: str, *, max_length: int = 1200) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 3].rstrip() + "..."


def _fetch_clickup_task_for_webhook(
    *,
    clickup: ClickUpClient,
    task_id: str,
    custom_task_ids: bool,
    team_id: str | None,
    include_subtasks: bool = False,
) -> dict[str, Any] | None:
    attempts: list[tuple[bool, str | None]] = []
    primary_team_id = team_id or clickup.settings.default_workspace_id
    attempts.append((custom_task_ids, primary_team_id))
    if custom_task_ids and primary_team_id is None:
        inferred_team_id = _infer_clickup_team_id(clickup)
        if inferred_team_id:
            attempts.append((True, inferred_team_id))
    attempts.append((False, None))

    seen: set[tuple[bool, str | None]] = set()
    for attempt_custom_ids, attempt_team_id in attempts:
        key = (attempt_custom_ids, attempt_team_id)
        if key in seen:
            continue
        seen.add(key)
        try:
            return clickup.get_task(
                task_id,
                custom_task_ids=attempt_custom_ids,
                team_id=attempt_team_id,
                include_subtasks=include_subtasks,
            )
        except Exception:
            logger.exception(
                "ClickUp task lookup failed for task_id=%s custom_task_ids=%s team_id=%s",
                task_id,
                attempt_custom_ids,
                attempt_team_id,
            )
    return None


def _resolve_clickup_team_id(clickup: ClickUpClient) -> str | None:
    explicit_team_id = os.getenv("CLICKUP_WEBHOOK_TEAM_ID", "").strip() or None
    if explicit_team_id:
        return explicit_team_id
    return clickup.settings.default_workspace_id or _infer_clickup_team_id(clickup)


def _infer_clickup_team_id(clickup: ClickUpClient) -> str | None:
    try:
        workspaces = clickup.get_authorized_workspaces()
    except Exception:
        logger.exception("Unable to infer ClickUp team id from authorized workspaces.")
        return None

    teams = workspaces.get("teams") or []
    if len(teams) == 1:
        team_id = teams[0].get("id")
        return str(team_id) if team_id is not None else None

    default_workspace_id = clickup.settings.default_workspace_id
    if default_workspace_id:
        return default_workspace_id

    return None


def extract_task_id(payload: dict[str, Any]) -> str | None:
    candidates = [
        payload.get("Task ID"),
        payload.get("task_id"),
        payload.get("taskId"),
        payload.get("task", {}).get("id") if isinstance(payload.get("task"), dict) else None,
    ]
    for candidate in candidates:
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return None


def _extract_invoice_numbers(payload: dict[str, Any]) -> list[str]:
    raw_value = (
        payload.get("invoice_numbers")
        or payload.get("invoiceNumbers")
        or payload.get("Invoice Numbers")
        or payload.get("invoice_number")
        or payload.get("invoiceNumber")
    )
    if raw_value is None:
        return []

    values = raw_value if isinstance(raw_value, list) else str(raw_value).replace(";", ",").split(",")
    invoice_numbers: list[str] = []
    market = os.getenv("CLICKUP_INVOICE_MARKET", "GT").strip().upper() or "GT"
    prefixes = _posted_invoice_number_prefixes_for_market(market)
    for value in values:
        text = str(value or "").strip().upper()
        if not text:
            continue
        if not any(text.startswith(prefix) for prefix in prefixes):
            raise HTTPException(
                status_code=400,
                detail=f"Only posted {market} invoice numbers are supported for delivery recovery: {text}",
            )
        invoice_numbers.append(text)
    return list(dict.fromkeys(invoice_numbers))


def _posted_invoice_number_prefixes_for_market(market: str) -> tuple[str, ...]:
    raw_value = os.getenv(f"BC_MARKET_{market}_POSTED_INVOICE_PREFIXES", "").strip()
    if raw_value:
        return tuple(part.strip().upper() for part in raw_value.split(",") if part.strip())
    if market == "MX":
        return ("B",)
    return ("GTFVR",)


def _write_inspection_invoice_number(
    *,
    clickup: ClickUpClient,
    task: dict[str, Any],
    issued: dict[str, Any],
) -> dict[str, Any]:
    finalized = (issued.get("finalized_invoices") or [{}])[0]
    invoice = finalized.get("posted_invoice_after_stamp") or {}
    invoice_number = str(invoice.get("number") or finalized.get("number") or "").strip()
    invoice_id = str(invoice.get("id") or "").strip()
    updates: list[dict[str, Any]] = []
    for env_name, value in (
        ("INSPECTION_INVOICE_BC_INVOICE_NUMBER_FIELD_ID", invoice_number),
        ("INSPECTION_INVOICE_BC_INVOICE_ID_FIELD_ID", invoice_id),
    ):
        field_id = os.getenv(env_name, "").strip()
        if field_id and value:
            updates.append(clickup.set_task_custom_field_value(str(task["id"]), field_id, value))
    return {"invoice_number": invoice_number or None, "invoice_id": invoice_id or None, "updates": updates}


def _mark_inspection_invoice_complete(*, clickup: ClickUpClient, task: dict[str, Any]) -> dict[str, Any] | None:
    target_status = os.getenv("INSPECTION_INVOICE_FINAL_STATUS", "").strip()
    if not target_status:
        return None
    return clickup.update_task(str(task["id"]), status=target_status)


def extract_task_id_from_path(path: str, *, base_path: str) -> str | None:
    normalized_path = path.rstrip("/")
    normalized_base = base_path.rstrip("/")
    if not normalized_path.startswith(normalized_base):
        return None

    suffix = normalized_path[len(normalized_base) :].lstrip("/")
    if not suffix:
        return None

    first_segment = suffix.split("/", 1)[0].strip()
    if not first_segment:
        return None

    # ClickUp may double-encode path variables; decode conservatively.
    decoded_segment = unquote(unquote(first_segment)).strip()
    return decoded_segment or None


def _resolve_clickup_match_status(custom_fields: dict[str, Any]) -> str | None:
    field = custom_fields.get("BC Match Status") or {}
    value = field.get("value")
    type_config = field.get("type_config") or {}
    for option in type_config.get("options", []):
        if option.get("id") == value or option.get("orderindex") == value:
            return option.get("name")
        if value is not None and str(option.get("id")) == str(value):
            return option.get("name")
        if value is not None and str(option.get("orderindex")) == str(value):
            return option.get("name")
    return None


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def _safe_json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}

    if isinstance(payload, dict):
        return payload
    return {}


async def _safe_form_urlencoded(request: Request) -> dict[str, str]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/x-www-form-urlencoded" not in content_type:
        return {}

    body = await request.body()
    if not body:
        return {}

    return {
        key: value
        for key, value in parse_qsl(body.decode("utf-8"), keep_blank_values=True)
    }


def _extract_webhook_token(
    *,
    x_webhook_token: str | None,
    authorization: str | None,
) -> str | None:
    if x_webhook_token:
        return x_webhook_token.strip()

    if not authorization:
        return None

    value = authorization.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def _storage_invoice_webhook_token() -> str:
    """Use an isolated credential for supplemental Almacenaje invoices when configured."""
    return (
        os.getenv("CLICKUP_STORAGE_INVOICE_WEBHOOK_TOKEN", "").strip()
        or os.getenv("CLICKUP_WEBHOOK_TOKEN", "").strip()
    )


def _inspection_invoice_webhook_token() -> str:
    """Prefer an isolated credential for Magna inspection invoices."""
    return (
        os.getenv("INSPECTION_INVOICE_WEBHOOK_TOKEN", "").strip()
        or os.getenv("CLICKUP_WEBHOOK_TOKEN", "").strip()
    )


def _demurrage_invoice_webhook_token() -> str:
    """Use an isolated credential for supplemental D&D invoices when configured."""
    return (
        os.getenv("CLICKUP_DEMURRAGE_INVOICE_WEBHOOK_TOKEN", "").strip()
        or os.getenv("CLICKUP_WEBHOOK_TOKEN", "").strip()
    )


def _apply_clickup_customer_writeback(*, clickup: ClickUpClient, writeback: dict[str, Any]) -> None:
    clickup.set_task_custom_field_value(
        writeback["task_id"],
        writeback["field_ids"]["number"],
        writeback["bc_customer_number"],
    )
    clickup.set_task_custom_field_value(
        writeback["task_id"],
        writeback["field_ids"]["id"],
        writeback["bc_customer_id"],
    )
    clickup.set_task_custom_field_value(
        writeback["task_id"],
        writeback["field_ids"]["link"],
        writeback["bc_customer_link"],
    )
    clickup.set_task_custom_field_value(
        writeback["task_id"],
        writeback["field_ids"]["legal_name"],
        writeback["bc_legal_name"],
    )
    clickup.set_task_custom_field_value(
        writeback["task_id"],
        writeback["field_ids"]["status"],
        writeback["bc_match_status"],
    )


def _apply_clickup_invoice_writeback(*, clickup: ClickUpClient, writeback: dict[str, Any]) -> None:
    invoice_number_field_id = writeback.get("field_ids", {}).get("invoice_number")
    if invoice_number_field_id and writeback.get("bc_invoice_number") is not None:
        clickup.set_task_custom_field_value(
            writeback["task_id"],
            invoice_number_field_id,
            writeback["bc_invoice_number"],
        )

    invoice_id_field_id = writeback.get("field_ids", {}).get("invoice_id")
    if invoice_id_field_id and writeback.get("bc_invoice_id") is not None:
        clickup.set_task_custom_field_value(
            writeback["task_id"],
            invoice_id_field_id,
            writeback["bc_invoice_id"],
        )


def _with_updated_custom_field_value(
    summary: dict[str, Any],
    *,
    field_id: str | int | None,
    value: str | int | None,
) -> dict[str, Any]:
    if field_id is None or value is None:
        return summary
    custom_fields = summary.get("custom_fields") or {}
    updated_fields = {}
    for field_name, details in custom_fields.items():
        if details.get("id") == field_id:
            updated_fields[field_name] = {**details, "value": value}
        else:
            updated_fields[field_name] = details
    return {**summary, "custom_fields": updated_fields}


def _log_webhook_result(*, task_id: str | None, result: dict[str, Any]) -> None:
    result_payload = result.get("result") if isinstance(result.get("result"), dict) else {}
    logger.info(
        "Webhook result task_id=%s status=%s action=%s reason=%s task_status=%s result_status=%s result_message=%s status_source=%s",
        task_id,
        result.get("status"),
        result.get("action"),
        result.get("reason"),
        result.get("task_status") or result_payload.get("task_status"),
        result_payload.get("status"),
        result_payload.get("message"),
        result_payload.get("status_source"),
    )


def _status_equals(left: str | None, right: str | None) -> bool:
    return " ".join((left or "").strip().lower().split()) == " ".join((right or "").strip().lower().split())
