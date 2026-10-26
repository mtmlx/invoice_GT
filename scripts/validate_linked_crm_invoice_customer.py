"""Validate live CRM resolution and invoice preview with all business writes blocked.

Run from the repository root with its normal BC/ClickUp environment configured:
    python -m scripts.validate_linked_crm_invoice_customer --task-id 86e27yzwp

The ready status is simulated on a deep copy only. Never invokes issue/apply,
stamping, delivery, or the production webhook.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from urllib.parse import urlparse

import requests

from business_central_client.client import BusinessCentralClient
from business_central_client.config import Settings
from clickup_integration.client import ClickUpClient
from clickup_integration.config import ClickUpSettings
from clickup_integration.invoice_sync import (
    InvoiceAutomationSettings, _resolve_invoice_customer,
    prepare_clickup_bc_sales_invoice_preview,
)
from clickup_integration.mapping import summarize_task_for_customer_mapping


def validate(task_id: str) -> dict:
    original_request = requests.sessions.Session.request
    reads = []
    blocked_writes = []

    def read_only_request(session, method, url, *args, **kwargs):
        parsed = urlparse(url)
        is_token = (method.upper() == "POST" and parsed.scheme == "https"
                    and parsed.hostname == "login.microsoftonline.com"
                    and parsed.path.endswith("/oauth2/v2.0/token"))
        if method.upper() != "GET" and not is_token:
            blocked_writes.append({"method": method, "host": parsed.hostname})
            raise RuntimeError("Business writes are disabled in this validation.")
        reads.append({"method": method.upper(), "host": parsed.hostname})
        return original_request(session, method, url, *args, **kwargs)

    requests.sessions.Session.request = read_only_request
    try:
        clickup = ClickUpClient(ClickUpSettings.from_env())
        bc = BusinessCentralClient(Settings.from_env())
        settings = InvoiceAutomationSettings.from_env()
        task = clickup.get_task(task_id)
        summary = summarize_task_for_customer_mapping(task)
        if summary["market"] != "GT" or settings.supported_market != "GT":
            raise RuntimeError("This validation is restricted to Guatemala.")
        fields = summary["custom_fields"]
        identity = _resolve_invoice_customer(
            custom_fields=fields, bc_client=bc, market="GT", config=settings,
            customer_id="", customer_number="",
        )
        if identity.get("source") != "linked_clickup_crm" or identity.get("status") != "resolved":
            raise RuntimeError("The live linked CRM identity did not resolve: " + json.dumps(identity))
        card = bc.get_customer_by_id(identity["customer_id"], market="GT")
        snapshot = deepcopy(summary)
        actual_invoice_status = None
        for name, field in snapshot["custom_fields"].items():
            if name in settings.invoice_status_field_names or field.get("id") in settings.invoice_status_field_ids:
                actual_invoice_status = deepcopy(fields[name]["value"])
                option = next(option for option in field.get("type_config", {}).get("options", [])
                              if option.get("name") == settings.ready_status)
                field["value"] = option["id"]
        if actual_invoice_status is None:
            raise RuntimeError("Could not simulate the invoice status field safely.")
        references = [summary.get("custom_id") or summary["task_id"]]

        def invoice_snapshot():
            ref = references[0].replace("'", "''")
            return bc.find_entities("salesInvoices", filters=f"startswith(externalDocumentNumber,'{ref}')",
                                    top=100, market="GT")

        before_invoices = invoice_snapshot()
        preview = prepare_clickup_bc_sales_invoice_preview(clickup_summary=snapshot, bc_client=bc, settings=settings)
        after_invoices = invoice_snapshot()
        after_task = clickup.get_task(task_id)
        unchanged = before_invoices == after_invoices and task == after_task
        result = {
            "task_id": task_id, "custom_id": summary.get("custom_id"),
            "mode": "read_only_preview", "simulated_ready_status_in_memory_only": True,
            "customer_resolution": identity, "bc_tax_id": card.get("taxRegistrationNumber"),
            "payment_terms_id": card.get("paymentTermsId"), "preview_status": preview.get("status"),
            "preview_message": preview.get("message"),
            "invoice_validation": preview.get("invoice_validation"),
            "product_validation": preview.get("product_validation"),
            "existing_invoices": [{k: row.get(k) for k in ("id", "number", "customerNumber", "status", "externalDocumentNumber")}
                                  for row in before_invoices],
            "proposed_invoice_customers": [invoice["proposed_bc_payload"].get("customerId")
                                           for invoice in preview.get("proposed_bc_invoices", [])],
            "source_records_unchanged": unchanged, "blocked_write_attempts": blocked_writes,
            "business_write_requests": 0,
            "read_requests": len([read for read in reads if read["method"] == "GET"]),
        }
        print(json.dumps(result, indent=2, default=str))
        if not unchanged or blocked_writes or preview.get("status") not in {"dry_run_ready", "duplicate_invoice"}:
            raise RuntimeError("Read-only validation did not pass.")
        if any(customer_id != identity["customer_id"] for customer_id in result["proposed_invoice_customers"]):
            raise RuntimeError("A proposed invoice has a different BC customer ID.")
        return result
    finally:
        requests.sessions.Session.request = original_request


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    validate(parser.parse_args().task_id)
