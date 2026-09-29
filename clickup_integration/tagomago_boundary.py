from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


TAGOMAGO_CUSTOMER_NUMBER = "C00067"
TAGOMAGO_MARKET = "MX"
TAGOMAGO_SOURCE_LIST_IDS = frozenset({"901704342223", "901704095060"})
TAGOMAGO_OCEAN_LIST_ID = "901703461634"


def generic_invoice_tagomago_blocker(
    *,
    clickup_summary: Mapping[str, Any] | None = None,
    invoice_result: Mapping[str, Any] | None = None,
    market: str | None = None,
    customer_field_names: Iterable[str] = (),
) -> dict[str, str] | None:
    """Identify TAGOMAGO work before a generic invoice path can mutate anything."""
    summary = clickup_summary or {}
    result = invoice_result or {}
    list_id = str(((summary.get("list") or {}).get("id")) or "").strip()
    invoice_type = str(summary.get("tagomago_invoice_type") or "").strip().casefold()
    if list_id in TAGOMAGO_SOURCE_LIST_IDS or invoice_type in {"warehouse", "distribution"}:
        return _blocked("restricted_tagomago_source")

    # Ocean invoices are a separate, USD workflow. Never relax the MXN
    # warehouse/distribution boundary merely because the customer is shared.
    if _is_tagomago_usd_ocean(summary, result, market=market):
        return None

    identity_text = " ".join(
        str(value or "")
        for value in (
            summary.get("name"),
            summary.get("reference"),
            result.get("reference"),
            result.get("externalDocumentNumber"),
        )
    ).casefold()
    if "tagomago" in identity_text:
        return _blocked("restricted_tagomago_provenance")

    markets = {
        str(value or "").strip().upper()
        for value in (market, summary.get("market"), result.get("market"))
        if str(value or "").strip()
    }
    customer_numbers = _named_customer_numbers(
        summary.get("custom_fields") or {},
        customer_field_names=customer_field_names,
    )
    customer_numbers.update(_recursive_customer_numbers(result))
    if TAGOMAGO_MARKET in markets and TAGOMAGO_CUSTOMER_NUMBER in customer_numbers:
        return _blocked("restricted_tagomago_customer")
    return None


def _is_tagomago_usd_ocean(
    summary: Mapping[str, Any], result: Mapping[str, Any], *, market: str | None
) -> bool:
    source_list = str(((summary.get("list") or {}).get("id")) or "")
    result_list = str(result.get("source_list_id") or "")
    if source_list and result_list and source_list != result_list:
        return False
    if (source_list or result_list) != TAGOMAGO_OCEAN_LIST_ID:
        return False
    markets = {str(v).upper() for v in (market, summary.get("market"), result.get("market")) if v}
    if markets != {"MX"}:
        return False
    if result:
        if result_list != TAGOMAGO_OCEAN_LIST_ID or result.get("currency") != "USD":
            return False
        if (result.get("shipment_metadata") or {}).get("product") != "OCEAN/FCL":
            return False
        # Check nested draft, posted and bill-to identities as well as the
        # resolved preview. This also protects duplicate recovery and delivery.
        if _recursive_customer_numbers(result) != {TAGOMAGO_CUSTOMER_NUMBER}:
            return False
        if _recursive_values(result, {"currencycode", "currency"}) != {"USD"}:
            return False
        if _recursive_values(result, {"lineobjectnumber", "itemnumber"}) & {"NAT00000012"}:
            return False
    if summary:
        from clickup_integration.mapping import resolve_dropdown_field

        fields = summary.get("custom_fields") or {}
        product = resolve_dropdown_field(fields.get("Product/") or fields.get("Product")) or {}
        if product.get("name") != "OCEAN/FCL":
            return False
        for name in ("Invoice Currency", "Currency"):
            field = fields.get(name) or {}
            if field.get("value") is not None:
                option = resolve_dropdown_field(field) or {}
                if str(option.get("name") or field["value"]).upper() != "USD":
                    return False
    # A source-only call is an early provenance check. The resolved preview is
    # checked again, including currency and customer, before header creation.
    return True


def _recursive_values(value: Any, names: set[str]) -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).replace("_", "").casefold() in names and isinstance(nested, str) and nested.strip():
                values.add(nested.strip().upper())
            elif isinstance(nested, (Mapping, list, tuple)):
                values.update(_recursive_values(nested, names))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            values.update(_recursive_values(nested, names))
    return values


def _named_customer_numbers(
    custom_fields: Any,
    *,
    customer_field_names: Iterable[str],
) -> set[str]:
    if not isinstance(custom_fields, Mapping):
        return set()
    accepted_names = {
        "business central customer number",
        *(str(name or "").strip().casefold() for name in customer_field_names),
    }
    values: set[str] = set()
    for name, field in custom_fields.items():
        if str(name or "").strip().casefold() not in accepted_names:
            continue
        raw_value = field.get("value") if isinstance(field, Mapping) else field
        normalized = str(raw_value or "").strip().upper()
        if normalized:
            values.add(normalized)
    return values


def _recursive_customer_numbers(value: Any) -> set[str]:
    numbers: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key or "").replace("_", "").casefold()
            if normalized_key in {"customernumber", "billtocustomernumber"}:
                normalized_value = str(nested or "").strip().upper()
                if normalized_value:
                    numbers.add(normalized_value)
            elif isinstance(nested, (Mapping, list, tuple)):
                numbers.update(_recursive_customer_numbers(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            numbers.update(_recursive_customer_numbers(nested))
    return numbers


def _blocked(matched_by: str) -> dict[str, str]:
    return {
        "reason": "generic_tagomago_path_prohibited",
        "matched_by": matched_by,
        "message": (
            "TAGOMAGO Mexico invoices must use the dedicated approval-ledger lifecycle; "
            "the generic invoice, delivery, email, and ClickUp writeback paths are prohibited."
        ),
    }
