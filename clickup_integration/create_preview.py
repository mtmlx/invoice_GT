from __future__ import annotations

import re
import logging
from typing import Any

from business_central_client.client import BusinessCentralClient
from clickup_integration.customer_rules import (
    CLICKUP_CONTACT_NAME_1_FIELD_ID,
    dropdown_label,
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
from clickup_integration.mapping import is_current_customer_status, resolve_dropdown_field
from clickup_integration.writeback import (
    BC_FIELD_DEFS,
    _find_clickup_field,
    build_bc_customer_url,
    resolve_clickup_dropdown_option_id,
)


LEGAL_ENTITY_MARKERS = (
    "s.a",
    "sociedad",
    "anonima",
    "s de rl",
    "de c.v",
    "sa de cv",
    "llc",
    "corp",
    "corporacion",
    "inc",
    "ltda",
)

CLICKUP_EMAIL_FIELD_CANDIDATES = (
    "Contact Email 1",
    "Contact E-mail 1",
    "Operations Email",
    "Sales email",
    "Finance email",
)

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

CLICKUP_TAX_ID_FIELD_CANDIDATES = (
    "Customer Tax ID",
    "Tax ID",
)

CLICKUP_ADDRESS_FIELD = "Customer Address"


def prepare_clickup_bc_customer_create_preview(
    *,
    clickup_summary: dict[str, Any],
    bc_client: BusinessCentralClient,
    current_match_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_status = clickup_summary.get("status")
    if not is_current_customer_status(task_status):
        return {
            "status": "not_current_customer",
            "message": (
                "ClickUp task is not eligible for BC customer creation preview. "
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
    legal_name = field_value(custom_fields, field_name="Business Central Legal Name")
    selected_customer_name = _selected_customer_name(custom_fields)
    task_name = (clickup_summary.get("name") or "").strip()
    display_name = normalize_customer_name(
        legal_name or _preferred_customer_name(task_name, selected_customer_name)
    )
    website = field_value(custom_fields, field_name="Webpage")
    email = normalize_email(_first_present_field(custom_fields, CLICKUP_EMAIL_FIELD_CANDIDATES))
    phone = _first_present_field(custom_fields, CLICKUP_PHONE_FIELD_CANDIDATES)
    contact_name = field_value(
        custom_fields,
        field_name="Contact Name 1",
        field_id=CLICKUP_CONTACT_NAME_1_FIELD_ID,
    )
    tax_id = normalize_tax_id_digits(_first_present_field(custom_fields, CLICKUP_TAX_ID_FIELD_CANDIDATES))
    address = location_formatted_address(custom_fields, field_name=CLICKUP_ADDRESS_FIELD)
    address_line1, address_line2 = split_bc_address_lines(address)
    credit_terms_label = resolve_clickup_credit_terms(custom_fields)
    credit_approved = resolve_clickup_credit_approved(custom_fields)

    missing_required_fields: list[str] = []
    if not legal_name:
        missing_required_fields.append("Business Central Legal Name")
    if not tax_id:
        missing_required_fields.append("Customer Tax ID or Tax ID")
    if not email:
        missing_required_fields.append("Contact E-mail 1 or Contact Email 1")
    if not phone:
        missing_required_fields.append("Contact Phone 1")
    if not address:
        missing_required_fields.append("Customer Address")
    if not credit_terms_label:
        missing_required_fields.append("Credit Terms or Credit Days Required")

    if missing_required_fields:
        return {
            "status": "missing_required_fields",
            "message": (
                "ClickUp customer is not ready for Business Central creation. "
                "Complete the required fields before retrying the customer sync."
            ),
            "task_status": task_status,
            "market": market,
            "task_id": clickup_summary.get("task_id"),
            "custom_id": clickup_summary.get("custom_id"),
            "missing_required_fields": missing_required_fields,
        }

    company = bc_client.get_company_metadata(market=market)
    company_name = (company or {}).get("name") or (company or {}).get("displayName")
    payment_term = (
        bc_client.resolve_payment_term(credit_terms_label, market=market)
        if credit_terms_label
        else None
    )
    payment_method_code = payment_method_code_from_credit_terms(credit_terms_label)
    payment_method = (
        bc_client.resolve_payment_method(payment_method_code, market=market)
        if payment_method_code
        else None
    )

    payload = {
        "displayName": display_name,
        "type": "Company",
        "country": market,
    }
    if website:
        payload["website"] = website
    if email:
        payload["email"] = email
    if phone:
        payload["phoneNumber"] = phone
    if tax_id:
        payload["taxRegistrationNumber"] = tax_id
    if address_line1:
        payload["addressLine1"] = address_line1
    if address_line2:
        payload["addressLine2"] = address_line2
    if payment_term:
        payload["paymentTermsId"] = payment_term["id"]
    if payment_method:
        payload["paymentMethodId"] = payment_method["id"]
    if credit_approved is not None:
        payload["creditLimit"] = credit_approved

    invoicing_extension_payload = {
        "cfdiCustomerName": display_name,
        "vatRegistrationNumber": tax_id or None,
        "invoiceEmail": email or None,
        "correoFactura": email or None,
        "contactName": contact_name or None,
        "contactEmail": email or None,
        "contactPhone": phone or None,
        "paymentTermsCode": credit_terms_label or None,
        "paymentMethodCode": payment_method_code or None,
        "cashFlowPaymentTermsCode": credit_terms_label or None,
        "copySellToAddressTo": "Company",
        "taxIdentificationType": "Legal Entity",
        "generalBusinessPostingGroupCode": "NAC",
        "customerPostingGroupCode": "NAC",
    }

    warnings: list[str] = []
    missing_recommended_fields: list[str] = []

    if not selected_customer_name:
        warnings.append("ClickUp field `Clientes/` is empty, so the legal customer name is inferred from the task name.")
    if not legal_name:
        missing_recommended_fields.append("Business Central Legal Name")
        warnings.append("Business Central Legal Name is empty, so the create payload falls back to the best available ClickUp customer name.")
    if display_name == task_name and selected_customer_name and task_name != selected_customer_name:
        warnings.append("Task name was chosen over the `Clientes/` value as the preferred BC display name.")
    if not tax_id:
        missing_recommended_fields.append("Customer Tax ID")
        warnings.append("Tax ID is empty, so duplicate prevention will rely on name and website only.")
    if not email:
        missing_recommended_fields.append("Contact Email 1")
    if not phone:
        missing_recommended_fields.append("Contact Phone 1")
    if not website:
        missing_recommended_fields.append("Webpage")
    if not address:
        missing_recommended_fields.append("Customer Address")
    if not credit_terms_label:
        missing_recommended_fields.append("Credit Terms")
    elif not payment_term:
        warnings.append(f"Could not resolve BC payment terms for ClickUp credit term '{credit_terms_label}'.")
    if credit_terms_label and not payment_method:
        warnings.append(f"Could not resolve BC payment method for ClickUp credit term '{credit_terms_label}'.")

    if current_match_result:
        match_status = current_match_result.get("status")
        if match_status == "likely_match":
            warnings.append("A likely BC match already exists, so creating a new customer would probably create a duplicate.")
        elif match_status == "possible_match":
            top_candidate = (current_match_result.get("candidates") or [None])[0]
            if top_candidate:
                warnings.append(
                    "A possible BC match exists and should be manually cleared before creating a new customer: "
                    f"{top_candidate.get('number')} {top_candidate.get('displayName')}."
                )

    field_lookup = {
        name: details
        for name, details in custom_fields.items()
        if name in {field_def["name"] for field_def in BC_FIELD_DEFS.values()}
    }
    missing_writeback_fields = [
        field_def["name"]
        for field_def in BC_FIELD_DEFS.values()
        if field_def["name"] not in field_lookup
    ]

    match_status_preview = _resolve_dropdown_option(
        field_lookup.get(BC_FIELD_DEFS["status"]["name"]),
        "Confirmed",
    )

    customer_number_placeholder = "<BC response.number>"
    expected_link = None
    if company_name:
        expected_link = build_bc_customer_url(
            tenant_id=bc_client.settings.tenant_id,
            environment=bc_client.settings.environment,
            company_name=company_name,
            customer_number=customer_number_placeholder,
        )

    return {
        "status": "dry_run_ready",
        "task_status": task_status,
        "market": market,
        "company_name": company_name,
        "clickup_source": {
            "task_id": clickup_summary.get("task_id"),
            "custom_id": clickup_summary.get("custom_id"),
            "task_name": task_name,
            "selected_customer_name": selected_customer_name,
            "website": website or None,
            "email": email or None,
            "phone": phone or None,
            "tax_id": tax_id or None,
            "address": address or None,
            "credit_terms": credit_terms_label or None,
            "credit_approved": credit_approved,
            "contact_name": contact_name or None,
        },
        "proposed_bc_payload": payload,
        "proposed_bc_invoicing_payload": invoicing_extension_payload,
        "missing_recommended_fields": missing_recommended_fields,
        "warnings": warnings,
        "current_match_result": current_match_result,
        "expected_clickup_writeback": {
            "bc_customer_number": customer_number_placeholder,
            "bc_customer_id": "<BC response.id>",
            "bc_customer_link": expected_link,
            "bc_match_status": match_status_preview,
            "missing_clickup_fields": missing_writeback_fields,
        },
        "notes": [
            "This preview does not create a Business Central customer.",
            "Business Central will assign the native customer number and internal id at creation time.",
        ],
    }


def apply_clickup_bc_customer_create(
    *,
    clickup_summary: dict[str, Any],
    current_match_result: dict[str, Any],
    bc_client: BusinessCentralClient,
) -> dict[str, Any]:
    preview = prepare_clickup_bc_customer_create_preview(
        clickup_summary=clickup_summary,
        bc_client=bc_client,
        current_match_result=current_match_result,
    )
    if preview.get("status") != "dry_run_ready":
        return preview

    match_status = (current_match_result or {}).get("status")
    if match_status not in {None, "no_match"}:
        return {
            **preview,
            "status": "blocked_duplicate_risk",
            "message": (
                "Customer creation is blocked because the current matcher still reports "
                f"{match_status}."
            ),
        }

    created_customer = bc_client.post_to_company(
        "/companies({company_id})/customers",
        preview["proposed_bc_payload"],
        market=preview["market"],
    )
    extension_result = _apply_customer_invoicing_extension(
        bc_client=bc_client,
        customer_id=created_customer["id"],
        market=preview["market"],
        invoicing_payload=preview["proposed_bc_invoicing_payload"],
    )
    writeback = prepare_clickup_bc_created_customer_writeback(
        clickup_summary=clickup_summary,
        created_customer=created_customer,
        market=preview["market"],
        bc_client=bc_client,
    )
    result = {
        **preview,
        "status": "applied",
        "created_customer": created_customer,
        "writeback": writeback,
    }
    if extension_result:
        result["invoicing_extension"] = extension_result
    return result


def prepare_clickup_bc_created_customer_writeback(
    *,
    clickup_summary: dict[str, Any],
    created_customer: dict[str, Any],
    market: str,
    bc_client: BusinessCentralClient,
) -> dict[str, Any]:
    company = bc_client.get_company_metadata(market=market)
    if not company:
        raise ValueError(f"Could not resolve company metadata for market {market}.")

    company_name = company.get("name") or company.get("displayName")
    if not company_name:
        raise ValueError("Could not determine the Business Central company name for the link.")

    link = build_bc_customer_url(
        tenant_id=bc_client.settings.tenant_id,
        environment=bc_client.settings.environment,
        company_name=company_name,
        customer_number=created_customer["number"],
    )

    field_lookup = {
        key: _find_clickup_field(
            clickup_summary.get("custom_fields") or {},
            field_name=field_def["name"],
            fallback_field_id=field_def.get("id"),
        )
        for key, field_def in BC_FIELD_DEFS.items()
    }
    missing_fields = [
        BC_FIELD_DEFS[key]["name"]
        for key, details in field_lookup.items()
        if details is None
    ]
    if missing_fields:
        missing = ", ".join(missing_fields)
        raise ValueError(f"ClickUp task is missing required BC write-back fields: {missing}")

    status_value = resolve_clickup_dropdown_option_id(field_lookup["status"], "Confirmed")

    return {
        "task_id": clickup_summary["task_id"],
        "market": market,
        "bc_customer_number": created_customer["number"],
        "bc_customer_id": created_customer["id"],
        "bc_customer_link": link,
        "bc_legal_name": created_customer.get("displayName"),
        "bc_match_status": status_value,
        "field_ids": {
            "number": field_lookup["number"]["id"],
            "id": field_lookup["id"]["id"],
            "link": field_lookup["link"]["id"],
            "legal_name": field_lookup["legal_name"]["id"],
            "status": field_lookup["status"]["id"],
        },
    }


def _field_value(custom_fields: dict[str, Any], field_name: str) -> str:
    return field_value(custom_fields, field_name=field_name)


def _first_present_field(custom_fields: dict[str, Any], field_names: tuple[str, ...]) -> str:
    for field_name in field_names:
        value = _field_value(custom_fields, field_name)
        if value:
            return value
    return ""


def _location_formatted_address(custom_fields: dict[str, Any], field_name: str) -> str:
    return location_formatted_address(custom_fields, field_name=field_name)


def _selected_customer_name(custom_fields: dict[str, Any]) -> str:
    selected_customer = custom_fields.get("Clientes/")
    if not selected_customer:
        return ""
    resolved = resolve_dropdown_field(selected_customer)
    return ((resolved or {}).get("name") or "").strip()


def _preferred_customer_name(task_name: str, selected_customer_name: str) -> str:
    candidates = [candidate.strip() for candidate in [task_name, selected_customer_name] if candidate and candidate.strip()]
    if not candidates:
        return ""
    ranked = sorted(candidates, key=_customer_name_rank, reverse=True)
    return ranked[0]


def _customer_name_rank(value: str) -> tuple[int, int]:
    normalized = value.lower()
    marker_score = sum(1 for marker in LEGAL_ENTITY_MARKERS if marker in normalized)
    word_score = len(re.findall(r"[a-z0-9]+", normalized))
    return (marker_score, word_score)


def _resolve_dropdown_option(field: dict[str, Any] | None, label: str) -> dict[str, Any] | None:
    if not field:
        return None
    for option in (field.get("type_config") or {}).get("options", []):
        if option.get("name") == label:
            return {"label": label, "option_id": option.get("id")}
    return {"label": label, "option_id": None}


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
