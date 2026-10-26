"""Read-only, market-scoped resolution of a shipment's linked CRM customer."""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from clickup_integration.client import ClickUpClient
from clickup_integration.config import ClickUpSettings
from clickup_integration.customer_rules import field_value, normalize_customer_name
from clickup_integration.mapping import resolve_dropdown_field, summarize_task_for_customer_mapping


CONSIGNEE_CRM_FIELD_ID = "5e1637c2-6bb2-4428-a592-06477fd13ef1"


def resolve_linked_crm_invoice_customer(
    *, custom_fields: dict[str, dict[str, Any]], customer_name: str,
    bc_client: Any, market: str,
) -> dict[str, Any] | None:
    # Mexico and shipments without a consignee CRM link retain their existing rules.
    if market != "GT":
        return None
    relationship = next((field for name, field in custom_fields.items()
                         if field.get("id") == CONSIGNEE_CRM_FIELD_ID
                         or name in {"Consignee’s relationship to CRM", "Consignee's relationship to CRM"}), {})
    links = relationship.get("value")
    if not links:
        return None
    result = {
        "source": "linked_clickup_crm", "customer_name": customer_name,
        "customer_id": None, "customer_number": None,
        "crm_relationship_field_id": relationship.get("id"),
    }

    def blocked(message: str) -> dict[str, Any]:
        return {**result, "status": "invalid_linked_crm_customer", "message": message}

    if not isinstance(links, list) or any(not isinstance(link, dict) or not link.get("id") for link in links):
        return blocked("The consignee CRM relationship has an invalid task reference.")
    task_ids = {str(link["id"]) for link in links}
    if len(task_ids) != 1:
        return blocked("The consignee CRM relationship must identify one customer.")
    crm_task_id = next(iter(task_ids))
    result["crm_task_id"] = crm_task_id
    try:
        crm_task = ClickUpClient(ClickUpSettings.from_env()).get_task(crm_task_id)
    except Exception:
        return blocked("The linked CRM customer could not be read; verify ClickUp access before retrying.")
    if str(crm_task.get("id") or "") != crm_task_id:
        return blocked("ClickUp returned a different CRM task identity.")
    crm = summarize_task_for_customer_mapping(crm_task)
    fields = crm["custom_fields"]
    alias = resolve_dropdown_field(fields.get("Clientes/")) or {}
    labels = [alias.get("name"), field_value(fields, field_name="Business Central Legal Name"), crm.get("name")]
    if normalize_customer_name(customer_name) not in {normalize_customer_name(str(label or "")) for label in labels}:
        # A shipment can bill a third party instead of its consignee.
        return None
    if crm.get("market") and crm["market"] != market:
        return blocked("The linked CRM customer belongs to a different market.")
    customer_id = field_value(fields, field_name="Business Central Customer ID")
    if not customer_id:
        return None
    try:
        customer_id = str(UUID(customer_id))
    except ValueError:
        return blocked("The linked CRM Business Central Customer ID is not a valid UUID.")
    try:
        customer = bc_client.get_customer_by_id(customer_id, market=market)
    except Exception:
        return blocked("The linked BC customer ID could not be read in the shipment's market.")
    if not customer or str(customer.get("id") or "").lower() != customer_id:
        return blocked("The linked BC customer ID was not found in the shipment's market.")
    number = str(customer.get("number") or "").strip()
    crm_number = field_value(fields, field_name="Business Central Customer Number")
    if not number or (crm_number and crm_number != number):
        return blocked("The CRM customer number conflicts with the BC customer selected by ID.")
    tax_ids = [field_value(fields, field_name=name) for name in ("Customer Tax ID", "Tax ID")]
    normalize_tax = lambda value: re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    bc_tax = normalize_tax(customer.get("taxRegistrationNumber"))
    if any(normalize_tax(tax) != bc_tax for tax in tax_ids if tax):
        return blocked("The CRM tax ID conflicts with the BC customer selected by ID.")
    return {
        **result, "status": "resolved", "customer_id": customer_id,
        "customer_number": number,
        "bc_display_name": customer.get("displayName") or customer.get("name"),
    }
