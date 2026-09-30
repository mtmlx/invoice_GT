from __future__ import annotations

import secrets

import argparse
import json
import os
from pathlib import Path

import requests

from business_central_client.client import BusinessCentralClient
from business_central_client.config import Settings as BusinessCentralSettings
from clickup_integration.ap_invoice_sync import (
    APPurchaseInvoiceSettings,
    apply_clickup_bc_purchase_invoice,
    build_clickup_ap_transfer_comment,
    prepare_clickup_bc_purchase_invoice_preview,
)
from clickup_integration.auth import (
    build_authorization_url,
    exchange_code_for_token,
    format_env_update,
    wait_for_oauth_callback,
)
from clickup_integration.bc_sync import (
    apply_clickup_to_bc_customer_sync,
    prepare_clickup_to_bc_customer_sync,
)
from clickup_integration.client import ClickUpClient
from clickup_integration.config import ClickUpSettings
from clickup_integration.create_preview import prepare_clickup_bc_customer_create_preview
from clickup_integration.create_preview import (
    apply_clickup_bc_customer_create,
    prepare_clickup_bc_created_customer_writeback,
)
from clickup_integration.invoice_delivery import (
    finalize_clickup_issued_invoices,
)
from clickup_integration.invoice_sync import (
    InvoiceAutomationSettings,
    apply_clickup_bc_sales_invoice,
    issue_clickup_bc_sales_invoice,
    prepare_clickup_bc_sales_invoice_preview,
    prepare_clickup_invoice_status_transition,
)
from clickup_integration.matcher import match_clickup_customer_to_bc
from clickup_integration.mapping import summarize_task_for_customer_mapping
from clickup_integration.revenue_invoice_sync import (
    RevenueInvoiceSyncSettings,
    run_revenue_invoice_sync,
)
from clickup_integration.writeback import (
    prepare_clickup_bc_writeback,
)
from whatsapp_integration.config import WhatsAppSettings
from whatsapp_integration.provider import normalize_twilio_inbound
from whatsapp_integration.router import route_customer_message


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ClickUp integration CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_url_parser = subparsers.add_parser(
        "auth-url",
        help="Generate the ClickUp OAuth authorization URL",
    )
    auth_url_parser.add_argument("--state")

    exchange_parser = subparsers.add_parser(
        "exchange-code",
        help="Exchange a ClickUp OAuth authorization code for an access token",
    )
    exchange_parser.add_argument("--code", required=True)
    exchange_parser.add_argument("--token-output", required=True, help="New private file for OAuth credentials")

    listen_parser = subparsers.add_parser(
        "oauth-listen",
        help="Wait for a local ClickUp OAuth callback and exchange the code",
    )
    listen_parser.add_argument("--state")
    listen_parser.add_argument("--token-output", required=True, help="New private file for OAuth credentials")

    subparsers.add_parser(
        "workspaces",
        help="List the ClickUp workspaces authorized for the current access token",
    )

    task_parser = subparsers.add_parser(
        "task",
        help="Fetch a ClickUp task for mapping review",
    )
    task_parser.add_argument("--task-id", required=True)
    task_parser.add_argument("--custom-task-ids", action="store_true")
    task_parser.add_argument("--team-id")
    task_parser.add_argument("--include-subtasks", action="store_true")

    match_parser = subparsers.add_parser(
        "match-customer",
        help="Fetch a ClickUp customer task and search Business Central for candidate matches",
    )
    match_parser.add_argument("--task-id", required=True)
    match_parser.add_argument("--custom-task-ids", action="store_true")
    match_parser.add_argument("--team-id")

    create_preview_parser = subparsers.add_parser(
        "preview-create-customer",
        help="Build a dry-run BC customer creation preview from a ClickUp current customer task",
    )
    create_preview_parser.add_argument("--task-id", required=True)
    create_preview_parser.add_argument("--custom-task-ids", action="store_true")
    create_preview_parser.add_argument("--team-id")

    create_apply_parser = subparsers.add_parser(
        "create-customer-in-bc",
        help="Create a new BC customer from a ClickUp current customer task and write the BC data back to ClickUp",
    )
    create_apply_parser.add_argument("--task-id", required=True)
    create_apply_parser.add_argument("--custom-task-ids", action="store_true")
    create_apply_parser.add_argument("--team-id")
    create_apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the BC creation and ClickUp write-back payloads without sending them",
    )

    writeback_parser = subparsers.add_parser(
        "writeback-customer-match",
        help="Write the best BC match back into ClickUp custom fields for a current customer task",
    )
    writeback_parser.add_argument("--task-id", required=True)
    writeback_parser.add_argument("--custom-task-ids", action="store_true")
    writeback_parser.add_argument("--team-id")
    writeback_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the ClickUp field updates without sending them",
    )

    sync_to_bc_parser = subparsers.add_parser(
        "sync-customer-to-bc",
        help="Push approved shared customer fields from ClickUp into the matched BC customer",
    )
    sync_to_bc_parser.add_argument("--task-id", required=True)
    sync_to_bc_parser.add_argument("--custom-task-ids", action="store_true")
    sync_to_bc_parser.add_argument("--team-id")
    sync_to_bc_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the BC patch without sending it",
    )

    invoice_smoke_parser = subparsers.add_parser(
        "smoke-test-invoice",
        help="Preview or apply the GT/USD ClickUp -> BC sales invoice flow for a task",
    )
    invoice_smoke_parser.add_argument("--task-id", required=True)
    invoice_smoke_parser.add_argument("--custom-task-ids", action="store_true")
    invoice_smoke_parser.add_argument("--team-id")
    invoice_smoke_parser.add_argument(
        "--apply",
        action="store_true",
        help="Create the BC sales invoice instead of only previewing the flow",
    )
    invoice_smoke_parser.add_argument(
        "--set-ready-status",
        action="store_true",
        help="If the task qualifies from OK Finops, update it to the configured ready-to-invoice status before applying",
    )
    invoice_smoke_parser.add_argument(
        "--writeback",
        action="store_true",
        help="Upload invoice PDFs, comment BC invoice details, and mark the task Facturada after apply",
    )

    fields_parser = subparsers.add_parser(
        "list-custom-fields",
        help="Fetch the custom fields available on a ClickUp list",
    )
    fields_parser.add_argument("--list-id")

    revenue_invoice_parser = subparsers.add_parser(
        "sync-revenue-invoices",
        help="Scheduled Business Central -> ClickUp Revenue Guatemala posted invoice sync",
    )
    revenue_invoice_parser.add_argument(
        "--apply",
        action="store_true",
        help="Create/update ClickUp tasks. Defaults to dry-run.",
    )
    revenue_invoice_parser.add_argument(
        "--invoice-no",
        help="Sync one posted Business Central invoice number.",
    )
    revenue_invoice_parser.add_argument(
        "--full-review",
        action="store_true",
        help="Run the weekly/full review window instead of the incremental window.",
    )

    ap_invoice_parser = subparsers.add_parser(
        "sync-ap-invoice",
        help="Preview or create a Guatemala ClickUp AP purchase invoice in Business Central",
    )
    ap_invoice_parser.add_argument("--task-id", required=True)
    ap_invoice_parser.add_argument("--custom-task-ids", action="store_true")
    ap_invoice_parser.add_argument("--team-id")
    ap_invoice_parser.add_argument(
        "--compare-bc-invoice-number",
        help="Compare the proposed payload to an existing Business Central purchase invoice.",
    )
    ap_invoice_parser.add_argument(
        "--apply",
        action="store_true",
        help="Create the BC purchase invoice draft. Defaults to dry-run.",
    )

    whatsapp_route_parser = subparsers.add_parser(
        "resolve-whatsapp-route",
        help="Preview how an inbound WhatsApp phone number would route into ClickUp",
    )
    whatsapp_route_parser.add_argument("--phone", required=True)
    whatsapp_route_parser.add_argument("--profile-name")
    whatsapp_route_parser.add_argument("--message-id", default="preview-message")
    whatsapp_route_parser.add_argument("--body", default="")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_oauth = args.command in {"auth-url", "exchange-code", "oauth-listen"}
    settings = ClickUpSettings.from_env(require_oauth=require_oauth)

    if args.command == "auth-url":
        print(build_authorization_url(settings, state=args.state))
        return

    if args.command in {"exchange-code", "oauth-listen"}:
        # Create exclusively before consuming a one-use code; never follow or overwrite a file.
        descriptor = os.open(args.token_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            if args.command == "oauth-listen":
                state = args.state or secrets.token_urlsafe(32)
                print("Open this URL in your browser:")
                print(build_authorization_url(settings, state=state))
                callback = wait_for_oauth_callback(settings, expected_state=state)
                code = callback["code"]
            else:
                code = args.code
            token = exchange_code_for_token(settings, code=code)
            output.write(format_env_update(token) + "\n")
        _print({"status": "credentials_saved", "token_output": str(Path(args.token_output).absolute())})
        return

    client = ClickUpClient(settings)

    if args.command == "workspaces":
        _print(client.get_authorized_workspaces())
        return

    if args.command == "task":
        task = client.get_task(
            args.task_id,
            custom_task_ids=args.custom_task_ids,
            team_id=args.team_id,
            include_subtasks=args.include_subtasks,
        )
        _print(summarize_task_for_customer_mapping(task))
        return

    if args.command == "match-customer":
        task = client.get_task(
            args.task_id,
            custom_task_ids=args.custom_task_ids,
            team_id=args.team_id,
            include_subtasks=False,
        )
        summary = summarize_task_for_customer_mapping(task)
        bc_settings = BusinessCentralSettings.from_env()
        bc_client = BusinessCentralClient(bc_settings)
        _print(match_clickup_customer_to_bc(clickup_summary=summary, bc_client=bc_client))
        return

    if args.command == "preview-create-customer":
        task = client.get_task(
            args.task_id,
            custom_task_ids=args.custom_task_ids,
            team_id=args.team_id,
            include_subtasks=False,
        )
        summary = summarize_task_for_customer_mapping(task)
        bc_settings = BusinessCentralSettings.from_env()
        bc_client = BusinessCentralClient(bc_settings)
        current_match = match_clickup_customer_to_bc(clickup_summary=summary, bc_client=bc_client)
        _print(
            prepare_clickup_bc_customer_create_preview(
                clickup_summary=summary,
                current_match_result=current_match,
                bc_client=bc_client,
            )
        )
        return

    if args.command == "create-customer-in-bc":
        task = client.get_task(
            args.task_id,
            custom_task_ids=args.custom_task_ids,
            team_id=args.team_id,
            include_subtasks=False,
        )
        summary = summarize_task_for_customer_mapping(task)
        bc_settings = BusinessCentralSettings.from_env()
        bc_client = BusinessCentralClient(bc_settings)
        current_match = match_clickup_customer_to_bc(clickup_summary=summary, bc_client=bc_client)

        if args.dry_run:
            preview = prepare_clickup_bc_customer_create_preview(
                clickup_summary=summary,
                current_match_result=current_match,
                bc_client=bc_client,
            )
            _print({"mode": "dry_run", "preview": preview, "match_result": current_match})
            return

        result = apply_clickup_bc_customer_create(
            clickup_summary=summary,
            current_match_result=current_match,
            bc_client=bc_client,
        )
        if result.get("status") != "applied":
            _print({"mode": "blocked", "result": result})
            return

        payload = result["writeback"]
        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["number"],
            payload["bc_customer_number"],
        )
        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["id"],
            payload["bc_customer_id"],
        )
        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["link"],
            payload["bc_customer_link"],
        )
        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["legal_name"],
            payload["bc_legal_name"],
        )
        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["status"],
            payload["bc_match_status"],
        )
        _print({"mode": "applied", "result": result})
        return

    if args.command == "writeback-customer-match":
        task = client.get_task(
            args.task_id,
            custom_task_ids=args.custom_task_ids,
            team_id=args.team_id,
            include_subtasks=False,
        )
        summary = summarize_task_for_customer_mapping(task)
        bc_settings = BusinessCentralSettings.from_env()
        bc_client = BusinessCentralClient(bc_settings)
        match_result = match_clickup_customer_to_bc(clickup_summary=summary, bc_client=bc_client)
        payload = prepare_clickup_bc_writeback(
            clickup_summary=summary,
            match_result=match_result,
            bc_client=bc_client,
        )

        if args.dry_run:
            _print({"mode": "dry_run", "writeback": payload, "match_result": match_result})
            return

        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["number"],
            payload["bc_customer_number"],
        )
        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["id"],
            payload["bc_customer_id"],
        )
        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["link"],
            payload["bc_customer_link"],
        )
        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["legal_name"],
            payload["bc_legal_name"],
        )
        client.set_task_custom_field_value(
            payload["task_id"],
            payload["field_ids"]["status"],
            payload["bc_match_status"],
        )
        _print({"mode": "applied", "writeback": payload, "match_result": match_result})
        return

    if args.command == "sync-customer-to-bc":
        task = client.get_task(
            args.task_id,
            custom_task_ids=args.custom_task_ids,
            team_id=args.team_id,
            include_subtasks=False,
        )
        summary = summarize_task_for_customer_mapping(task)
        bc_settings = BusinessCentralSettings.from_env()
        bc_client = BusinessCentralClient(bc_settings)

        if args.dry_run:
            _print(
                {
                    "mode": "dry_run",
                    "sync": prepare_clickup_to_bc_customer_sync(
                        clickup_summary=summary,
                        bc_client=bc_client,
                    ),
                }
            )
            return

        _print(
            {
                "mode": "applied",
                "sync": apply_clickup_to_bc_customer_sync(
                    clickup_summary=summary,
                    bc_client=bc_client,
                ),
            }
        )
        return

    if args.command == "smoke-test-invoice":
        task = client.get_task(
            args.task_id,
            custom_task_ids=args.custom_task_ids,
            team_id=args.team_id,
            include_subtasks=False,
        )
        summary = summarize_task_for_customer_mapping(task)
        bc_settings = BusinessCentralSettings.from_env()
        bc_client = BusinessCentralClient(bc_settings)
        invoice_settings = InvoiceAutomationSettings.from_env()

        transition = prepare_clickup_invoice_status_transition(
            clickup_summary=summary,
            settings=invoice_settings,
        )
        mutated_summary = summary
        if args.set_ready_status and transition.get("status") == "ready_to_update":
            if transition.get("status_field_id") and transition.get("target_status_option_id"):
                client.set_task_custom_field_value(
                    summary["task_id"],
                    transition["status_field_id"],
                    transition["target_status_option_id"],
                )
            else:
                client.update_task(
                    summary["task_id"],
                    status=invoice_settings.ready_status,
                    custom_task_ids=args.custom_task_ids,
                    team_id=args.team_id,
                )
            mutated_summary = _with_updated_custom_field_value(
                {**summary, "status": invoice_settings.ready_status},
                field_id=transition.get("status_field_id"),
                value=transition.get("target_status_option_id"),
            )

        preview = prepare_clickup_bc_sales_invoice_preview(
            clickup_summary=mutated_summary,
            bc_client=bc_client,
            settings=invoice_settings,
        )
        if not args.apply:
            _print(
                {
                    "mode": "dry_run",
                    "transition": transition,
                    "status_updated": args.set_ready_status and transition.get("status") == "ready_to_update",
                    "invoice_preview": preview,
                }
            )
            return

        if not args.writeback and _require_invoice_writeback_on_apply():
            _print(
                {
                    "mode": "blocked",
                    "transition": transition,
                    "status_updated": args.set_ready_status and transition.get("status") == "ready_to_update",
                    "result": {
                        "status": "writeback_required",
                        "message": (
                            "Refusing to create Business Central invoices without ClickUp PDF/comment "
                            "writeback. Re-run with --writeback, or set "
                            "CLICKUP_INVOICE_ALLOW_APPLY_WITHOUT_WRITEBACK=true for an explicit exception."
                        ),
                    },
                }
            )
            return

        issue_fn = issue_clickup_bc_sales_invoice if args.writeback else apply_clickup_bc_sales_invoice
        result = issue_fn(
            clickup_summary=mutated_summary,
            bc_client=bc_client,
            settings=invoice_settings,
        )

        if args.writeback and result.get("status") == "applied":
            try:
                delivery = finalize_clickup_issued_invoices(
                    clickup=client,
                    bc_client=bc_client,
                    clickup_summary=mutated_summary,
                    invoice_result=result,
                    settings=invoice_settings,
                    workspace_id=args.team_id or settings.default_workspace_id,
                    mark_status=True,
                )
            except Exception as exc:
                result = {**result, "status": "failed_post_creation", "message": str(exc)}
            else:
                result = {
                    **result,
                    "delivery": delivery,
                    "final_status_update": delivery.get("final_status_update"),
                }
        elif result.get("status") == "applied":
            result = {
                **result,
                "final_status_update": None,
                "writeback_warning": (
                    "Invoice writeback was not requested. ClickUp was not marked Facturada."
                ),
            }

        _print(
            {
                "mode": "applied",
                "transition": transition,
                "status_updated": args.set_ready_status and transition.get("status") == "ready_to_update",
                "result": result,
            }
        )
        return

    if args.command == "list-custom-fields":
        list_id = args.list_id or settings.default_customer_list_id
        if not list_id:
            parser.error(
                "--list-id is required unless CLICKUP_DEFAULT_CUSTOMER_LIST_ID is set."
            )
        _print(client.get_list_custom_fields(list_id))
        return

    if args.command == "sync-revenue-invoices":
        bc_settings = BusinessCentralSettings.from_env()
        bc_client = BusinessCentralClient(bc_settings)
        _print(
            run_revenue_invoice_sync(
                bc=bc_client,
                clickup=client,
                settings=RevenueInvoiceSyncSettings.from_env(),
                dry_run=not args.apply,
                invoice_no=args.invoice_no,
                full_review=args.full_review,
            )
        )
        return

    if args.command == "sync-ap-invoice":
        task = client.get_task(
            args.task_id,
            custom_task_ids=args.custom_task_ids,
            team_id=args.team_id,
            include_subtasks=False,
        )
        summary = summarize_task_for_customer_mapping(task)
        bc_settings = BusinessCentralSettings.from_env()
        bc_client = BusinessCentralClient(bc_settings)
        ap_settings = APPurchaseInvoiceSettings.from_env()
        pdf_contents = _download_task_pdf_attachments(client, task)

        if args.apply:
            result = apply_clickup_bc_purchase_invoice(
                clickup_summary=summary,
                bc_client=bc_client,
                settings=ap_settings,
                pdf_contents=pdf_contents,
            )
            if result.get("status") == "applied":
                try:
                    result = {
                        **result,
                        "clickup_comment": client.create_task_comment(
                            summary["task_id"],
                            comment_text=build_clickup_ap_transfer_comment(result),
                            notify_all=False,
                        ),
                    }
                except Exception as exc:
                    result = {
                        **result,
                        "clickup_comment_warning": str(exc),
                    }
            _print(
                {
                    "mode": "applied",
                    "result": result,
                }
            )
            return

        _print(
            {
                "mode": "dry_run",
                "result": prepare_clickup_bc_purchase_invoice_preview(
                    clickup_summary=summary,
                    bc_client=bc_client,
                    settings=ap_settings,
                    pdf_contents=pdf_contents,
                    compare_invoice_number=args.compare_bc_invoice_number,
                ),
            }
        )
        return

    if args.command == "resolve-whatsapp-route":
        whatsapp_settings = WhatsAppSettings.from_env()
        event = normalize_twilio_inbound(
            {
                "From": args.phone,
                "ProfileName": args.profile_name or "",
                "MessageSid": args.message_id,
                "Body": args.body,
                "NumMedia": "0",
            }
        )
        _print(
            route_customer_message(
                event,
                whatsapp_settings,
                clickup=client,
            ).__dict__
        )
        return

    parser.error(f"Unsupported command: {args.command}")


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def _require_invoice_writeback_on_apply() -> bool:
    value = os.getenv("CLICKUP_INVOICE_ALLOW_APPLY_WITHOUT_WRITEBACK", "").strip().lower()
    return value not in {"1", "true", "yes", "y", "on"}


def _with_updated_custom_field_value(
    summary: dict[str, object],
    *,
    field_id: str | int | None,
    value: str | int | None,
) -> dict[str, object]:
    if field_id is None or value is None:
        return summary
    custom_fields = summary.get("custom_fields") or {}
    if not isinstance(custom_fields, dict):
        return summary
    updated_fields = {}
    for field_name, details in custom_fields.items():
        if isinstance(details, dict) and details.get("id") == field_id:
            updated_fields[field_name] = {**details, "value": value}
        else:
            updated_fields[field_name] = details
    return {**summary, "custom_fields": updated_fields}


def _download_task_pdf_attachments(client: ClickUpClient, task: dict[str, object]) -> list[bytes]:
    contents: list[bytes] = []
    for attachment in task.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        name = str(attachment.get("title") or attachment.get("filename") or "").lower()
        url = str(attachment.get("url") or "").strip()
        if not url or not name.endswith(".pdf"):
            continue
        response = requests.get(
            url,
            headers=client._authorization_headers(),
            timeout=120,
        )
        response.raise_for_status()
        contents.append(response.content)
    return contents


if __name__ == "__main__":
    main()
