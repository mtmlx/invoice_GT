from __future__ import annotations

import re
import logging
from typing import Any

from business_central_client.client import BusinessCentralClient
from clickup_integration.customer_rules import (
    CLICKUP_CONTACT_NAME_1_FIELD_ID,
    field_value,
    location_formatted_address,
    normalize_customer_name,
    normalize_email,
    normalize_tax_id_digits,
    payment_method_code_from_credit_terms,
    resolve_clickup_credit_approved,
    resolve_clickup_credit_terms,
    split_bc_address_lines,
)
from clickup_integration.mapping import is_current_customer_status
from clickup_integration.writeback import _resolve_dropdown_option_name


CLICKUP_PHONE_FIELD_CANDIDATES = (
    "Contact Phone 1",
    "Contact Phone Number",
    "Phone",
    "Contact Phone 2",
    "Contact Phone 3",
    "Contact Phone 4",
    "Contact Phone 5",
    "Contact Phone 6",
)

CLICKUP_WEBSITE_FIELD = "Webpage"
CLICKUP_EMAIL_FIELD_CANDIDATES = (
    "Contact Email 1",
    "Contact E-mail 1",
    "Operations Email",
    "Sales email",
    "Finance email",
)
CLICKUP_TAX_ID_FIELD_CANDIDATES = (
    "Customer Tax ID",
    "Tax ID",
)
CLICKUP_ADDRESS_FIELD = "Customer Address"
BC_FIELD_NAMES = {
    "customer_id": "Business Central Customer ID",
    "customer_number": "Business Central Customer Number",
    "match_status": "BC Match Status",
}


def prepare_clickup_to_bc_customer_sync(
    *,
    clickup_summary: dict[str, Any],
    bc_client: BusinessCentralClient,
) -> dict[str, Any]:
    task_status = clickup_summary.get("status")
    if not is_current_customer_status(task_status):
        return {
            "status": "not_current_customer",
            "message": (
                "ClickUp task is not eligible for BC customer updates. "
                "Only tasks with status CURRENT CUSTOMER are processed."
            ),
            "task_status": task_status,
        }

    market = clickup_summary.get("market")
    if not market:
        return {
            "status": "no_market",
            "message": "ClickUp task does not resolve to a supported market from Owner Country/.",
            "task_status": task_status,
        }

    custom_fields = clickup_summary.get("custom_fields") or {}
    bc_customer_id = _field_value(custom_fields, BC_FIELD_NAMES["customer_id"])
    bc_customer_number = _field_value(custom_fields, BC_FIELD_NAMES["customer_number"])
    match_status_name = _match_status_name(custom_fields.get(BC_FIELD_NAMES["match_status"]))

    if match_status_name != "Confirmed":
        return {
            "status": "not_confirmed",
            "message": "ClickUp task does not have a confirmed BC match. Updates to BC are blocked.",
            "task_status": task_status,
            "market": market,
            "bc_match_status": match_status_name,
        }

    if not bc_customer_id:
        return {
            "status": "missing_bc_customer_id",
            "message": "ClickUp task does not have a Business Central Customer ID.",
            "task_status": task_status,
            "market": market,
            "bc_match_status": match_status_name,
        }

    bc_customer = bc_client.get_entity("customers", bc_customer_id, market=market)

    clickup_phone = _first_present_field(custom_fields, CLICKUP_PHONE_FIELD_CANDIDATES)
    clickup_website = field_value(custom_fields, field_name=CLICKUP_WEBSITE_FIELD)
    clickup_email = normalize_email(_first_present_field(custom_fields, CLICKUP_EMAIL_FIELD_CANDIDATES))
    clickup_contact_name = field_value(
        custom_fields,
        field_name="Contact Name 1",
        field_id=CLICKUP_CONTACT_NAME_1_FIELD_ID,
    )
    clickup_tax_id = normalize_tax_id_digits(_first_present_field(custom_fields, CLICKUP_TAX_ID_FIELD_CANDIDATES))
    clickup_legal_name = normalize_customer_name(
        field_value(custom_fields, field_name="Business Central Legal Name")
    )
    clickup_address = location_formatted_address(custom_fields, field_name=CLICKUP_ADDRESS_FIELD)
    clickup_address_line1, clickup_address_line2 = split_bc_address_lines(clickup_address)
    clickup_credit_terms = resolve_clickup_credit_terms(custom_fields)
    clickup_credit_approved = resolve_clickup_credit_approved(custom_fields)
    payment_term = (
        bc_client.resolve_payment_term(clickup_credit_terms, market=market)
        if clickup_credit_terms
        else None
    )
    payment_method_code = payment_method_code_from_credit_terms(clickup_credit_terms)
    payment_method = (
        bc_client.resolve_payment_method(payment_method_code, market=market)
        if payment_method_code
        else None
    )

    proposed_updates: dict[str, str] = {}
    comparison = {
        "display_name": {
            "clickup": clickup_legal_name or None,
            "bc": (bc_customer.get("displayName") or None),
            "will_update": False,
        },
        "email": {
            "clickup": clickup_email or None,
            "bc": (bc_customer.get("email") or None),
            "will_update": False,
        },
        "phone": {
            "clickup": clickup_phone or None,
            "bc": (bc_customer.get("phoneNumber") or None),
            "will_update": False,
        },
        "website": {
            "clickup": clickup_website or None,
            "bc": (bc_customer.get("website") or None),
            "will_update": False,
        },
        "tax_id": {
            "clickup": clickup_tax_id or None,
            "bc": (bc_customer.get("taxRegistrationNumber") or None),
            "will_update": False,
        },
        "address": {
            "clickup": " | ".join(part for part in [clickup_address_line1, clickup_address_line2] if part) or None,
            "bc": " | ".join(
                part for part in [bc_customer.get("addressLine1") or "", bc_customer.get("addressLine2") or ""]
                if part
            ) or None,
            "will_update": False,
        },
        "payment_terms": {
            "clickup": clickup_credit_terms or None,
            "bc": (bc_customer.get("paymentTermsId") or None),
            "will_update": False,
        },
        "payment_method": {
            "clickup": payment_method_code or None,
            "bc": (bc_customer.get("paymentMethodId") or None),
            "will_update": False,
        },
        "credit_limit": {
            "clickup": clickup_credit_approved,
            "bc": bc_customer.get("creditLimit"),
            "will_update": False,
        },
    }

    if clickup_legal_name and clickup_legal_name != (bc_customer.get("displayName") or ""):
        proposed_updates["displayName"] = clickup_legal_name
        comparison["display_name"]["will_update"] = True

    if clickup_email and clickup_email != (bc_customer.get("email") or ""):
        proposed_updates["email"] = clickup_email
        comparison["email"]["will_update"] = True

    if clickup_phone and not _phone_equivalent(clickup_phone, bc_customer.get("phoneNumber") or ""):
        proposed_updates["phoneNumber"] = clickup_phone
        comparison["phone"]["will_update"] = True

    if clickup_website and not _website_equivalent(clickup_website, bc_customer.get("website") or ""):
        proposed_updates["website"] = clickup_website
        comparison["website"]["will_update"] = True

    if clickup_tax_id and _digits_only(clickup_tax_id) != _digits_only(bc_customer.get("taxRegistrationNumber") or ""):
        proposed_updates["taxRegistrationNumber"] = clickup_tax_id
        comparison["tax_id"]["will_update"] = True

    bc_address_line1 = (bc_customer.get("addressLine1") or "").strip()
    bc_address_line2 = (bc_customer.get("addressLine2") or "").strip()
    if clickup_address_line1 and (
        clickup_address_line1 != bc_address_line1 or clickup_address_line2 != bc_address_line2
    ):
        proposed_updates["addressLine1"] = clickup_address_line1
        if clickup_address_line2 or bc_address_line2:
            proposed_updates["addressLine2"] = clickup_address_line2
        comparison["address"]["will_update"] = True

    if payment_term and payment_term["id"] != (bc_customer.get("paymentTermsId") or ""):
        proposed_updates["paymentTermsId"] = payment_term["id"]
        comparison["payment_terms"]["will_update"] = True

    if payment_method and payment_method["id"] != (bc_customer.get("paymentMethodId") or ""):
        proposed_updates["paymentMethodId"] = payment_method["id"]
        comparison["payment_method"]["will_update"] = True

    if clickup_credit_approved is not None and clickup_credit_approved != bc_customer.get("creditLimit"):
        proposed_updates["creditLimit"] = clickup_credit_approved
        comparison["credit_limit"]["will_update"] = True

    invoicing_extension_payload = {
        "cfdiCustomerName": clickup_legal_name or None,
        "vatRegistrationNumber": clickup_tax_id or None,
        "invoiceEmail": clickup_email or None,
        "correoFactura": clickup_email or None,
        "contactName": clickup_contact_name or None,
        "contactEmail": clickup_email or None,
        "contactPhone": clickup_phone or None,
        "paymentTermsCode": clickup_credit_terms or None,
        "paymentMethodCode": payment_method_code or None,
        "cashFlowPaymentTermsCode": clickup_credit_terms or None,
        "copySellToAddressTo": "Company",
        "taxIdentificationType": "Legal Entity",
        "generalBusinessPostingGroupCode": "NAC",
        "customerPostingGroupCode": "NAC",
    }
    extension_payload = {
        key: value for key, value in invoicing_extension_payload.items() if value not in {None, ""}
    }
    should_apply_extension = bool(getattr(bc_client.settings, "customer_invoicing_sync_path", None) and extension_payload)

    return {
        "status": "dry_run_ready" if proposed_updates or should_apply_extension else "no_changes",
        "task_status": task_status,
        "market": market,
        "bc_match_status": match_status_name,
        "task_id": clickup_summary.get("task_id"),
        "bc_customer_id": bc_customer_id,
        "bc_customer_number": bc_customer_number or bc_customer.get("number"),
        "bc_customer_name": bc_customer.get("displayName"),
        "clickup_sources": {
            "name_field": "Business Central Legal Name" if clickup_legal_name else None,
            "email_field": _first_present_field_name(custom_fields, CLICKUP_EMAIL_FIELD_CANDIDATES),
            "phone_field": _first_present_field_name(custom_fields, CLICKUP_PHONE_FIELD_CANDIDATES),
            "website_field": CLICKUP_WEBSITE_FIELD if clickup_website else None,
            "tax_id_field": _first_present_field_name(custom_fields, CLICKUP_TAX_ID_FIELD_CANDIDATES),
            "address_field": CLICKUP_ADDRESS_FIELD if clickup_address else None,
            "credit_terms_field_name": "Credit Terms / Credit Days Required" if clickup_credit_terms else None,
            "credit_approved_field_id": "54574add-833f-42a5-b027-3b0d64ef95af" if clickup_credit_approved is not None else None,
            "contact_name_field": "Contact Name 1" if clickup_contact_name else None,
        },
        "comparison": comparison,
        "proposed_bc_patch": proposed_updates,
        "proposed_bc_invoicing_payload": extension_payload,
    }


def apply_clickup_to_bc_customer_sync(
    *,
    clickup_summary: dict[str, Any],
    bc_client: BusinessCentralClient,
) -> dict[str, Any]:
    preview = prepare_clickup_to_bc_customer_sync(
        clickup_summary=clickup_summary,
        bc_client=bc_client,
    )
    if preview.get("status") != "dry_run_ready":
        return preview

    updated = None
    if preview["proposed_bc_patch"]:
        updated = bc_client.patch_entity(
            "customers",
            preview["bc_customer_id"],
            preview["proposed_bc_patch"],
            market=preview["market"],
        )
    extension_result = _apply_customer_invoicing_extension(
        bc_client=bc_client,
        customer_id=preview["bc_customer_id"],
        market=preview["market"],
        invoicing_payload=preview["proposed_bc_invoicing_payload"],
    )
    result = {
        **preview,
        "status": "applied",
        "updated_customer": updated,
    }
    if extension_result:
        result["invoicing_extension"] = extension_result
    return result


def _field_value(custom_fields: dict[str, Any], field_name: str) -> str:
    return field_value(custom_fields, field_name=field_name)


def _first_present_field(custom_fields: dict[str, Any], field_names: tuple[str, ...]) -> str:
    for field_name in field_names:
        value = _field_value(custom_fields, field_name)
        if value:
            return value
    return ""


def _first_present_field_name(custom_fields: dict[str, Any], field_names: tuple[str, ...]) -> str | None:
    for field_name in field_names:
        value = _field_value(custom_fields, field_name)
        if value:
            return field_name
    return None


def _match_status_name(field: dict[str, Any] | None) -> str | None:
    if not field:
        return None
    return _resolve_dropdown_option_name(field, field.get("value"))


def _phone_equivalent(left: str, right: str) -> bool:
    return _digits_only(left) == _digits_only(right) and bool(_digits_only(left))


def _website_equivalent(left: str, right: str) -> bool:
    return _normalize_website(left) == _normalize_website(right) and bool(_normalize_website(left))


def _digits_only(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def _normalize_website(value: str) -> str:
    normalized = (value or "").strip().lower().rstrip("/")
    normalized = re.sub(r"^https?://", "", normalized)
    return normalized


def _apply_customer_invoicing_extension(
    *,
    bc_client: BusinessCentralClient,
    customer_id: str,
    market: str,
    invoicing_payload: dict[str, Any],
) -> dict[str, Any] | None:
    path = getattr(bc_client.settings, "customer_invoicing_sync_path", None)
    if not path:
        return None

    payload = {key: value for key, value in invoicing_payload.items() if value not in {None, ""}}
    if not payload:
        return None

    try:
        response = bc_client.patch_company_path(
            path,
            payload,
            market=market,
            customer_id=customer_id,
        )
    except Exception:
        logging.getLogger(__name__).exception("Customer invoicing extension update failed customer=%s", customer_id)
        return {
            "status": "failed",
            "message": "Customer invoicing extension update failed. Consult the server logs.",
            "path": path,
        }

    return {
        "status": "applied",
        "path": path,
        "response": response,
    }
