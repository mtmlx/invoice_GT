from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from business_central_client.client import BusinessCentralClient
from business_central_client.config import Settings as BusinessCentralSettings
from clickup_integration.client import ClickUpClient
from clickup_integration.config import ClickUpSettings
from clickup_integration.invoice_delivery import finalize_clickup_issued_invoices
from clickup_integration.invoice_sync import (
    InvoiceAutomationSettings,
    prepare_clickup_bc_sales_invoice_preview,
)
from clickup_integration.mapping import summarize_task_for_customer_mapping
from scripts.replace_gt_invoices_once import (
    _combine_invoices_for_writeback,
    _force_ready_invoice_status,
    _parse_issue_datetime_overrides,
    _assert_replacement_invoice_matches_proposal,
    _require_replacement_reference_preflight,
    _require_valid_replacement_preflight,
    _wait_for_fel_row,
    _wait_for_fel_status_value,
    _write_audit_file,
    cancel_invoice_if_needed,
)


SPLIT_CHARGES = (
    {
        "reference_suffix": "",
        "charges": {"Freight (Ocean/Truck/Air)"},
    },
    {
        "reference_suffix": "-2",
        "charges": {"Emergency Surcharge"},
    },
)

SPECIAL_REQUIREMENT_SCHEMA = (
    "config/special_invoice_requirements/gt_int_two_step_special_request.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Cancel one GT invoice and replace it with two INT invoices split by "
            "Freight (Ocean/Truck/Air) and Emergency Surcharge."
        )
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--team-id", default="8451352")
    parser.add_argument("--old-invoice", required=True)
    parser.add_argument(
        "--issue-datetime",
        action="append",
        default=[],
        metavar="INVOICE=YYYY-MM-DDTHH:MM:SS",
        help="Override FechaEmisionDocumentoAnular for the old invoice if FEL cancellation needs it.",
    )
    parser.add_argument(
        "--motive",
        default="REEMISION DE FACTURA POR SOLICITUD OPERATIVA MTM LOGIX",
    )
    parser.add_argument("--output-dir", default="output/invoice_runs")
    args = parser.parse_args()

    clickup = ClickUpClient(ClickUpSettings.from_env())
    bc = BusinessCentralClient(BusinessCentralSettings.from_env())
    settings = InvoiceAutomationSettings.from_env()
    market = settings.supported_market
    issue_datetime_overrides = _parse_issue_datetime_overrides(args.issue_datetime)
    old_invoice = bc.get_posted_sales_invoice_by_number(args.old_invoice, market=market)
    if not old_invoice:
        raise ValueError(f"Business Central posted invoice was not found: {args.old_invoice}.")
    old_invoice_reference = str(old_invoice.get("externalDocumentNumber") or "").strip()

    task = clickup.get_task(
        args.task_id,
        custom_task_ids=True,
        team_id=args.team_id,
        include_subtasks=False,
    )
    summary = summarize_task_for_customer_mapping(task)
    summary = _force_supported_market_for_one_off(summary, settings)
    has_invoice_status_field = _has_invoice_status_field(summary, settings)
    summary = _force_ready_invoice_status_for_one_off(summary, settings)

    preview_before_cancel = prepare_clickup_bc_sales_invoice_preview(
        clickup_summary=summary,
        bc_client=bc,
        settings=settings,
    )
    _require_valid_replacement_preflight(preview_before_cancel, invoice_group="INT")
    split_preview_before_cancel = _build_split_int_preview(preview_before_cancel)
    _require_valid_replacement_preflight(split_preview_before_cancel, invoice_group="INT")
    _require_replacement_reference_preflight(
        preview=split_preview_before_cancel, bc=bc, market=market,
        old_invoice_numbers=[args.old_invoice],
        allow_matching_replacements=True,
    )

    try:
        cancellation = cancel_invoice_if_needed(
            bc=bc,
            invoice_number=args.old_invoice,
            market=market,
            motive=args.motive,
            issue_datetime_text=issue_datetime_overrides.get(args.old_invoice.upper()),
        )
    except Exception as exc:
        detail = str(exc)
        if _looks_like_fel_issue_datetime_mismatch(detail):
            raise SystemExit(
                json.dumps(
                    {
                        "status": "fel_cancellation_issue_datetime_mismatch",
                        "message": (
                            "FEL rejected the cancellation because the invoice issue datetime sent "
                            "by Business Central does not match the datetime registered in SAT/FEL."
                        ),
                        "old_invoice": args.old_invoice,
                        "required_rerun_argument": (
                            f"--issue-datetime {args.old_invoice}=YYYY-MM-DDTHH:MM:SS"
                        ),
                        "example_command": (
                            "python scripts/replace_gt_invoice_split_int_charges_once.py "
                            f"--task-id {args.task_id} --team-id {args.team_id} "
                            f"--old-invoice {args.old_invoice} "
                            f"--issue-datetime {args.old_invoice}=YYYY-MM-DDTHH:MM:SS"
                        ),
                        "detail": detail,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            ) from exc
        raise

    preview_after_cancel = prepare_clickup_bc_sales_invoice_preview(
        clickup_summary=summary,
        bc_client=bc,
        settings=settings,
    )
    split_preview = _build_split_int_preview(preview_after_cancel)

    if split_preview.get("status") != "dry_run_ready":
        raise SystemExit(
            json.dumps(
                {
                    "status": "blocked_after_cancel",
                    "message": "Split replacement preview did not become ready after cancellation.",
                    "preview_before_cancel": preview_before_cancel,
                    "cancellation": cancellation,
                    "preview_after_cancel": preview_after_cancel,
                    "split_preview": split_preview,
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )

    invoice_result = issue_split_preview_invoice(
        preview=split_preview,
        bc=bc,
        market=market,
        clickup_summary=summary,
    )
    if invoice_result.get("status") != "applied":
        raise SystemExit(
            json.dumps(
                {
                    "status": "issue_failed",
                    "preview_before_cancel": preview_before_cancel,
                    "cancellation": cancellation,
                    "preview_after_cancel": preview_after_cancel,
                    "split_preview": split_preview,
                    "invoice_result": invoice_result,
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )

    # Retain active documents, including the NAT invoice, while removing only the
    # cancelled INT document before the two replacement PDFs are appended.
    delivery = finalize_clickup_issued_invoices(
        clickup=clickup,
        bc_client=bc,
        clickup_summary={
            **summary,
            "custom_fields": _retain_non_replaced_invoice_attachments(
                summary.get("custom_fields") or {},
                replaced_invoice_number=args.old_invoice,
                replaced_external_reference=old_invoice_reference,
            ),
        },
        invoice_result=invoice_result,
        settings=settings,
        workspace_id=args.team_id,
        mark_status=has_invoice_status_field,
    )

    result = {
        "status": "completed",
        "task_id": args.task_id,
        "task_clickup_id": summary.get("task_id"),
        "old_invoice": args.old_invoice,
        "preview_before_cancel": preview_before_cancel,
        "cancellation": cancellation,
        "preview_after_cancel": preview_after_cancel,
        "split_preview": split_preview,
        "invoice_result": invoice_result,
        "delivery": delivery,
    }
    output_path = _write_audit_file(args.output_dir, args.task_id, result)
    result["audit_file"] = str(output_path)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def _build_split_int_preview(preview: dict[str, Any]) -> dict[str, Any]:
    try:
        _require_valid_replacement_preflight(preview, invoice_group="INT")
    except ValueError as exc:
        return {
            **preview,
            "status": "source_preview_not_ready",
            "message": str(exc),
        }
    if preview.get("status") == "duplicate_invoice":
        preview = {
            **preview,
            "status": "dry_run_ready",
            "ignored_duplicate_invoices": [
                duplicate
                for duplicate in preview.get("duplicate_invoices") or []
            ],
            "duplicate_invoices": [],
            "existing_invoice": None,
            "message": (
                "Ignoring duplicate preview blockers for this split INT replacement run; "
                "the issuance step will reuse matching active replacement invoices and block "
                "if a matching reference has the wrong total."
            ),
        }

    if preview.get("status") not in {"dry_run_ready", "ready"}:
        return {
            **preview,
            "status": "source_preview_not_ready",
            "message": f"Source preview status is {preview.get('status')}.",
        }

    int_invoice = _find_int_invoice(preview.get("proposed_bc_invoices") or [])
    if not int_invoice:
        return {
            **preview,
            "status": "missing_int_invoice",
            "message": "Source preview did not include an INT invoice.",
        }

    comment_lines = [
        line
        for line in int_invoice.get("proposed_bc_line_payloads") or []
        if str(line.get("lineType") or "").strip().lower() == "comment"
    ]
    charge_lines = [
        line
        for line in int_invoice.get("proposed_bc_line_payloads") or []
        if str(line.get("lineType") or "").strip().lower() != "comment"
    ]
    charge_sources = list(int_invoice.get("line_sources") or [])
    if len(charge_lines) != len(charge_sources):
        return {
            **preview,
            "status": "line_source_mismatch",
            "message": "INT charge line payload count does not match source count.",
            "int_charge_line_count": len(charge_lines),
            "int_line_source_count": len(charge_sources),
        }
    supported_charges = {
        str(charge).strip()
        for split in SPLIT_CHARGES
        for charge in split["charges"]
    }
    unexpected_charges = sorted({
        str(source.get("charge_name") or "").strip() or "unnamed INT charge"
        for source in charge_sources
        if str(source.get("charge_name") or "").strip() not in supported_charges
    })
    if unexpected_charges:
        return {
            **preview,
            "status": "unexpected_split_charges",
            "message": (
                "The approved INT split does not cover every source charge; "
                "refusing to omit: " + ", ".join(unexpected_charges)
            ),
            "unexpected_int_charges": unexpected_charges,
            "supported_int_charges": sorted(supported_charges),
        }

    base_header = dict(int_invoice["proposed_bc_payload"])
    base_reference = str(base_header.get("externalDocumentNumber") or preview.get("reference") or "").strip()
    if not base_reference:
        return {**preview, "status": "missing_reference", "message": "No external document reference was resolved."}

    proposed_invoices: list[dict[str, Any]] = []
    used_charge_names: set[str] = set()
    for split in SPLIT_CHARGES:
        target_charges = {str(charge).strip() for charge in split["charges"]}
        selected_pairs = [
            (line, source)
            for line, source in zip(charge_lines, charge_sources, strict=True)
            if str(source.get("charge_name") or "").strip() in target_charges
        ]
        if not selected_pairs:
            return {
                **preview,
                "status": "missing_split_charge",
                "message": f"INT invoice does not include required charge(s): {sorted(target_charges)}.",
                "available_int_charges": [source.get("charge_name") for source in charge_sources],
            }

        selected_lines = [line for line, _source in selected_pairs]
        selected_sources = [source for _line, source in selected_pairs]
        used_charge_names.update(str(source.get("charge_name") or "").strip() for source in selected_sources)
        reference = f"{base_reference}{split['reference_suffix']}"
        header = {
            **base_header,
            "externalDocumentNumber": reference,
            "customerPurchaseOrderReference": base_header.get("customerPurchaseOrderReference")
            or preview.get("reference")
            or reference,
        }
        proposed_invoices.append(
            {
                "invoice_group": "INT",
                "reference": reference,
                "proposed_bc_payload": header,
                "proposed_bc_line_payloads": [*comment_lines, *selected_lines],
                "line_sources": selected_sources,
                "total": _sum_line_amounts(selected_lines),
                "split_charges": sorted(target_charges),
            }
        )

    expected_total = _sum_line_amounts(charge_lines)
    proposed_total = _sum_line_amounts(
        [
            line
            for invoice in proposed_invoices
            for line in invoice.get("proposed_bc_line_payloads") or []
            if str(line.get("lineType") or "").strip().lower() != "comment"
        ]
    )
    if round(expected_total, 2) != round(proposed_total, 2):
        return {
            **preview,
            "status": "split_total_mismatch",
            "message": "Split invoice total does not reconcile to selected charge total.",
            "expected_total": expected_total,
            "proposed_total": proposed_total,
        }

    return {
        **preview,
        "status": "dry_run_ready",
        "invoice_count": len(proposed_invoices),
        "invoice_groups": ["INT", "INT"],
        "proposed_bc_invoices": proposed_invoices,
        "proposed_bc_payload": proposed_invoices[0]["proposed_bc_payload"],
        "proposed_bc_line_payloads": proposed_invoices[0]["proposed_bc_line_payloads"],
        "line_sources": [source for invoice in proposed_invoices for source in invoice.get("line_sources") or []],
        "invoice_validation": {
            "status": "passed",
            "errors": [],
            "expected_total": expected_total,
            "line_payload_total": proposed_total,
            "proposed_invoice_total": proposed_total,
            "selected_charges": sorted(used_charge_names),
            "billable_fields": [
                {
                    "charge_name": source.get("charge_name"),
                    "invoice_group": source.get("invoice_group"),
                    "amount": round(float(source.get("amount") or 0), 2),
                    "source_field": source.get("source_field"),
                    "source_field_id": source.get("source_field_id"),
                    "item_number": source.get("item_number"),
                    "description": source.get("description"),
                }
                for invoice in proposed_invoices
                for source in invoice.get("line_sources") or []
            ],
        },
    }


def _find_int_invoice(proposed_invoices: list[dict[str, Any]]) -> dict[str, Any] | None:
    for invoice in proposed_invoices:
        if str(invoice.get("invoice_group") or "").strip().upper() == "INT":
            return invoice
    return None


def issue_split_preview_invoice(
    *,
    preview: dict[str, Any],
    bc: BusinessCentralClient,
    market: str,
    clickup_summary: dict[str, Any],
) -> dict[str, Any]:
    created_invoices: list[dict[str, Any]] = []
    created_lines: list[dict[str, Any]] = []
    posted_invoices: list[dict[str, Any]] = []
    finalized_invoices: list[dict[str, Any]] = []
    completed_stages: list[str] = []

    for proposed_invoice in preview.get("proposed_bc_invoices") or []:
        invoice_group = proposed_invoice.get("invoice_group")
        expected_total = round(float(proposed_invoice.get("total") or 0), 2)
        reference = str(proposed_invoice["proposed_bc_payload"].get("externalDocumentNumber") or "").strip()
        existing_invoice = _find_active_invoice_by_reference(
            bc=bc,
            market=market,
            reference=reference,
        )
        if existing_invoice:
            _assert_existing_invoice_matches_expected_total(
                invoice=existing_invoice,
                reference=reference,
                expected_total=expected_total,
                expected_header=proposed_invoice["proposed_bc_payload"],
            )
            posted_invoice = existing_invoice
            if not _looks_posted_invoice_number(posted_invoice.get("number")):
                post_response = bc.post_sales_invoice(posted_invoice["id"], market=market)
                posted_invoice = _resolve_posted_invoice_for_created_or_reused_invoice(
                    bc=bc,
                    invoice=posted_invoice,
                    market=market,
                )
            else:
                post_response = {"status": "reused_existing_posted_invoice"}
            created_invoices.append(
                {
                    "invoice_group": invoice_group,
                    "reused_existing_invoice": True,
                    **posted_invoice,
                }
            )
            posted_invoices.append(
                {
                    **posted_invoice,
                    "invoice_group": invoice_group,
                    "post_response": post_response,
                }
            )
            continue

        created_invoice = bc.create_sales_invoice(
            proposed_invoice["proposed_bc_payload"],
            market=market,
        )
        created_invoice = {"invoice_group": invoice_group, **created_invoice}
        created_invoices.append(created_invoice)

        for line_payload in proposed_invoice["proposed_bc_line_payloads"]:
            created_line = bc.create_sales_invoice_line(
                created_invoice["id"],
                line_payload,
                market=market,
            )
            created_lines.append(
                {
                    "invoice_group": invoice_group,
                    "invoice_id": created_invoice["id"],
                    **created_line,
                }
            )

        post_response = bc.post_sales_invoice(created_invoice["id"], market=market)
        posted_invoice = _resolve_posted_invoice_for_created_or_reused_invoice(
            bc=bc,
            invoice=created_invoice,
            market=market,
        )
        posted_invoices.append(
            {
                **posted_invoice,
                "invoice_group": invoice_group,
                "post_response": post_response,
            }
        )

    completed_stages.extend(["create_or_reuse_sales_invoice", "post_sales_invoice"])

    for posted_invoice in posted_invoices:
        invoice_number = str(posted_invoice.get("number") or "").strip()
        fel_row = _wait_for_fel_row(
            bc=bc,
            invoice_number=invoice_number,
            market=market,
        )
        if _is_stamp_received(fel_row):
            sync_response = {"status": "already_stamp_received"}
            stamp_response = {"status": "already_stamp_received"}
            fel_row_after_sync = fel_row
            fel_row_after_stamp = fel_row
        else:
            sync_response = bc.sync_posted_invoice_fel_line_descriptions(
                fel_row["id"],
                market=market,
            )
            fel_row_after_sync = (
                bc.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
                or fel_row
            )
            stamp_response = bc.stamp_posted_invoice_fel(fel_row["id"], market=market)
            fel_row_after_stamp = _wait_for_fel_status_value(
                bc=bc,
                invoice_number=invoice_number,
                market=market,
                expected="Stamp Received",
            )
        if "sync_fel_descriptions" not in completed_stages:
            completed_stages.append("sync_fel_descriptions")
        if "stamp_fel_invoice" not in completed_stages:
            completed_stages.append("stamp_fel_invoice")

        finalized_invoices.append(
            {
                "invoice_group": posted_invoice.get("invoice_group"),
                "number": invoice_number,
                "externalDocumentNumber": posted_invoice.get("externalDocumentNumber"),
                "posted_invoice_after_stamp": bc.get_entity(
                    "salesInvoices",
                    posted_invoice["id"],
                    market=market,
                )
                or posted_invoice,
                "custom_api_row_after_sync": fel_row_after_sync,
                "custom_api_row_after_stamp": fel_row_after_stamp,
                "gt_registered_invoice_after_stamp": bc.get_gt_registered_invoice_by_number(
                    invoice_number,
                    market=market,
                ),
                "sync_descriptions_response": sync_response,
                "stamp_response": stamp_response,
            }
        )

    return {
        **preview,
        "status": "applied",
        "created_invoice": _combine_invoices_for_writeback(finalized_invoices),
        "created_invoices": created_invoices,
        "created_lines": created_lines,
        "posted_invoices": posted_invoices,
        "finalized_invoices": finalized_invoices,
        "completed_stages": completed_stages,
        "task_id": clickup_summary.get("task_id"),
    }


def _resolve_posted_invoice_for_created_or_reused_invoice(
    *,
    bc: BusinessCentralClient,
    invoice: dict[str, Any],
    market: str,
) -> dict[str, Any]:
    invoice_id = str(invoice.get("id") or "").strip()
    reference = str(invoice.get("externalDocumentNumber") or "").strip()
    for _attempt in range(8):
        if invoice_id:
            by_id = bc.get_entity("salesInvoices", invoice_id, market=market)
            if by_id and _looks_posted_invoice_number(by_id.get("number")):
                return by_id
        if reference:
            by_reference = _find_active_invoice_by_reference(
                bc=bc,
                market=market,
                reference=reference,
            )
            if by_reference and _looks_posted_invoice_number(by_reference.get("number")):
                return by_reference
        time.sleep(2)
    raise ValueError(f"Business Central did not return a posted sales invoice for {reference or invoice_id}.")


def _find_active_invoice_by_reference(
    *,
    bc: BusinessCentralClient,
    market: str,
    reference: str,
) -> dict[str, Any] | None:
    escaped = reference.replace("'", "''")
    rows = bc.find_entities(
        "salesInvoices",
        filters=f"externalDocumentNumber eq '{escaped}'",
        top=10,
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
        key=lambda row: (
            0 if _looks_posted_invoice_number(row.get("number")) else 1,
            str(row.get("lastModifiedDateTime") or row.get("postingDate") or ""),
        )
    )
    return active_rows[0]


def _assert_existing_invoice_matches_expected_total(
    *,
    invoice: dict[str, Any],
    reference: str,
    expected_total: float,
    expected_header: dict[str, Any] | None = None,
) -> None:
    if expected_header is not None:
        _assert_replacement_invoice_matches_proposal(
            invoice=invoice, reference=reference, expected_total=expected_total,
            expected_header=expected_header,
        )
        return
    raw_total = invoice.get("totalAmountIncludingTax")
    if raw_total is None:
        raw_total = invoice.get("totalAmountExcludingTax")
    actual_total = round(float(raw_total or 0), 2)
    if actual_total != round(expected_total, 2):
        raise ValueError(
            f"Active invoice for {reference} already exists but total {actual_total} "
            f"does not match expected {round(expected_total, 2)}."
        )


def _is_stamp_received(fel_row: dict[str, Any] | None) -> bool:
    return " ".join(str((fel_row or {}).get("electronicDocumentStatus") or "").split()).lower() == "stamp received"


def _looks_posted_invoice_number(value: Any) -> bool:
    return str(value or "").strip().upper().startswith("GTFVR")


def _force_ready_invoice_status_for_one_off(
    summary: dict[str, Any],
    settings: InvoiceAutomationSettings,
) -> dict[str, Any]:
    try:
        return _force_ready_invoice_status(summary, settings)
    except ValueError as exc:
        if "invoice status field" not in str(exc):
            raise
        # DISPUTE tasks may not carry the shared invoice status custom field.
        # The standard preview accepts task status as a fallback, so keep this
        # override in-memory and do not mutate the actual ClickUp task status.
        return {**summary, "status": settings.ready_status}


def _force_supported_market_for_one_off(
    summary: dict[str, Any],
    settings: InvoiceAutomationSettings,
) -> dict[str, Any]:
    if str(summary.get("market") or "").strip():
        return summary
    return {**summary, "market": settings.supported_market}


def _has_invoice_status_field(
    summary: dict[str, Any],
    settings: InvoiceAutomationSettings,
) -> bool:
    custom_fields = summary.get("custom_fields") or {}
    for field_name in settings.invoice_status_field_names:
        if field_name in custom_fields:
            return True
    for field in custom_fields.values():
        if isinstance(field, dict) and field.get("id") in settings.invoice_status_field_ids:
            return True
    return False


def _looks_like_fel_issue_datetime_mismatch(detail: str) -> bool:
    normalized = detail.casefold()
    return (
        "fel-gui-56" in normalized
        or (
            "fecha de emision" in _strip_accents(normalized)
            and "no coincide" in _strip_accents(normalized)
        )
    )


def _strip_accents(value: str) -> str:
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


def _active_replacement_duplicates(
    *,
    bc: BusinessCentralClient,
    market: str,
    references: list[str],
) -> list[dict[str, Any]]:
    duplicates: list[dict[str, Any]] = []
    for reference in references:
        escaped = reference.replace("'", "''")
        rows = bc.find_entities(
            "salesInvoices",
            filters=f"externalDocumentNumber eq '{escaped}'",
            top=5,
            market=market,
        )
        active_rows = [
            row
            for row in rows
            if str(row.get("status") or "").strip().lower() not in {"canceled", "cancelled"}
        ]
        for row in active_rows:
            duplicates.append(
                {
                    "reference": reference,
                    "id": row.get("id"),
                    "number": row.get("number"),
                    "status": row.get("status"),
                    "customerNumber": row.get("customerNumber"),
                }
            )
    return duplicates


def _retain_non_replaced_invoice_attachments(
    custom_fields: dict[str, Any],
    *,
    replaced_invoice_number: str,
    replaced_external_reference: str,
) -> dict[str, Any]:
    retained_fields = dict(custom_fields)
    replaced_names = {
        f"{value.strip()}.pdf".casefold()
        for value in (replaced_invoice_number, replaced_external_reference)
        if str(value or "").strip()
    }
    for key, field in list(retained_fields.items()):
        if isinstance(field, dict) and field.get("id") == "5d67859a-1ae0-4cda-9f57-2a89bf1ff259":
            field = dict(field)
            attachments = field.get("value")
            if not isinstance(attachments, list):
                attachments = []
            field["value"] = [
                attachment
                for attachment in attachments
                if not (
                    isinstance(attachment, dict)
                    and str(attachment.get("title") or "").strip().casefold() in replaced_names
                )
            ]
            retained_fields[key] = field
    return retained_fields


def _sum_line_amounts(line_payloads: list[dict[str, Any]]) -> float:
    total = 0.0
    for line in line_payloads:
        if str(line.get("lineType") or "").strip().lower() == "comment":
            continue
        total += float(line.get("unitPrice") or 0) * float(line.get("quantity") or 0)
    return round(total, 2)


if __name__ == "__main__":
    main()
