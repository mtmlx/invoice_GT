from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from business_central_client.client import BusinessCentralClient
from business_central_client.config import Settings
from clickup_integration.invoice_delivery import INVOICE_EMAIL_SENDERS, validate_mx_invoice_email_readiness


MARKET = "GT"
TEST_RECIPIENT = "mario@mtmlogix.com"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Send one existing stamped GT or MX invoice through the internal BC email canary. "
            "This never creates or posts an invoice."
        )
    )
    parser.add_argument("--market", choices=("GT", "MX"), default=MARKET)
    parser.add_argument(
        "--expected-pdf-sha256",
        help="Mexico only: SHA256 of the independently reviewed PAC PDF snapshot.",
    )
    parser.add_argument(
        "--invoice-number",
        help="Existing posted invoice number. Omit to select one random eligible invoice.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually send the internal canary. Without this flag the command is read-only.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Allow one new canary only when the existing BC evidence is an explicit Failed "
            "outbox record. Queued, unknown, timed-out, and sent states are never retried."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional dotenv file containing the Business Central connection settings.",
    )
    args = parser.parse_args()
    market = args.market
    if market == "MX" and not args.invoice_number:
        parser.error("--invoice-number is required for a Mexico canary; random Mexico selection is prohibited.")
    expected_pdf_sha256 = args.expected_pdf_sha256 or ""
    if market == "MX" and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_pdf_sha256):
        parser.error("Mexico canaries require --expected-pdf-sha256 from an independently reviewed PAC PDF snapshot.")

    if args.env_file:
        load_dotenv(args.env_file, override=True)

    bc = BusinessCentralClient(Settings.from_env())
    fel_row = _select_invoice(bc, invoice_number=args.invoice_number, market=market)
    invoice_number = str(fel_row.get("number") or "").strip()
    posted_invoice = bc.get_posted_sales_invoice_by_number(invoice_number, market=market)
    if not posted_invoice:
        raise SystemExit(f"Posted Business Central invoice {invoice_number} was not found.")
    if str(posted_invoice.get("status") or "").strip().lower() == "canceled":
        raise SystemExit(f"Posted Business Central invoice {invoice_number} is canceled.")

    result: dict[str, Any] = {
        "status": "ready_to_send" if not args.apply else "sending",
        "creates_invoice": False,
        "market": market,
        "expected_sender": INVOICE_EMAIL_SENDERS[market],
        "expected_pdf_sha256": expected_pdf_sha256 if market == "MX" else None,
        "test_recipient": TEST_RECIPIENT,
        "invoice": {
            "id": posted_invoice.get("id"),
            "number": invoice_number,
            "customerNumber": posted_invoice.get("customerNumber"),
            "customerName": posted_invoice.get("customerName"),
            "externalDocumentNumber": posted_invoice.get("externalDocumentNumber"),
            "currencyCode": posted_invoice.get("currencyCode"),
            "totalAmountIncludingTax": posted_invoice.get("totalAmountIncludingTax"),
            "felStatus": fel_row.get("electronicDocumentStatus"),
            "fiscalUuid": fel_row.get("fiscalInvoiceNumberPac"),
            "invoiceEmailReadiness": fel_row.get("invoiceEmailReadiness"),
            "dueDate": posted_invoice.get("dueDate"),
        },
    }
    evidence_before = _get_bc_evidence(bc, invoice_number=invoice_number, market=market)
    result["bc_evidence_before"] = evidence_before
    fiscal_uuid = str(fel_row.get("fiscalInvoiceNumberPac") or "")
    blocked_reason = _evidence_blocker(evidence_before, market=market, fiscal_uuid=fiscal_uuid, expected_pdf_sha256=expected_pdf_sha256)
    if blocked_reason:
        result["status"] = "blocked_unverified_canary_evidence"
        result["reason"] = blocked_reason
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        raise SystemExit(2)

    if evidence_before.startswith("Sent|"):
        result["status"] = "already_sent_and_native_bc_evidence_verified"
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return
    if evidence_before.startswith("Outbox|"):
        outbox_status = _evidence_value(evidence_before, "Status").lower()
        if not args.apply:
            result["status"] = (
                "ready_to_retry_explicit_failed_outbox"
                if outbox_status == "failed"
                else "blocked_existing_bc_outbox_message"
            )
        elif outbox_status != "failed":
            result["status"] = "blocked_existing_bc_outbox_message"
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
            raise SystemExit(2)
        elif not args.retry_failed:
            result["status"] = "blocked_failed_outbox_requires_explicit_retry_flag"
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
            raise SystemExit(2)

    if not args.apply:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return

    try:
        if market == "MX":
            bc.send_posted_invoice_test_email_to_mario(
                str(fel_row["id"]), market=market, expected_pdf_sha256=expected_pdf_sha256,
            )
        else:
            bc.send_posted_invoice_test_email_to_mario(str(fel_row["id"]), market=market)
    except requests.Timeout:
        evidence_after = _wait_for_bc_evidence(bc, invoice_number=invoice_number, timeout_seconds=90, market=market)
        result["bc_evidence_after"] = evidence_after
        if evidence_after.startswith("Sent|") and not _evidence_blocker(evidence_after, market=market, fiscal_uuid=fiscal_uuid, expected_pdf_sha256=expected_pdf_sha256):
            result["status"] = "sent_and_native_bc_evidence_verified_after_client_timeout"
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
            return
        result["status"] = (
            "blocked_bc_outbox_after_client_timeout"
            if evidence_after.startswith("Outbox|")
            else "unresolved_after_client_timeout_no_retry"
        )
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        raise SystemExit(2)

    evidence_after = _wait_for_bc_evidence(bc, invoice_number=invoice_number, timeout_seconds=30, market=market)
    result["bc_evidence_after"] = evidence_after
    if not evidence_after.startswith("Sent|") or _evidence_blocker(evidence_after, market=market, fiscal_uuid=fiscal_uuid, expected_pdf_sha256=expected_pdf_sha256):
        result["status"] = "send_returned_without_native_bc_evidence_no_retry"
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        raise SystemExit(2)
    result["status"] = "sent_and_native_bc_evidence_verified"
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def _select_invoice(
    bc: BusinessCentralClient,
    *,
    invoice_number: str | None,
    market: str = MARKET,
) -> dict[str, Any]:
    if market == "MX" and not invoice_number:
        raise SystemExit("Mexico canaries require an explicit invoice number.")
    if invoice_number:
        row = bc.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
        if not row:
            raise SystemExit(f"FEL row for invoice {invoice_number} was not found.")
        rows = [row]
    else:
        rows = bc.get_posted_invoice_fel_descriptions(
            filters="cancelled eq false",
            top=100,
            order_by="systemModifiedAt desc",
            market=market,
        )

    eligible = [
        row
        for row in rows
        if str(row.get("electronicDocumentStatus") or "").strip().upper() == "STAMP RECEIVED"
        and not bool(row.get("cancelled"))
        and str(row.get("number") or "").strip()
    ]
    if not eligible:
        raise SystemExit(f"No eligible non-canceled {market} invoice with status Stamp Received was found.")
    recent_candidates = sorted(eligible, key=_invoice_sequence, reverse=True)[:20]
    selected = secrets.choice(recent_candidates)
    if market == "MX":
        validate_mx_invoice_email_readiness(selected)
    return selected


def _invoice_sequence(row: dict[str, Any]) -> int:
    digits = "".join(character for character in str(row.get("number") or "") if character.isdigit())
    return int(digits or 0)


def _wait_for_bc_evidence(
    bc: BusinessCentralClient,
    *,
    invoice_number: str,
    timeout_seconds: int,
    market: str = MARKET,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    evidence = "NotFound"
    while time.monotonic() < deadline:
        evidence = _get_bc_evidence(bc, invoice_number=invoice_number, market=market)
        if evidence:
            if evidence.startswith(("Sent|", "Outbox|", "SentWrongAccount|", "ConfigurationError|")):
                return evidence
        time.sleep(3)
    return evidence


def _get_bc_evidence(bc: BusinessCentralClient, *, invoice_number: str, market: str = MARKET) -> str:
    row = bc.get_invoice_email_canary_evidence_by_number(invoice_number, market=market)
    if not row:
        return "Unavailable"
    return str(row.get("evidence") or "Unavailable")


def _evidence_blocker(evidence: str, *, market: str, fiscal_uuid: str = "", expected_pdf_sha256: str = "") -> str | None:
    # A missing API row or unsupported evidence is not proof that no previous
    # attempt exists. Only native NotFound evidence permits a first attempt.
    if not evidence.startswith(("NotFound|", "Sent|", "Outbox|")):
        return "Native canary evidence is unavailable or ambiguous; no resend."
    if _evidence_value(evidence, "Sender").lower() != INVOICE_EMAIL_SENDERS[market]:
        return "The native canary evidence has an unexpected sender."
    if not _evidence_value(evidence, "AccountId"):
        return "The native canary evidence is missing the scenario account ID."
    if evidence.startswith(("Sent|", "Outbox|")):
        if _evidence_value(evidence, "Recipient").lower() != TEST_RECIPIENT:
            return "The native canary evidence has an unexpected recipient."
        if not _evidence_value(evidence, "MessageId"):
            return "The native canary evidence is missing the message ID."
    if market == "MX" and evidence.startswith("Sent|"):
        if not fiscal_uuid or _evidence_value(evidence, "FiscalUuid").casefold() != fiscal_uuid.casefold():
            return "The Mexico canary evidence does not match the posted fiscal UUID."
        if _evidence_value(evidence, "AttachmentCount") != "2":
            return "The Mexico canary did not confirm exactly two fiscal attachments."
        for key in ("PdfAttachmentSha256", "XmlAttachmentSha256"):
            if not re.fullmatch(r"[0-9a-fA-F]{64}", _evidence_value(evidence, key)):
                return "The Mexico canary is missing native fiscal attachment verification."
        if (not re.fullmatch(r"[0-9a-fA-F]{64}", expected_pdf_sha256)
                or _evidence_value(evidence, "PdfAttachmentSha256").casefold() != expected_pdf_sha256.casefold()):
            return "The Mexico canary PDF differs from the independently reviewed snapshot."
    return None


def _evidence_value(evidence: str, key: str) -> str:
    prefix = f"{key}="
    for component in evidence.split("|"):
        if component.startswith(prefix):
            return component[len(prefix) :].strip()
    return ""


if __name__ == "__main__":
    main()
