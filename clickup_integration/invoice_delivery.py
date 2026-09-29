from __future__ import annotations

import logging
import os
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from business_central_client.client import BusinessCentralClient
from clickup_integration.client import ClickUpClient
from clickup_integration.invoice_sync import InvoiceAutomationSettings
from clickup_integration.tagomago_boundary import generic_invoice_tagomago_blocker


logger = logging.getLogger(__name__)

DEFAULT_INVOICE_PDF_FIELD_ID = "5d67859a-1ae0-4cda-9f57-2a89bf1ff259"
DEFAULT_REQUIRED_PDF_TEXT = (
    "FACTURA ELECTRONICA",
    "DOCUMENTO TRIBUTARIO ELECTRONICO",
    "INFORMACION DE EMBARQUE",
    "SERIE INTERNA",
    "NO. INTERNO",
)
DEFAULT_FORBIDDEN_PDF_TEXT = (
    "FACTRURA",
    "SALDO PENDIENTE",
    "REPORT.FEEL.COM.GT",
)
DEFAULT_MX_REQUIRED_PDF_TEXT = (
    "CFDI",
    "Folio Fiscal",
    "Sello CFDI",
    "Sello SAT",
    "Este documento es una representación impresa de un CFDI",
)


class ClickUpInvoiceFieldDeliveryError(RuntimeError):
    """The dedicated ClickUp invoice file field could not accept the PDF."""


def resolve_invoice_pdf_field_id() -> str:
    pdf_field_id = os.getenv("CLICKUP_INVOICE_PDF_FIELD_ID", DEFAULT_INVOICE_PDF_FIELD_ID).strip()
    if not pdf_field_id:
        raise ValueError("CLICKUP_INVOICE_PDF_FIELD_ID is required for invoice PDF upload.")
    return pdf_field_id


def should_validate_invoice_pdf_layout() -> bool:
    raw_value = os.getenv("CLICKUP_INVOICE_VALIDATE_PDF_LAYOUT", "true").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def should_send_invoice_customer_email() -> bool:
    """Control guarded native BC customer delivery for Guatemala invoice flows."""
    raw_value = os.getenv("CLICKUP_INVOICE_SEND_ENABLED", "false").strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def send_issued_invoice_customer_emails(
    *,
    bc_client: BusinessCentralClient,
    invoice_result: dict[str, Any],
    settings: InvoiceAutomationSettings,
) -> dict[str, Any]:
    """Validate the BC PDF, then submit each FEL-stamped invoice through BC Email."""
    blocker = generic_invoice_tagomago_blocker(
        invoice_result=invoice_result,
        market=str(invoice_result.get("market") or settings.supported_market),
        customer_field_names=settings.bc_customer_number_field_names,
    )
    if blocker:
        raise ValueError(blocker["message"])
    if str(invoice_result.get("market") or settings.supported_market).upper() == "MX":
        raise ValueError(
            "Mexico invoice email requires the native Mexico delivery route; "
            "SendApprovedInvoiceEmail renders the Guatemala report."
        )
    created_invoices = delivery_invoices_from_result(invoice_result)
    market = _resolve_delivery_market(invoice_result, settings=settings)
    finalized_by_number = {
        str((finalized.get("posted_invoice_after_stamp") or {}).get("number") or "").strip(): finalized
        for finalized in invoice_result.get("finalized_invoices") or []
        if isinstance(finalized, dict)
    }
    deliveries: list[dict[str, Any]] = []

    for invoice in created_invoices:
        invoice_id = str(invoice.get("id") or "").strip()
        invoice_number = str(invoice.get("number") or "").strip()
        if not invoice_id or not invoice_number:
            raise ValueError("Invoice email requires a posted Business Central invoice id and number.")
        finalized = finalized_by_number.get(invoice_number) or {}
        fel_row = finalized.get("custom_api_row_after_stamp") or {}
        fel_row_id = str(fel_row.get("id") or "").strip()
        if not fel_row_id:
            fel_row = bc_client.get_posted_invoice_fel_description_by_number(invoice_number, market=market) or {}
            fel_row_id = str(fel_row.get("id") or "").strip()
        if not fel_row_id:
            raise ValueError(f"Business Central FEL API row was not found for invoice {invoice_number}.")

        pdf_content = _download_invoice_pdf_with_retry(
            bc_client=bc_client,
            invoice_id=invoice_id,
            market=market,
        )
        validate_invoice_pdf_layout(
            pdf_content,
            invoice_number=invoice_number,
            invoice_group=str(invoice.get("invoice_group") or ""),
            market=market,
        )
        bc_client.send_posted_invoice_customer_email(fel_row_id, market=market)
        # The standard salesInvoices API id is not the posted Sales Invoice Header
        # SystemId used by the extension audit table. The FEL API row is keyed by
        # that posted SystemId, so use it for the authoritative delivery readback.
        audit = bc_client.get_invoice_email_delivery_by_posted_invoice_id(
            fel_row_id,
            market=market,
        )
        audit_status = str((audit or {}).get("status") or "").strip().lower()
        if audit_status != "sent":
            raise ValueError(
                f"Business Central did not confirm customer email submission for {invoice_number}. "
                f"Audit status: {audit_status or 'missing'}. "
                f"Detail: {(audit or {}).get('errorText') or 'none'}."
            )
        audit_sender = str((audit or {}).get("senderEmail") or "").strip().lower()
        if audit_sender != "consuelo@mtmlogix.com":
            raise ValueError(
                f"Business Central confirmed an unexpected sender for {invoice_number}: "
                f"{audit_sender or 'missing'}."
            )
        if not str((audit or {}).get("bcEmailMessageId") or "").strip():
            raise ValueError(
                f"Business Central did not return native email message evidence for {invoice_number}."
            )
        if (audit or {}).get("nativeSentVerified") is not True:
            raise ValueError(
                f"Business Central did not verify the native Sent Email record for {invoice_number}."
            )
        deliveries.append({"invoice_number": invoice_number, "invoice_id": invoice_id, "audit": audit})

    return {
        "status": "sent",
        "sender": "consuelo@mtmlogix.com",
        "delivery_provider": "business_central_email_scenario",
        "deliveries": deliveries,
    }


def validate_invoice_pdf_field_on_task(
    clickup_summary: dict[str, Any],
    *,
    field_id: str | None = None,
) -> dict[str, Any]:
    resolved_field_id = field_id or resolve_invoice_pdf_field_id()
    field = _find_custom_field_by_id(clickup_summary.get("custom_fields") or {}, field_id=resolved_field_id)
    if not field:
        raise ValueError(
            "ClickUp task does not expose the Invoice to Client custom field "
            f"({resolved_field_id}). Confirm the token user has access to the field's parent "
            "space/folder/list before issuing invoices."
        )
    return field


def finalize_clickup_issued_invoices(
    *,
    clickup: ClickUpClient,
    bc_client: BusinessCentralClient,
    clickup_summary: dict[str, Any],
    invoice_result: dict[str, Any],
    settings: InvoiceAutomationSettings,
    workspace_id: str | None,
    mark_status: bool = True,
) -> dict[str, Any]:
    blocker = generic_invoice_tagomago_blocker(
        clickup_summary=clickup_summary,
        invoice_result=invoice_result,
        market=str(invoice_result.get("market") or settings.supported_market),
        customer_field_names=settings.bc_customer_number_field_names,
    )
    if blocker:
        raise ValueError(blocker["message"])
    created_invoices = delivery_invoices_from_result(invoice_result)

    market = _resolve_delivery_market(invoice_result, settings=settings)
    resolved_workspace_id = workspace_id or clickup.settings.default_workspace_id
    if not resolved_workspace_id:
        raise ValueError("A ClickUp workspace ID is required to upload invoice PDFs to a custom field.")

    pdf_field_id = resolve_invoice_pdf_field_id()
    field_delivery_error: Exception | None = None
    try:
        validate_invoice_pdf_field_on_task(clickup_summary, field_id=pdf_field_id)
    except ValueError as exc:
        if "does not expose the Invoice to Client custom field" not in str(exc):
            raise
        field_delivery_error = exc

    delivery_mode = "invoice_to_client_field"
    if field_delivery_error is None:
        try:
            uploaded_documents, pdf_field_update = _upload_invoice_pdfs_to_clickup_field(
                clickup=clickup,
                bc_client=bc_client,
                task_id=clickup_summary["task_id"],
                workspace_id=resolved_workspace_id,
                field_id=pdf_field_id,
                created_invoices=created_invoices,
                market=market,
                existing_attachment_ids=_existing_file_field_attachment_ids(
                    clickup_summary.get("custom_fields") or {},
                    field_id=pdf_field_id,
                ),
            )
        except ClickUpInvoiceFieldDeliveryError as exc:
            field_delivery_error = exc

    if field_delivery_error is not None:
        delivery_mode = "task_attachment_comment_fallback"
        logger.warning(
            "Invoice to Client delivery failed for ClickUp task %s; using task attachments.",
            clickup_summary["task_id"],
        )
        uploaded_documents = _upload_invoice_pdfs_to_clickup_task(
            clickup=clickup,
            bc_client=bc_client,
            task_id=clickup_summary["task_id"],
            created_invoices=created_invoices,
            market=market,
        )
        pdf_field_update = {
            "status": "fallback_task_attachments",
            "field_id": pdf_field_id,
            "reason": _safe_delivery_error(field_delivery_error),
        }
    comment_text = build_issued_invoice_comment(
        bc_client=bc_client,
        market=market,
        created_invoices=created_invoices,
        uploaded_documents=uploaded_documents,
        delivery_mode=delivery_mode,
    )
    comment = clickup.create_task_comment(
        clickup_summary["task_id"],
        comment_text=comment_text,
        notify_all=False,
    )
    final_status_update = None
    if mark_status:
        final_status_update = mark_clickup_invoice_facturada(
            clickup=clickup,
            clickup_summary=clickup_summary,
            settings=settings,
        )

    return {
        "pdf_field_id": pdf_field_id,
        "delivery_mode": delivery_mode,
        "uploaded_documents": uploaded_documents,
        "pdf_field_update": pdf_field_update,
        "comment": comment,
        "comment_text": comment_text,
        "final_status_update": final_status_update,
    }


def delivery_invoices_from_result(invoice_result: dict[str, Any]) -> list[dict[str, Any]]:
    finalized_invoices = invoice_result.get("finalized_invoices") or []
    delivery_invoices: list[dict[str, Any]] = []
    for finalized in finalized_invoices:
        if not isinstance(finalized, dict):
            continue
        invoice = finalized.get("posted_invoice_after_stamp") or {}
        stamp_row = finalized.get("custom_api_row_after_stamp") or {}
        stamp_status = str(stamp_row.get("electronicDocumentStatus") or "").strip().lower()
        if stamp_status != "stamp received":
            raise ValueError(
                "Invoice delivery requires every posted invoice to have FEL status Stamp Received."
            )
        if not invoice.get("id") or not invoice.get("number"):
            raise ValueError("Invoice delivery result is missing a posted invoice id or number.")
        delivery_invoices.append(
            {
                **invoice,
                "invoice_group": finalized.get("invoice_group") or invoice.get("invoice_group"),
                "externalDocumentNumber": finalized.get("externalDocumentNumber")
                or invoice.get("externalDocumentNumber"),
            }
        )

    if delivery_invoices:
        return delivery_invoices

    created_invoices = invoice_result.get("created_invoices") or []
    if invoice_result.get("status") == "applied" and created_invoices:
        return list(created_invoices)

    raise ValueError(
        "Invoice delivery requires either an applied invoice result with created_invoices "
        "or a finalized posted-invoice result with FEL status Stamp Received."
    )


def _resolve_delivery_market(
    invoice_result: dict[str, Any],
    *,
    settings: InvoiceAutomationSettings,
) -> str:
    candidates = [
        invoice_result.get("market"),
        (invoice_result.get("preview") or {}).get("market")
        if isinstance(invoice_result.get("preview"), dict)
        else None,
        (invoice_result.get("live_summary") or {}).get("market")
        if isinstance(invoice_result.get("live_summary"), dict)
        else None,
        settings.supported_market,
    ]
    for candidate in candidates:
        value = str(candidate or "").strip().upper()
        if value:
            return value
    raise ValueError("Invoice delivery could not resolve the Business Central market.")


def mark_clickup_invoice_facturada(
    *,
    clickup: ClickUpClient,
    clickup_summary: dict[str, Any],
    settings: InvoiceAutomationSettings,
) -> dict[str, Any]:
    return _set_invoice_status(
        clickup=clickup,
        clickup_summary=clickup_summary,
        settings=settings,
        target_status=os.getenv("CLICKUP_INVOICE_FINAL_STATUS", "Facturada").strip() or "Facturada",
    )


def build_issued_invoice_comment(
    *,
    bc_client: BusinessCentralClient,
    market: str,
    created_invoices: list[dict[str, Any]],
    uploaded_documents: list[dict[str, Any]],
    delivery_mode: str = "invoice_to_client_field",
) -> str:
    company = bc_client.get_company_metadata(market=market)
    company_name = (company or {}).get("name") or (company or {}).get("displayName") or ""
    uploaded_by_invoice_id = {
        document.get("invoice_id"): document for document in uploaded_documents if document.get("invoice_id")
    }

    lines = ["Business Central invoices issued:"]
    if delivery_mode == "task_attachment_comment_fallback":
        lines.extend(
            [
                "",
                "ENTREGA EN CLICKUP: LOS PDF CERTIFICADOS FUERON ADJUNTADOS DIRECTAMENTE A LA TAREA.",
                "EL CAMPO INVOICE TO CLIENT NO ESTABA DISPONIBLE PARA ESTA OPERACION.",
            ]
        )
    for invoice in created_invoices:
        invoice_group = str(invoice.get("invoice_group") or "ALL").upper()
        invoice_number = invoice.get("number") or ""
        invoice_id = invoice.get("id") or ""
        link = (
            bc_client.build_sales_invoice_url(company_name=company_name, invoice_number=str(invoice_number))
            if company_name and invoice_number
            else ""
        )
        uploaded = uploaded_by_invoice_id.get(invoice_id) or {}
        lines.extend(
            [
                "",
                f"{invoice_group} invoice",
                f"- Number: {invoice_number}",
                f"- ID: {invoice_id}",
                f"- Link: {link or 'Unavailable'}",
                f"- PDF: {_uploaded_document_reference(uploaded)}",
            ]
        )
    return "\n".join(lines)


def _upload_invoice_pdfs_to_clickup_field(
    *,
    clickup: ClickUpClient,
    bc_client: BusinessCentralClient,
    task_id: str,
    workspace_id: str,
    field_id: str,
    created_invoices: list[dict[str, Any]],
    market: str,
    existing_attachment_ids: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    uploaded_documents: list[dict[str, Any]] = []
    new_attachment_ids: list[str] = []
    for invoice in created_invoices:
        invoice_id = str(invoice.get("id") or "").strip()
        if not invoice_id:
            raise ValueError("Created invoice is missing its Business Central id.")

        invoice_number = str(invoice.get("number") or invoice_id).strip()
        invoice_group = str(invoice.get("invoice_group") or "").strip().upper()
        file_stem = invoice.get("externalDocumentNumber") or invoice_number
        file_name = f"{file_stem}.pdf"
        pdf_content = _download_invoice_pdf_with_retry(
            bc_client=bc_client,
            invoice_id=invoice_id,
            market=market,
        )
        layout_validation = validate_invoice_pdf_layout(
            pdf_content,
            invoice_number=invoice_number,
            invoice_group=invoice_group,
            market=market,
        )
        temp_path = _write_temp_pdf(pdf_content)
        try:
            try:
                upload_result = clickup.upload_custom_field_attachment(
                    workspace_id,
                    field_id,
                    temp_path,
                    file_name=file_name,
                    mime_type="application/pdf",
                )
            except Exception as exc:
                raise ClickUpInvoiceFieldDeliveryError(
                    f"Could not upload {file_name} to the Invoice to Client field."
                ) from exc
        finally:
            temp_path.unlink(missing_ok=True)

        attachment_id = _extract_attachment_id(upload_result)
        if not attachment_id:
            raise ClickUpInvoiceFieldDeliveryError(
                f"ClickUp did not return an attachment id for {file_name}."
            )
        new_attachment_ids.append(attachment_id)
        uploaded_documents.append(
            {
                "invoice_group": invoice_group,
                "invoice_id": invoice_id,
                "invoice_number": invoice_number,
                "file_name": file_name,
                "attachment_id": attachment_id,
                "pdf_source": "business_central_salesInvoices_pdfDocument",
                "layout_validation": layout_validation,
                "upload_result": upload_result,
            }
        )

    combined_attachment_ids = [*existing_attachment_ids, *new_attachment_ids]
    # A Files custom field appends values when set. Clear it first so a
    # reissue replaces the cancelled PDF instead of leaving a stale document.
    try:
        clickup.clear_task_custom_field_value(task_id, field_id)
    except Exception as exc:
        raise ClickUpInvoiceFieldDeliveryError(
            "Could not clear the Invoice to Client field before replacing its PDFs."
        ) from exc
    try:
        pdf_field_update = clickup.set_task_file_custom_field_attachments(
            task_id,
            field_id,
            combined_attachment_ids,
        )
    except Exception:
        # Preserve the prior active documents if ClickUp fails after the clear.
        if existing_attachment_ids:
            try:
                clickup.set_task_file_custom_field_attachments(
                    task_id,
                    field_id,
                    existing_attachment_ids,
                )
            except Exception:  # pragma: no cover - surface the original failure
                logger.exception("Could not restore ClickUp Invoice to Client attachments.")
        raise ClickUpInvoiceFieldDeliveryError(
            "Could not set the final Invoice to Client attachment list."
        )
    return uploaded_documents, pdf_field_update


def _upload_invoice_pdfs_to_clickup_task(
    *,
    clickup: ClickUpClient,
    bc_client: BusinessCentralClient,
    task_id: str,
    created_invoices: list[dict[str, Any]],
    market: str,
) -> list[dict[str, Any]]:
    uploaded_documents: list[dict[str, Any]] = []
    for invoice in created_invoices:
        invoice_id = str(invoice.get("id") or "").strip()
        if not invoice_id:
            raise ValueError("Created invoice is missing its Business Central id.")
        invoice_number = str(invoice.get("number") or invoice_id).strip()
        invoice_group = str(invoice.get("invoice_group") or "").strip().upper()
        file_stem = invoice.get("externalDocumentNumber") or invoice_number
        file_name = f"{file_stem}.pdf"
        pdf_content = _download_invoice_pdf_with_retry(
            bc_client=bc_client,
            invoice_id=invoice_id,
            market=market,
        )
        layout_validation = validate_invoice_pdf_layout(
            pdf_content,
            invoice_number=invoice_number,
            invoice_group=invoice_group,
            market=market,
        )
        temp_path = _write_temp_pdf(pdf_content)
        try:
            upload_result = clickup.attach_file_to_task(
                task_id,
                temp_path,
                file_name=file_name,
                mime_type="application/pdf",
            )
        finally:
            temp_path.unlink(missing_ok=True)
        attachment_id = _extract_attachment_id(upload_result)
        if not attachment_id:
            raise ValueError(f"ClickUp did not return a task attachment id for {file_name}.")
        uploaded_documents.append(
            {
                "invoice_group": invoice_group,
                "invoice_id": invoice_id,
                "invoice_number": invoice_number,
                "file_name": file_name,
                "attachment_id": attachment_id,
                "attachment_url": _extract_attachment_url(upload_result),
                "pdf_source": "business_central_salesInvoices_pdfDocument",
                "delivery_destination": "clickup_task_attachment",
                "layout_validation": layout_validation,
                "upload_result": upload_result,
            }
        )
    return uploaded_documents


def _download_invoice_pdf_with_retry(
    *,
    bc_client: BusinessCentralClient,
    invoice_id: str,
    market: str,
    attempts: int = 3,
    delay_seconds: float = 2.0,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            pdf_content = bc_client.get_sales_invoice_pdf_content(invoice_id, market=market)
            if not pdf_content:
                raise ValueError(f"Business Central returned an empty PDF for invoice {invoice_id}.")
            return pdf_content
        except Exception as exc:  # pragma: no cover - retry behavior is runtime defensive code
            last_error = exc
            if attempt == attempts:
                break
            logger.info(
                "BC invoice PDF not ready invoice_id=%s attempt=%s/%s error=%s",
                invoice_id,
                attempt,
                attempts,
                exc,
            )
            time.sleep(delay_seconds)
    raise RuntimeError(f"Could not download Business Central invoice PDF for {invoice_id}: {last_error}")


def validate_invoice_pdf_layout(
    pdf_content: bytes,
    *,
    invoice_number: str,
    invoice_group: str,
    market: str | None = None,
) -> dict[str, Any]:
    if not should_validate_invoice_pdf_layout():
        return {"status": "skipped", "reason": "CLICKUP_INVOICE_VALIDATE_PDF_LAYOUT disabled"}

    normalized_market = str(market or "").strip().upper()
    text = _normalize_pdf_text(_extract_invoice_pdf_text(pdf_content))
    required = _configured_pdf_text_markers(
        _market_pdf_env_name("CLICKUP_INVOICE_PDF_REQUIRED_TEXT", normalized_market),
        _default_required_pdf_text(normalized_market),
    )
    forbidden = _configured_pdf_text_markers(
        _market_pdf_env_name("CLICKUP_INVOICE_PDF_FORBIDDEN_TEXT", normalized_market),
        _default_forbidden_pdf_text(normalized_market),
    )
    missing = [marker for marker in required if _normalize_pdf_text(marker) not in text]
    present_forbidden = [marker for marker in forbidden if _normalize_pdf_text(marker) in text]

    result = {
        "status": "passed" if not missing and not present_forbidden else "failed",
        "invoice_number": invoice_number,
        "invoice_group": invoice_group,
        "market": normalized_market or None,
        "required_markers": required,
        "missing_markers": missing,
        "forbidden_markers": forbidden,
        "present_forbidden_markers": present_forbidden,
    }
    if result["status"] == "failed":
        market_label = normalized_market or "GT"
        raise ValueError(
            f"Business Central invoice PDF did not match the approved MTM {market_label} invoice form "
            f"for {invoice_number}. Missing markers: {missing or 'none'}. "
            f"Forbidden markers: {present_forbidden or 'none'}."
        )
    return result


def _extract_invoice_pdf_text(pdf_content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - production dependency guard
        raise RuntimeError(
            "pypdf is required to validate invoice PDFs before ClickUp/customer delivery."
        ) from exc

    try:
        reader = PdfReader(BytesIO(pdf_content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # pragma: no cover - corrupt PDF guard
        raise ValueError("Could not extract text from Business Central invoice PDF.") from exc


def _normalize_pdf_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(without_accents.upper().split())


def _configured_pdf_text_markers(env_name: str, default: tuple[str, ...]) -> list[str]:
    raw_value = os.getenv(env_name, "").strip()
    if not raw_value:
        return list(default)
    return [part.strip() for part in raw_value.split("|") if part.strip()]


def _market_pdf_env_name(base_name: str, market: str) -> str:
    market_env_name = f"{base_name}_{market}" if market else ""
    if market_env_name and os.getenv(market_env_name, "").strip():
        return market_env_name
    return base_name


def _default_required_pdf_text(market: str) -> tuple[str, ...]:
    if market == "MX":
        return DEFAULT_MX_REQUIRED_PDF_TEXT
    return DEFAULT_REQUIRED_PDF_TEXT


def _default_forbidden_pdf_text(market: str) -> tuple[str, ...]:
    if market == "MX":
        return ("FACTRURA",)
    return DEFAULT_FORBIDDEN_PDF_TEXT


def _write_temp_pdf(content: bytes) -> Path:
    with NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(content)
        return Path(handle.name)


def _extract_attachment_id(upload_result: dict[str, Any]) -> str:
    candidates: list[Any] = [
        upload_result.get("id"),
        upload_result.get("attachment_id"),
        (upload_result.get("attachment") or {}).get("id")
        if isinstance(upload_result.get("attachment"), dict)
        else None,
        (upload_result.get("data") or {}).get("id") if isinstance(upload_result.get("data"), dict) else None,
    ]
    attachments = upload_result.get("attachments")
    if isinstance(attachments, list) and attachments:
        first = attachments[0]
        if isinstance(first, dict):
            candidates.append(first.get("id"))

    for candidate in candidates:
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _extract_attachment_url(upload_result: dict[str, Any]) -> str:
    candidates: list[Any] = [
        upload_result.get("url"),
        upload_result.get("url_w_query"),
        upload_result.get("url_w_host"),
    ]
    attachments = upload_result.get("attachments")
    if isinstance(attachments, list) and attachments and isinstance(attachments[0], dict):
        first = attachments[0]
        candidates.extend([first.get("url"), first.get("url_w_query"), first.get("url_w_host")])
    for candidate in candidates:
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _uploaded_document_reference(uploaded: dict[str, Any]) -> str:
    file_name = str(uploaded.get("file_name") or "Unavailable")
    attachment_url = str(uploaded.get("attachment_url") or "").strip()
    if attachment_url:
        return f"{file_name} | {attachment_url}"
    return file_name


def _safe_delivery_error(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return message[:500]


def _existing_file_field_attachment_ids(
    custom_fields: dict[str, dict[str, Any]],
    *,
    field_id: str,
) -> list[str]:
    field = _find_custom_field_by_id(custom_fields, field_id=field_id)
    value = (field or {}).get("value")
    if not isinstance(value, list):
        return []

    ids = []
    for item in value:
        if not isinstance(item, dict):
            continue
        attachment_id = item.get("id")
        if attachment_id is not None and str(attachment_id).strip():
            ids.append(str(attachment_id).strip())
    return ids


def _find_custom_field_by_id(
    custom_fields: dict[str, dict[str, Any]],
    *,
    field_id: str,
) -> dict[str, Any] | None:
    for details in custom_fields.values():
        if details.get("id") == field_id:
            return details
    return None


def _set_invoice_status(
    *,
    clickup: ClickUpClient,
    clickup_summary: dict[str, Any],
    settings: InvoiceAutomationSettings,
    target_status: str,
) -> dict[str, Any]:
    custom_fields = clickup_summary.get("custom_fields") or {}
    for field_name in settings.invoice_status_field_names:
        field = custom_fields.get(field_name)
        if not field:
            continue
        option_id = _dropdown_option_id(field, target_status)
        if option_id is None:
            raise ValueError(f"Could not resolve ClickUp invoice status option: {target_status}")
        return clickup.set_task_custom_field_value(clickup_summary["task_id"], field["id"], option_id)

    for field_id in settings.invoice_status_field_ids:
        field = _find_custom_field_by_id(custom_fields, field_id=field_id)
        if not field:
            continue
        option_id = _dropdown_option_id(field, target_status)
        if option_id is None:
            raise ValueError(f"Could not resolve ClickUp invoice status option: {target_status}")
        return clickup.set_task_custom_field_value(
            clickup_summary["task_id"],
            field.get("id") or field_id,
            option_id,
        )

    raise ValueError("Could not find the ClickUp invoice status field to mark the task as Facturada.")


def _dropdown_option_id(field: dict[str, Any], option_name: str) -> str | int | None:
    target = _normalize_status(option_name)
    for option in (field.get("type_config") or {}).get("options", []):
        if _normalize_status(option.get("name")) == target:
            return option.get("id") or option.get("orderindex")
    return None


def _normalize_status(value: str | None) -> str:
    return "".join(char for char in (value or "").strip().lower() if char.isalnum())
