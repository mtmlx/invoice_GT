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
    issue_clickup_bc_sales_invoice,
    prepare_clickup_bc_sales_invoice_preview,
)
from clickup_integration.mapping import resolve_dropdown_field, summarize_task_for_customer_mapping


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cancel existing GT posted invoices and issue replacement ClickUp invoices."
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--team-id", default="8451352")
    parser.add_argument("--old-invoice", action="append", required=True)
    parser.add_argument(
        "--issue-datetime",
        action="append",
        default=[],
        metavar="INVOICE=YYYY-MM-DDTHH:MM:SS",
        help="Override FechaEmisionDocumentoAnular for one old invoice.",
    )
    parser.add_argument(
        "--motive",
        default="REEMISION DE FACTURA POR SOLICITUD OPERATIVA MTM LOGIX",
    )
    parser.add_argument(
        "--only-group",
        choices=["INT", "NAT", "ALL"],
        default="ALL",
        help="Issue only one invoice group after cancellation. ALL keeps the default behavior.",
    )
    parser.add_argument("--output-dir", default="output/invoice_runs")
    args = parser.parse_args()

    clickup = ClickUpClient(ClickUpSettings.from_env())
    bc = BusinessCentralClient(BusinessCentralSettings.from_env())
    settings = InvoiceAutomationSettings.from_env()
    market = settings.supported_market
    issue_datetime_overrides = _parse_issue_datetime_overrides(args.issue_datetime)

    task = clickup.get_task(
        args.task_id,
        custom_task_ids=True,
        team_id=args.team_id,
        include_subtasks=False,
    )
    summary = summarize_task_for_customer_mapping(task)
    summary = _force_ready_invoice_status(summary, settings)

    preview_before_cancel = prepare_clickup_bc_sales_invoice_preview(
        clickup_summary=summary,
        bc_client=bc,
        settings=settings,
    )
    _require_valid_replacement_preflight(
        preview_before_cancel,
        invoice_group=None if args.only_group == "ALL" else args.only_group,
    )
    _require_replacement_reference_preflight(
        preview=preview_before_cancel, bc=bc, market=market,
        old_invoice_numbers=args.old_invoice,
        invoice_group=None if args.only_group == "ALL" else args.only_group,
    )

    cancellation_results = []
    for invoice_number in args.old_invoice:
        cancellation_results.append(
            cancel_invoice_if_needed(
                bc=bc,
                invoice_number=invoice_number,
                market=market,
                motive=args.motive,
                issue_datetime_text=issue_datetime_overrides.get(invoice_number.upper()),
            )
        )

    preview_after_cancel = prepare_clickup_bc_sales_invoice_preview(
        clickup_summary=summary,
        bc_client=bc,
        settings=settings,
    )
    selected_group = None if args.only_group == "ALL" else args.only_group
    if selected_group:
        preview_after_cancel = _filter_preview_to_invoice_group(
            preview_after_cancel,
            invoice_group=selected_group,
        )

    if preview_after_cancel.get("status") not in {"ready", "dry_run_ready"}:
        raise SystemExit(
            json.dumps(
                {
                    "status": "blocked_after_cancel",
                    "message": "Replacement preview did not become ready after cancellation.",
                    "preview_before_cancel": preview_before_cancel,
                    "preview_after_cancel": preview_after_cancel,
                    "cancellations": cancellation_results,
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )

    if selected_group:
        invoice_result = issue_filtered_preview_invoice(
            preview=preview_after_cancel,
            bc=bc,
            market=market,
            clickup_summary=summary,
        )
    else:
        invoice_result = issue_clickup_bc_sales_invoice(
            clickup_summary=summary,
            bc_client=bc,
            settings=settings,
        )
    if invoice_result.get("status") != "applied":
        raise SystemExit(
            json.dumps(
                {
                    "status": "issue_failed",
                    "preview_before_cancel": preview_before_cancel,
                    "preview_after_cancel": preview_after_cancel,
                    "cancellations": cancellation_results,
                    "invoice_result": invoice_result,
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )

    delivery = finalize_clickup_issued_invoices(
        clickup=clickup,
        bc_client=bc,
        clickup_summary=summary,
        invoice_result=invoice_result,
        settings=settings,
        workspace_id=args.team_id,
        mark_status=True,
    )

    result = {
        "status": "completed",
        "task_id": args.task_id,
        "task_clickup_id": summary.get("task_id"),
        "old_invoices": args.old_invoice,
        "preview_before_cancel": preview_before_cancel,
        "cancellations": cancellation_results,
        "preview_after_cancel": preview_after_cancel,
        "invoice_result": invoice_result,
        "delivery": delivery,
    }
    output_path = _write_audit_file(args.output_dir, args.task_id, result)
    result["audit_file"] = str(output_path)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def _require_valid_replacement_preflight(
    preview: dict[str, Any],
    *,
    invoice_group: str | None = None,
) -> None:
    """Require a complete replacement plan before changing an old document."""
    blockers = []
    if preview.get("status") not in {"dry_run_ready", "duplicate_invoice"}:
        blockers.append(f"source preview status is {preview.get('status')!r}")
    validation = preview.get("invoice_validation") or {}
    if validation.get("status") != "passed" or validation.get("errors"):
        blockers.append("invoice validation has not passed")
    proposals = preview.get("proposed_bc_invoices") or []
    if not proposals:
        blockers.append("replacement invoice proposals are empty")
    for proposal in proposals:
        if not isinstance(proposal, dict) or not proposal.get("proposed_bc_payload"):
            blockers.append("a replacement invoice header is missing")
            continue
        billable_lines = [
            line
            for line in proposal.get("proposed_bc_line_payloads") or []
            if isinstance(line, dict)
            and str(line.get("lineType") or "").strip().lower() in {"item", "account"}
        ]
        if not billable_lines:
            blockers.append("a replacement invoice has no billable lines")
    if invoice_group and not any(
        isinstance(proposal, dict)
        and str(proposal.get("invoice_group") or "").strip().upper() == invoice_group.upper()
        for proposal in proposals
    ):
        blockers.append(f"replacement invoice group {invoice_group} is missing")
    if blockers:
        raise ValueError(
            "Replacement preflight blocked before cancellation; no cancellation was requested: "
            + "; ".join(blockers)
        )


def _assert_replacement_invoice_matches_proposal(
    *, invoice: dict[str, Any], reference: str, expected_total: float,
    expected_header: dict[str, Any],
) -> None:
    if str(invoice.get("externalDocumentNumber") or "").strip() != reference:
        raise ValueError(f"Active invoice lookup returned a conflicting reference for {reference}.")
    raw_total = invoice.get("totalAmountIncludingTax")
    if raw_total is None or round(float(raw_total), 2) != round(expected_total, 2):
        raise ValueError(f"Active invoice for {reference} has an unknown or conflicting total.")
    if str(invoice.get("currencyCode") or "").strip().upper() != str(expected_header.get("currencyCode") or "").strip().upper():
        raise ValueError(f"Active invoice for {reference} has a conflicting currency.")
    for key in ("customerNumber", "customerId"):
        expected = str(expected_header.get(key) or "").strip().casefold()
        if expected and str(invoice.get(key) or "").strip().casefold() != expected:
            raise ValueError(f"Active invoice for {reference} has a conflicting {key}.")


def _require_replacement_reference_preflight(
    *, preview: dict[str, Any], bc: BusinessCentralClient, market: str,
    old_invoice_numbers: list[str], invoice_group: str | None = None,
    allow_matching_replacements: bool = False,
) -> None:
    """Read every replacement reference before cancellation, exempting named originals only."""
    eligible_old_numbers = {str(number).strip().upper() for number in old_invoice_numbers if str(number).strip()}
    if not eligible_old_numbers:
        raise ValueError("Replacement reference preflight blocked before cancellation: original invoice numbers are missing.")
    for proposal in preview.get("proposed_bc_invoices") or []:
        if invoice_group and str(proposal.get("invoice_group") or "").strip().upper() != invoice_group.upper():
            continue
        header = proposal["proposed_bc_payload"]
        reference = str(header.get("externalDocumentNumber") or "").strip()
        if not reference or not header.get("currencyCode") or not (header.get("customerNumber") or header.get("customerId")):
            raise ValueError("Replacement reference preflight blocked before cancellation: replacement reference, customer or currency is missing.")
        escaped = reference.replace("'", "''")
        rows = bc.find_entities("salesInvoices", filters=f"externalDocumentNumber eq '{escaped}'", top=100, market=market)
        if len(rows) >= 100:
            raise ValueError("Replacement reference preflight blocked before cancellation: invoice lookup may be truncated.")
        surviving_active_rows = [
            invoice for invoice in rows
            if _normalized_status(invoice.get("status")) not in {"canceled", "cancelled"}
            and str(invoice.get("number") or "").strip().upper() not in eligible_old_numbers
        ]
        if len(surviving_active_rows) > 1:
            raise ValueError(
                f"Replacement reference preflight blocked before cancellation: more than one active "
                f"replacement invoice would survive for {reference}."
            )
        for invoice in rows:
            if _normalized_status(invoice.get("status")) in {"canceled", "cancelled"}:
                continue
            if str(invoice.get("externalDocumentNumber") or "").strip() != reference:
                raise ValueError("Replacement reference preflight blocked before cancellation: invoice reference lookup is inconsistent.")
            if str(invoice.get("number") or "").strip().upper() in eligible_old_numbers:
                continue
            try:
                _assert_replacement_invoice_matches_proposal(
                    invoice=invoice, reference=reference,
                    expected_total=float(proposal["total"]), expected_header=header,
                )
                if not allow_matching_replacements:
                    raise ValueError(
                        f"Active invoice {invoice.get('number')} for {reference} is not an original "
                        "explicitly selected for cancellation; this replacement flow cannot reuse it."
                    )
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"Replacement reference preflight blocked before cancellation: {exc}") from exc


def cancel_invoice_if_needed(
    *,
    bc: BusinessCentralClient,
    invoice_number: str,
    market: str,
    motive: str,
    issue_datetime_text: str | None = None,
) -> dict[str, Any]:
    invoice = bc.get_posted_sales_invoice_by_number(invoice_number, market=market)
    if not invoice:
        return {"invoice_number": invoice_number, "status": "not_found"}

    fel_row = bc.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
    fel_status = _normalized_status((fel_row or {}).get("electronicDocumentStatus"))
    invoice_status = _normalized_status(invoice.get("status"))
    if invoice_status in {"canceled", "cancelled"} and fel_status == "canceled":
        return {
            "invoice_number": invoice_number,
            "status": "already_canceled",
            "invoice": _invoice_summary(invoice),
            "fel_row": _fel_summary(fel_row),
        }

    if not fel_row or not fel_row.get("id"):
        raise ValueError(f"Business Central FEL row was not found for {invoice_number}.")

    if issue_datetime_text:
        response = bc._post_posted_invoice_fel_action(  # noqa: SLF001 - operational one-off.
            fel_row["id"],
            "CancelPostedInvoiceAndFelWithMotiveAndIssueDateTime",
            body={"motiveText": motive, "issueDateTimeText": issue_datetime_text},
            market=market,
        )
    else:
        response = bc._post_posted_invoice_fel_action(  # noqa: SLF001 - operational one-off.
            fel_row["id"],
            "CancelPostedInvoiceAndFelWithMotive",
            body={"motiveText": motive},
            market=market,
        )
    final_fel = wait_for_fel_status(
        bc=bc,
        invoice_number=invoice_number,
        market=market,
        expected="canceled",
    )
    final_invoice = bc.get_posted_sales_invoice_by_number(invoice_number, market=market)
    return {
        "invoice_number": invoice_number,
        "status": "canceled",
        "invoice_before": _invoice_summary(invoice),
        "fel_before": _fel_summary(fel_row),
        "cancel_response": response,
        "issue_datetime_override": issue_datetime_text,
        "invoice_after": _invoice_summary(final_invoice),
        "fel_after": _fel_summary(final_fel),
    }


def _filter_preview_to_invoice_group(
    preview: dict[str, Any],
    *,
    invoice_group: str,
) -> dict[str, Any]:
    normalized_group = invoice_group.strip().upper()
    proposed_invoices = [
        invoice
        for invoice in preview.get("proposed_bc_invoices") or []
        if str(invoice.get("invoice_group") or "").strip().upper() == normalized_group
    ]
    if not proposed_invoices:
        return {
            **preview,
            "status": "missing_invoice_group",
            "message": f"Preview does not include invoice group {normalized_group}.",
        }

    duplicate_invoices = [
        duplicate
        for duplicate in preview.get("duplicate_invoices") or []
        if str(duplicate.get("invoice_group") or "").strip().upper() == normalized_group
    ]
    if duplicate_invoices:
        return {
            **preview,
            "status": "duplicate_invoice",
            "message": f"Business Central invoice already exists for invoice group {normalized_group}.",
            "duplicate_invoices": duplicate_invoices,
            "existing_invoice": duplicate_invoices[0].get("existing_invoice"),
            "reference": duplicate_invoices[0].get("reference"),
            "invoice_group": normalized_group,
        }

    selected_invoice = proposed_invoices[0]
    selected_sources = selected_invoice.get("line_sources") or []
    selected_payloads = [
        line
        for line in selected_invoice.get("proposed_bc_line_payloads") or []
        if str(line.get("lineType") or "").strip().lower() != "comment"
    ]
    selected_total = round(float(selected_invoice.get("total") or 0), 2)
    return {
        **preview,
        "status": "dry_run_ready",
        "invoice_count": 1,
        "invoice_groups": [normalized_group],
        "invoice_group": normalized_group,
        "reference": selected_invoice.get("reference") or preview.get("reference"),
        "proposed_bc_payload": selected_invoice["proposed_bc_payload"],
        "proposed_bc_line_payloads": selected_payloads,
        "proposed_bc_invoices": proposed_invoices,
        "line_sources": selected_sources,
        "invoice_validation": {
            "status": "passed",
            "errors": [],
            "expected_total": selected_total,
            "line_payload_total": selected_total,
            "proposed_invoice_total": selected_total,
            "expected_totals_by_group": {normalized_group: selected_total},
            "proposed_totals_by_group": {normalized_group: selected_total},
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
                for source in selected_sources
            ],
        },
    }


def issue_filtered_preview_invoice(
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
        posted_invoice = _resolve_posted_invoice_for_created_invoice(
            bc=bc,
            created_invoice=created_invoice,
            market=market,
        )
        posted_invoice = {
            **posted_invoice,
            "invoice_group": invoice_group,
            "post_response": post_response,
        }
        posted_invoices.append(posted_invoice)

    completed_stages.extend(["create_sales_invoice", "post_sales_invoice"])

    for posted_invoice in posted_invoices:
        invoice_number = str(posted_invoice.get("number") or "").strip()
        fel_row = _wait_for_fel_row(
            bc=bc,
            invoice_number=invoice_number,
            market=market,
        )
        sync_response = bc.sync_posted_invoice_fel_line_descriptions(
            fel_row["id"],
            market=market,
        )
        fel_row_after_sync = (
            bc.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
            or fel_row
        )
        if "sync_fel_descriptions" not in completed_stages:
            completed_stages.append("sync_fel_descriptions")

        stamp_response = bc.stamp_posted_invoice_fel(fel_row["id"], market=market)
        fel_row_after_stamp = _wait_for_fel_status_value(
            bc=bc,
            invoice_number=invoice_number,
            market=market,
            expected="Stamp Received",
        )
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

    combined_created_invoice = _combine_invoices_for_writeback(finalized_invoices)
    return {
        **preview,
        "status": "applied",
        "created_invoice": combined_created_invoice,
        "created_invoices": created_invoices,
        "created_lines": created_lines,
        "posted_invoices": posted_invoices,
        "finalized_invoices": finalized_invoices,
        "completed_stages": completed_stages,
        "task_id": clickup_summary.get("task_id"),
    }


def _resolve_posted_invoice_for_created_invoice(
    *,
    bc: BusinessCentralClient,
    created_invoice: dict[str, Any],
    market: str,
) -> dict[str, Any]:
    reference = str(created_invoice.get("externalDocumentNumber") or "").strip()
    for _ in range(8):
        if reference:
            posted_invoice = bc.get_posted_sales_invoice_by_external_document_number(
                reference,
                market=market,
            )
            if posted_invoice and str(posted_invoice.get("number") or "").upper().startswith("GTFVR"):
                return posted_invoice
        time.sleep(2)
    raise ValueError(
        f"Business Central did not return a posted sales invoice for {reference}."
    )


def _wait_for_fel_row(
    *,
    bc: BusinessCentralClient,
    invoice_number: str,
    market: str,
) -> dict[str, Any]:
    for _ in range(8):
        row = bc.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
        if row:
            return row
        time.sleep(2)
    raise ValueError(f"Business Central FEL row was not available for {invoice_number}.")


def _wait_for_fel_status_value(
    *,
    bc: BusinessCentralClient,
    invoice_number: str,
    market: str,
    expected: str,
) -> dict[str, Any]:
    last_row: dict[str, Any] | None = None
    normalized_expected = _normalized_status(expected)
    for _ in range(12):
        row = bc.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
        if row:
            last_row = row
            if _normalized_status(row.get("electronicDocumentStatus")) == normalized_expected:
                return row
        time.sleep(3)
    raise ValueError(
        f"FEL status for {invoice_number} did not become {expected}. Last row: {last_row}"
    )


def _combine_invoices_for_writeback(finalized_invoices: list[dict[str, Any]]) -> dict[str, Any]:
    invoices = [
        {
            "invoice_group": invoice.get("invoice_group"),
            **(invoice.get("posted_invoice_after_stamp") or {}),
        }
        for invoice in finalized_invoices
    ]
    if not invoices:
        return {}
    if len(invoices) == 1:
        return invoices[0]
    return {
        "id": "; ".join(str(invoice.get("id") or "") for invoice in invoices),
        "number": "; ".join(str(invoice.get("number") or "") for invoice in invoices),
    }


def _parse_issue_datetime_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        invoice_number, separator, issue_datetime_text = value.partition("=")
        if not separator or not invoice_number.strip() or not issue_datetime_text.strip():
            raise ValueError(
                "--issue-datetime must use INVOICE=YYYY-MM-DDTHH:MM:SS format."
            )
        overrides[invoice_number.strip().upper()] = issue_datetime_text.strip()
    return overrides


def wait_for_fel_status(
    *,
    bc: BusinessCentralClient,
    invoice_number: str,
    market: str,
    expected: str,
    attempts: int = 12,
    delay_seconds: float = 5.0,
) -> dict[str, Any]:
    last_row: dict[str, Any] | None = None
    normalized_expected = _normalized_status(expected)
    for _ in range(attempts):
        row = bc.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
        if row:
            last_row = row
            if _normalized_status(row.get("electronicDocumentStatus")) == normalized_expected:
                return row
        time.sleep(delay_seconds)
    raise TimeoutError(
        f"FEL status for {invoice_number} did not become {expected}. Last row: {last_row}"
    )


def _force_ready_invoice_status(
    summary: dict[str, Any],
    settings: InvoiceAutomationSettings,
) -> dict[str, Any]:
    updated = dict(summary)
    custom_fields = {
        key: dict(value)
        for key, value in (summary.get("custom_fields") or {}).items()
        if isinstance(value, dict)
    }
    for field in custom_fields.values():
        if field.get("id") not in settings.invoice_status_field_ids:
            continue
        ready_option = _dropdown_option(field, settings.ready_status)
        if not ready_option:
            continue
        field["value"] = ready_option.get("id") or ready_option.get("orderindex")
        updated["custom_fields"] = custom_fields
        return updated

    for field_name in settings.invoice_status_field_names:
        field = custom_fields.get(field_name)
        if not field:
            continue
        ready_option = _dropdown_option(field, settings.ready_status)
        if not ready_option:
            continue
        field["value"] = ready_option.get("id") or ready_option.get("orderindex")
        updated["custom_fields"] = custom_fields
        return updated

    raise ValueError("Could not find the ClickUp invoice status field on the task summary.")


def _dropdown_option(field: dict[str, Any], label: str) -> dict[str, Any] | None:
    normalized_label = " ".join(label.strip().casefold().split())
    for option in (field.get("type_config") or {}).get("options", []):
        if " ".join(str(option.get("name") or "").strip().casefold().split()) == normalized_label:
            return option
    return resolve_dropdown_field(field)


def _normalized_status(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _invoice_summary(invoice: dict[str, Any] | None) -> dict[str, Any] | None:
    if not invoice:
        return None
    return {
        "id": invoice.get("id"),
        "number": invoice.get("number"),
        "status": invoice.get("status"),
        "externalDocumentNumber": invoice.get("externalDocumentNumber"),
        "customerNumber": invoice.get("customerNumber"),
        "customerName": invoice.get("customerName"),
        "currencyCode": invoice.get("currencyCode"),
        "totalAmountIncludingTax": invoice.get("totalAmountIncludingTax"),
    }


def _fel_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row.get("id"),
        "No": row.get("No"),
        "electronicDocumentStatus": row.get("electronicDocumentStatus"),
        "errorDescription": row.get("errorDescription"),
        "fiscalInvoiceNumberPAC": row.get("fiscalInvoiceNumberPAC"),
    }


def _write_audit_file(output_dir: str, task_id: str, result: dict[str, Any]) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    task_dir = Path(output_dir) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    output_path = task_dir / f"{task_id}-replacement-{timestamp}.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    main()
