from __future__ import annotations

import logging
import os
import json
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from business_central_client.client import BusinessCentralClient
from clickup_integration.customer_rules import field_value
from clickup_integration.mapping import resolve_dropdown_field
from clickup_integration.writeback import prepare_clickup_bc_invoice_writeback


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InvoiceChargeMapping:
    charge_name: str
    clickup_field_name: str
    clickup_field_id: str
    bc_item_number: str
    bc_description: str
    tax_group: str | None = None
    quantity_basis: str = "shipment"


@dataclass(frozen=True)
class InvoiceAutomationSettings:
    ready_status: str
    ok_finops_status: str
    eta_horizon_days: int
    supported_market: str
    supported_currency: str
    invoice_status_field_names: tuple[str, ...]
    eta_field_names: tuple[str, ...]
    currency_field_names: tuple[str, ...]
    reference_field_names: tuple[str, ...]
    invoice_date_field_names: tuple[str, ...]
    posting_date_field_names: tuple[str, ...]
    due_date_field_names: tuple[str, ...]
    freight_field_names: tuple[str, ...]
    inland_field_names: tuple[str, ...]
    destination_field_names: tuple[str, ...]
    bc_customer_id_field_names: tuple[str, ...]
    bc_customer_number_field_names: tuple[str, ...]
    bc_invoice_number_field_names: tuple[str, ...]
    bc_invoice_id_field_names: tuple[str, ...]
    freight_account_number: str | None
    inland_account_number: str | None
    destination_account_number: str | None
    charge_mappings: tuple[InvoiceChargeMapping, ...] = ()
    split_invoice_by_item_prefix: bool = True
    invoice_status_field_ids: tuple[str, ...] = ()
    eta_field_ids: tuple[str, ...] = ()
    bc_customer_name_field_names: tuple[str, ...] = ()
    bc_customer_name_field_ids: tuple[str, ...] = ()
    shipment_booking_field_names: tuple[str, ...] = ("Booking number/", "Booking Number", "Booking")
    shipment_container_field_names: tuple[str, ...] = (
        "Container(s) number(s)/",
        "Container Numbers",
        "Containers",
        "Container",
    )
    shipment_container_count_field_names: tuple[str, ...] = (
        "Number of Containers",
        "Container Count",
        "Containers Count",
    )
    product_field_names: tuple[str, ...] = ("Product/", "Product")
    airway_bill_field_names: tuple[str, ...] = (
        "Airway Bill",
        "Airway Bill ",
        "AWB",
        "HAWB",
        "MAWB",
        "Agent's Reference",
        "MTM Booking",
    )
    int_split_customer_numbers: tuple[str, ...] = ("C00056",)
    int_split_freight_charge_names: tuple[str, ...] = ("Freight (Ocean/Truck/Air)",)
    int_split_emergency_charge_names: tuple[str, ...] = ("Emergency Surcharge",)

    @classmethod
    def from_env(cls) -> "InvoiceAutomationSettings":
        supported_market = os.getenv("CLICKUP_INVOICE_MARKET", "GT").strip().upper() or "GT"
        charge_mapping_path = _env_invoice_charge_mapping_path(supported_market)
        return cls(
            ready_status=os.getenv("CLICKUP_INVOICE_READY_STATUS", "Listo para facturar").strip()
            or "Listo para facturar",
            ok_finops_status=os.getenv("CLICKUP_INVOICE_OK_FINOPS_STATUS", "OK Finops").strip()
            or "OK Finops",
            eta_horizon_days=_env_int("CLICKUP_INVOICE_ETA_HORIZON_DAYS", default=10),
            supported_market=supported_market,
            supported_currency=os.getenv("CLICKUP_INVOICE_CURRENCY", "USD").strip().upper() or "USD",
            invoice_status_field_names=_env_csv(
                "CLICKUP_INVOICE_STATUS_FIELD_NAMES",
                default=(
                    "Estatus de facturación (USD)/",
                    "Estatus de Facturacion (USD)",
                    "Estatus de Facturacion",
                    "Invoice Status",
                ),
            ),
            invoice_status_field_ids=_env_csv(
                "CLICKUP_INVOICE_STATUS_FIELD_IDS",
                default=("b436ff28-24d8-4a5a-9f7e-66623401dee1",),
            ),
            eta_field_names=_env_csv("CLICKUP_INVOICE_ETA_FIELD_NAMES", default=("ETA",)),
            eta_field_ids=_env_csv(
                "CLICKUP_INVOICE_ETA_FIELD_IDS",
                default=("736ddd1d-33da-4ff8-a128-f7f3f738987d",),
            ),
            currency_field_names=_env_csv(
                "CLICKUP_INVOICE_CURRENCY_FIELD_NAMES",
                default=("Invoice Currency", "Currency"),
            ),
            reference_field_names=_env_csv(
                "CLICKUP_INVOICE_REFERENCE_FIELD_NAMES",
                default=("Reference", "Customer Reference", "PO Number"),
            ),
            invoice_date_field_names=_env_csv(
                "CLICKUP_INVOICE_DATE_FIELD_NAMES",
                default=("Invoice Date",),
            ),
            posting_date_field_names=_env_csv(
                "CLICKUP_INVOICE_POSTING_DATE_FIELD_NAMES",
                default=("Posting Date",),
            ),
            due_date_field_names=_env_csv(
                "CLICKUP_INVOICE_DUE_DATE_FIELD_NAMES",
                default=("Due Date",),
            ),
            freight_field_names=_env_csv(
                "CLICKUP_INVOICE_FREIGHT_FIELD_NAMES",
                default=("Freight",),
            ),
            inland_field_names=_env_csv(
                "CLICKUP_INVOICE_INLAND_FIELD_NAMES",
                default=("Inland",),
            ),
            destination_field_names=_env_csv(
                "CLICKUP_INVOICE_DESTINATION_FIELD_NAMES",
                default=("Destination Charges", "Destination Charge"),
            ),
            bc_customer_id_field_names=_env_csv(
                "CLICKUP_INVOICE_BC_CUSTOMER_ID_FIELD_NAMES",
                default=("Business Central Customer ID",),
            ),
            bc_customer_number_field_names=_env_csv(
                "CLICKUP_INVOICE_BC_CUSTOMER_NUMBER_FIELD_NAMES",
                default=("Business Central Customer Number",),
            ),
            bc_customer_name_field_names=_env_csv(
                "CLICKUP_INVOICE_CUSTOMER_NAME_FIELD_NAMES",
                default=("Invoice to (Consignee's name)",),
            ),
            bc_customer_name_field_ids=_env_csv(
                "CLICKUP_INVOICE_CUSTOMER_NAME_FIELD_IDS",
                default=("729c2b40-6a61-4908-b0ed-3d6f92e72bcd",),
            ),
            bc_invoice_number_field_names=_env_csv(
                "CLICKUP_INVOICE_BC_INVOICE_NUMBER_FIELD_NAMES",
                default=("Business Central Invoice Number",),
            ),
            bc_invoice_id_field_names=_env_csv(
                "CLICKUP_INVOICE_BC_INVOICE_ID_FIELD_NAMES",
                default=("Business Central Invoice ID",),
            ),
            shipment_booking_field_names=_env_csv(
                "CLICKUP_INVOICE_SHIPMENT_BOOKING_FIELD_NAMES",
                default=("Booking number/", "Booking Number", "Booking"),
            ),
            shipment_container_field_names=_env_csv(
                "CLICKUP_INVOICE_SHIPMENT_CONTAINER_FIELD_NAMES",
                default=("Container(s) number(s)/", "Container Numbers", "Containers", "Container"),
            ),
            shipment_container_count_field_names=_env_csv(
                "CLICKUP_INVOICE_SHIPMENT_CONTAINER_COUNT_FIELD_NAMES",
                default=("Number of Containers", "Container Count", "Containers Count"),
            ),
            product_field_names=_env_csv(
                "CLICKUP_INVOICE_PRODUCT_FIELD_NAMES",
                default=("Product/", "Product"),
            ),
            airway_bill_field_names=_env_csv(
                "CLICKUP_INVOICE_AIRWAY_BILL_FIELD_NAMES",
                default=(
                    "Airway Bill",
                    "Airway Bill ",
                    "AWB",
                    "HAWB",
                    "MAWB",
                    "Agent's Reference",
                    "MTM Booking",
                ),
            ),
            freight_account_number=os.getenv(
                f"BC_MARKET_{supported_market}_FREIGHT_ACCOUNT_NUMBER",
                "",
            ).strip()
            or None,
            inland_account_number=os.getenv(
                f"BC_MARKET_{supported_market}_INLAND_ACCOUNT_NUMBER",
                "",
            ).strip()
            or None,
            destination_account_number=os.getenv(
                f"BC_MARKET_{supported_market}_DESTINATION_ACCOUNT_NUMBER",
                "",
            ).strip()
            or None,
            charge_mappings=load_invoice_charge_mappings(charge_mapping_path)
            if charge_mapping_path
            else (),
            split_invoice_by_item_prefix=load_invoice_charge_mapping_split_by_item_prefix(
                charge_mapping_path,
                default=True,
            )
            if charge_mapping_path
            else True,
            int_split_customer_numbers=_env_csv(
                "CLICKUP_INVOICE_INT_SPLIT_CUSTOMER_NUMBERS",
                default=("C00056",),
            ),
            int_split_freight_charge_names=_env_csv(
                "CLICKUP_INVOICE_INT_SPLIT_FREIGHT_CHARGE_NAMES",
                default=("Freight (Ocean/Truck/Air)",),
            ),
            int_split_emergency_charge_names=_env_csv(
                "CLICKUP_INVOICE_INT_SPLIT_EMERGENCY_CHARGE_NAMES",
                default=("Emergency Surcharge",),
            ),
        )


def prepare_clickup_invoice_status_transition(
    *,
    clickup_summary: dict[str, Any],
    settings: InvoiceAutomationSettings | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    config = settings or InvoiceAutomationSettings.from_env()
    as_of = today or date.today()
    custom_fields = clickup_summary.get("custom_fields") or {}
    invoice_status = _resolve_invoice_status(custom_fields, config, fallback=clickup_summary.get("status"))
    task_status = invoice_status["status"]
    market = (clickup_summary.get("market") or "").strip().upper()
    if market != config.supported_market:
        return {
            "status": "ignored_market",
            "message": f"Invoice automation only runs for market {config.supported_market}.",
            "market": market or None,
            "task_status": task_status,
        }

    currency = _resolve_currency_code(custom_fields, config)
    if currency != config.supported_currency:
        return {
            "status": "ignored_currency",
            "message": (
                f"Invoice automation only runs for currency {config.supported_currency}."
            ),
            "market": market,
            "currency": currency,
            "task_status": task_status,
        }

    if _status_equals(task_status, config.ready_status):
        return {
            "status": "already_ready_to_invoice",
            "message": f"Invoice status is already {config.ready_status}.",
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "status_source": invoice_status["source"],
        }

    if not _status_equals(task_status, config.ok_finops_status):
        return {
            "status": "ignored_status",
            "message": f"Task status is not {config.ok_finops_status}.",
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "status_source": invoice_status["source"],
        }

    eta_date = _resolve_eta_date(clickup_summary, config)
    if eta_date is None:
        return {
            "status": "missing_eta",
            "message": "ETA is required before the task can move to invoicing readiness.",
            "market": market,
            "currency": currency,
            "task_status": task_status,
        }

    if eta_date > as_of + timedelta(days=config.eta_horizon_days):
        return {
            "status": "eta_outside_window",
            "message": (
                f"ETA is outside the {config.eta_horizon_days}-day invoicing window."
            ),
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "eta_date": eta_date.isoformat(),
        }

    return {
        "status": "ready_to_update",
        "action": "set_ready_to_invoice",
        "market": market,
        "currency": currency,
        "task_status": task_status,
        "status_source": invoice_status["source"],
        "target_status": config.ready_status,
        "status_field_id": invoice_status.get("field_id"),
        "target_status_option_id": invoice_status.get("ready_option_id"),
        "eta_date": eta_date.isoformat(),
    }


def prepare_clickup_bc_sales_invoice_preview(
    *,
    clickup_summary: dict[str, Any],
    bc_client: BusinessCentralClient,
    settings: InvoiceAutomationSettings | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    config = settings or InvoiceAutomationSettings.from_env()
    as_of = today or date.today()
    custom_fields = clickup_summary.get("custom_fields") or {}
    invoice_status = _resolve_invoice_status(custom_fields, config, fallback=clickup_summary.get("status"))
    task_status = invoice_status["status"]
    market = (clickup_summary.get("market") or "").strip().upper()

    if market != config.supported_market:
        return {
            "status": "unsupported_market",
            "message": f"Invoice creation only supports market {config.supported_market}.",
            "market": market or None,
            "task_status": task_status,
        }

    currency = _resolve_currency_code(custom_fields, config)
    if currency != config.supported_currency:
        return {
            "status": "unsupported_currency",
            "message": f"Invoice creation only supports currency {config.supported_currency}.",
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "status_source": invoice_status["source"],
        }

    if not _status_equals(task_status, config.ready_status):
        return {
            "status": "not_ready_to_invoice",
            "message": f"Task status must be {config.ready_status} before creating the invoice.",
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "status_source": invoice_status["source"],
        }

    customer_id = _first_present_field(custom_fields, config.bc_customer_id_field_names)
    customer_number = _first_present_field(custom_fields, config.bc_customer_number_field_names)
    customer_resolution = _resolve_invoice_customer(
        custom_fields=custom_fields,
        bc_client=bc_client,
        market=market,
        config=config,
        customer_id=customer_id,
        customer_number=customer_number,
    )
    if customer_resolution["status"] == "ambiguous":
        return {
            "status": "ambiguous_customer",
            "message": customer_resolution["message"],
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "customer_resolution": customer_resolution,
        }
    if customer_resolution["customer_id"]:
        customer_id = customer_resolution["customer_id"]
    if customer_resolution["customer_number"]:
        customer_number = customer_resolution["customer_number"]

    bc_customer = _resolve_bc_customer_card(
        bc_client=bc_client,
        market=market,
        customer_id=customer_id,
        customer_number=customer_number,
    )
    customer_currency = str((bc_customer or {}).get("currencyCode") or "").strip().upper()
    if customer_currency and customer_currency != currency:
        return {
            "status": "customer_currency_mismatch",
            "message": (
                f"Business Central customer {customer_number or customer_id} is configured in "
                f"{customer_currency}, but the ClickUp invoice currency is {currency}."
            ),
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "customer_resolution": {
                **customer_resolution,
                "bc_customer_currency": customer_currency,
            },
        }
    if customer_id or customer_number:
        customer_fel_readiness = _validate_customer_fel_readiness(
            bc_client=bc_client,
            bc_customer=bc_customer,
            market=market,
            customer_number=customer_number,
            customer_id=customer_id,
        )
        if customer_fel_readiness["status"] != "ready":
            return {
                "status": "customer_fel_readiness_failed",
                "message": customer_fel_readiness["message"],
                "market": market,
                "currency": currency,
                "task_status": task_status,
                "customer_resolution": customer_resolution,
                "customer_fel_readiness": customer_fel_readiness,
            }

    market_invoice_settings = _resolve_market_invoice_settings(
        bc_client=bc_client,
        bc_customer=bc_customer,
        market=market,
        customer_number=customer_number,
        customer_id=customer_id,
    )
    if market_invoice_settings["status"] != "ready":
        return {
            "status": "market_invoice_readiness_failed",
            "message": market_invoice_settings["message"],
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "customer_resolution": customer_resolution,
            "market_invoice_settings": market_invoice_settings,
        }

    reference = _resolve_reference(clickup_summary, custom_fields, config)
    shipment_metadata = _resolve_shipment_metadata(clickup_summary, custom_fields, config)
    eta_date = _resolve_eta_date(clickup_summary, config)
    invoice_date = _resolve_date_from_fields(custom_fields, config.invoice_date_field_names) or as_of
    posting_date = _resolve_date_from_fields(custom_fields, config.posting_date_field_names) or invoice_date
    payment_terms_id = str((bc_customer or {}).get("paymentTermsId") or "").strip()

    missing_fields: list[str] = []
    if not customer_id and not customer_number:
        missing_fields.append("Business Central Customer ID or Business Central Customer Number")
    if not reference:
        missing_fields.append("Reference/custom_id/task_id")
    if not payment_terms_id:
        missing_fields.append("Business Central customer Payment Terms")

    if config.charge_mappings:
        charge_inputs = [
            _build_mapped_charge_input(custom_fields=custom_fields, mapping=mapping)
            for mapping in config.charge_mappings
        ]
        missing_charge_label = "At least one non-zero mapped charge line"
    else:
        charge_inputs = [
            _build_charge_input(
                custom_fields=custom_fields,
                amount_field_names=config.freight_field_names,
                charge_name="freight",
                account_number=config.freight_account_number,
                description="Freight",
            ),
            _build_charge_input(
                custom_fields=custom_fields,
                amount_field_names=config.inland_field_names,
                charge_name="inland",
                account_number=config.inland_account_number,
                description="Inland",
            ),
            _build_charge_input(
                custom_fields=custom_fields,
                amount_field_names=config.destination_field_names,
                charge_name="destination_charges",
                account_number=config.destination_account_number,
                description="Destination charges",
            ),
        ]
        missing_charge_label = "At least one non-zero charge line (freight, inland, or destination charges)"

    parse_errors = [charge["error"] for charge in charge_inputs if charge.get("error")]
    if parse_errors:
        return {
            "status": "invalid_charge_data",
            "message": "; ".join(parse_errors),
            "market": market,
            "currency": currency,
            "task_status": task_status,
        }

    active_charges = [charge for charge in charge_inputs if charge.get("amount") is not None and charge["amount"] > 0]
    if not active_charges:
        missing_fields.append(missing_charge_label)

    if missing_fields:
        return {
            "status": "missing_required_fields",
            "message": "Missing required invoice fields.",
            "missing_fields": missing_fields,
            "market": market,
            "currency": currency,
            "task_status": task_status,
        }

    product_for_gate = _resolve_product_for_invoice_gate(custom_fields, config)
    active_charges = [
        _apply_product_charge_description_overrides(charge=charge, product=product_for_gate)
        for charge in active_charges
    ]

    line_payloads: list[dict[str, Any]] = []
    line_sources: list[dict[str, Any]] = []
    for charge in active_charges:
        item_number = charge.get("item_number")
        if item_number:
            item = bc_client.resolve_item_by_number(item_number, market=market)
            if not item:
                return {
                    "status": "missing_bc_item",
                    "message": (
                        f"BC item {item_number} for {charge['charge_name']} was not found in market {market}."
                    ),
                    "market": market,
                    "currency": currency,
                    "task_status": task_status,
                }

            amount = Decimal(str(charge["amount"]))
            quantity_details = _resolve_invoice_line_quantity(
                charge=charge,
                amount=amount,
                custom_fields=custom_fields,
                config=config,
                product=product_for_gate,
            )
            if quantity_details.get("error"):
                return {
                    "status": "invalid_line_quantity",
                    "message": quantity_details["error"],
                    "market": market,
                    "currency": currency,
                    "task_status": task_status,
                    "charge_name": charge["charge_name"],
                }
            line_payloads.append(
                {
                    "lineType": "Item",
                    "lineObjectNumber": item.get("number") or item_number,
                    "itemId": item["id"],
                    "description": charge["description"],
                    "quantity": quantity_details["quantity"],
                    "unitPrice": quantity_details["unit_price"],
                }
            )
            line_sources.append(
                {
                    "charge_name": charge["charge_name"],
                    "invoice_group": _invoice_group_from_item_number(item.get("number") or item_number),
                    "amount": float(amount),
                    "quantity": quantity_details["quantity"],
                    "unit_price": quantity_details["unit_price"],
                    "quantity_basis": quantity_details["quantity_basis"],
                    "source_field": charge["source_field"],
                    "source_field_id": charge.get("source_field_id"),
                    "item_number": item.get("number") or item_number,
                    "description": charge["description"],
                    "description_override": charge.get("description_override"),
                    "tax_group": charge.get("tax_group"),
                }
            )
            continue

        account_number = charge.get("account_number")
        if not account_number:
            return {
                "status": "missing_account_mapping",
                "message": f"No BC account number is configured for {charge['charge_name']}.",
                "market": market,
                "currency": currency,
                "task_status": task_status,
            }

        account = bc_client.resolve_account_by_number(account_number, market=market)
        if not account:
            return {
                "status": "missing_bc_account",
                "message": (
                    f"BC account {account_number} for {charge['charge_name']} was not found in market {market}."
                ),
                "market": market,
                "currency": currency,
                "task_status": task_status,
            }

        amount = Decimal(str(charge["amount"]))
        quantity_details = _resolve_invoice_line_quantity(
            charge=charge,
            amount=amount,
            custom_fields=custom_fields,
            config=config,
            product=product_for_gate,
        )
        if quantity_details.get("error"):
            return {
                "status": "invalid_line_quantity",
                "message": quantity_details["error"],
                "market": market,
                "currency": currency,
                "task_status": task_status,
                "charge_name": charge["charge_name"],
            }
        line_payloads.append(
            {
                "lineType": "Account",
                "lineObjectNumber": account.get("number") or account_number,
                "accountId": account["id"],
                "description": charge["description"],
                "quantity": quantity_details["quantity"],
                "unitPrice": quantity_details["unit_price"],
            }
        )
        line_sources.append(
            {
                "charge_name": charge["charge_name"],
                "invoice_group": "ACCOUNT",
                "amount": float(amount),
                "quantity": quantity_details["quantity"],
                "unit_price": quantity_details["unit_price"],
                "quantity_basis": quantity_details["quantity_basis"],
                "source_field": charge["source_field"],
                "account_number": account.get("number") or account_number,
                "description": charge["description"],
            }
        )

    customer_purchase_order_reference, header_warnings = _bounded_customer_purchase_order_reference(
        shipment_metadata["shipment_number"] or reference
    )
    header_payload = {
        "currencyCode": currency,
        "externalDocumentNumber": reference,
        "customerPurchaseOrderReference": customer_purchase_order_reference,
        "invoiceDate": invoice_date.isoformat(),
        "postingDate": posting_date.isoformat(),
        "paymentTermsId": payment_terms_id,
    }
    if customer_id:
        header_payload["customerId"] = customer_id
    if customer_number:
        header_payload["customerNumber"] = customer_number

    customer_split_rule = _resolve_customer_int_split_rule(
        customer_number=customer_number,
        config=config,
    )
    customer_split_validation = _validate_customer_int_split_rule(
        line_sources=line_sources,
        customer_split_rule=customer_split_rule,
    )
    if customer_split_validation["status"] != "passed":
        return {
            "status": "customer_int_split_validation_failed",
            "message": customer_split_validation["message"],
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "reference": reference,
            "customer_number": customer_number or None,
            "customer_invoice_rule": customer_split_rule,
            "customer_split_validation": customer_split_validation,
            "line_sources": line_sources,
        }
    if customer_split_rule:
        line_sources = _apply_customer_int_split_rule(
            line_sources=line_sources,
            customer_split_rule=customer_split_rule,
        )

    proposed_invoices = _build_proposed_invoice_groups(
        header_payload=header_payload,
        line_payloads=line_payloads,
        line_sources=line_sources,
        split_by_item_prefix=bool(config.charge_mappings) and config.split_invoice_by_item_prefix,
        shipment_metadata=shipment_metadata,
    )
    invoice_validation = _validate_proposed_invoice_totals(
        line_payloads=line_payloads,
        line_sources=line_sources,
        proposed_invoices=proposed_invoices,
    )
    if invoice_validation["status"] != "passed":
        return {
            "status": "invoice_validation_failed",
            "message": "Mapped ClickUp billable fields do not reconcile to the proposed Business Central invoices.",
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "reference": reference,
            "invoice_validation": invoice_validation,
            "proposed_bc_invoices": proposed_invoices,
        }

    product_validation = _validate_product_invoice_gate(
        product=product_for_gate,
        line_sources=line_sources,
        proposed_invoices=proposed_invoices,
        shipment_metadata=shipment_metadata,
    )
    if product_validation["status"] != "passed":
        return {
            "status": "product_validation_failed",
            "message": product_validation["message"],
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "reference": reference,
            "product_validation": product_validation,
            "invoice_validation": invoice_validation,
            "proposed_bc_invoices": proposed_invoices,
            "line_sources": line_sources,
        }

    duplicate_invoices: list[dict[str, Any]] = []
    for proposed_invoice in proposed_invoices:
        duplicate_check = _find_existing_invoice(
            bc_client=bc_client,
            market=market,
            reference=proposed_invoice["proposed_bc_payload"]["externalDocumentNumber"],
            customer_number=customer_number or None,
            customer_id=customer_id or None,
        )
        if duplicate_check:
            duplicate_invoices.append(
                {
                    "invoice_group": proposed_invoice["invoice_group"],
                    "reference": proposed_invoice["proposed_bc_payload"]["externalDocumentNumber"],
                    "existing_invoice": duplicate_check,
                }
            )

    if duplicate_invoices:
        return {
            "status": "duplicate_invoice",
            "message": "One or more Business Central sales invoices already exist for this shipment reference.",
            "market": market,
            "currency": currency,
            "task_status": task_status,
            "reference": duplicate_invoices[0]["reference"],
            "invoice_group": duplicate_invoices[0]["invoice_group"],
            "existing_invoice": duplicate_invoices[0]["existing_invoice"],
            "duplicate_invoices": duplicate_invoices,
            "invoice_validation": invoice_validation,
            "product_validation": product_validation,
            "line_sources": line_sources,
            "shipment_metadata": shipment_metadata,
            "header_warnings": header_warnings,
            "proposed_bc_invoices": proposed_invoices,
        }

    return {
        "status": "dry_run_ready",
        "market": market,
        "currency": currency,
        "task_status": task_status,
        "status_source": invoice_status["source"],
        "reference": reference,
        "invoice_count": len(proposed_invoices),
        "invoice_groups": [invoice["invoice_group"] for invoice in proposed_invoices],
        "customer_id": customer_id or None,
        "customer_number": customer_number or None,
        "customer_resolution": customer_resolution,
        "customer_invoice_rule": customer_split_rule,
        "customer_split_validation": customer_split_validation,
        "market_invoice_settings": market_invoice_settings,
        "shipment_metadata": shipment_metadata,
        "header_warnings": header_warnings,
        "eta_date": eta_date.isoformat() if eta_date else None,
        "proposed_bc_payload": proposed_invoices[0]["proposed_bc_payload"],
        "proposed_bc_line_payloads": line_payloads,
        "line_sources": line_sources,
        "invoice_validation": invoice_validation,
        "product_validation": product_validation,
        "proposed_bc_invoices": proposed_invoices,
    }


def apply_clickup_bc_sales_invoice(
    *,
    clickup_summary: dict[str, Any],
    bc_client: BusinessCentralClient,
    settings: InvoiceAutomationSettings | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    config = settings or InvoiceAutomationSettings.from_env()
    preview = prepare_clickup_bc_sales_invoice_preview(
        clickup_summary=clickup_summary,
        bc_client=bc_client,
        settings=config,
        today=today,
    )
    if preview.get("status") != "dry_run_ready":
        logger.info(
            "Invoice creation skipped task_id=%s status=%s message=%s",
            clickup_summary.get("task_id"),
            preview.get("status"),
            preview.get("message"),
        )
        return preview

    proposed_invoices = preview.get("proposed_bc_invoices") or [
        {
            "invoice_group": "ALL",
            "proposed_bc_payload": preview["proposed_bc_payload"],
            "proposed_bc_line_payloads": preview["proposed_bc_line_payloads"],
        }
    ]
    created_invoices: list[dict[str, Any]] = []
    created_lines: list[dict[str, Any]] = []
    for proposed_invoice in proposed_invoices:
        try:
            created_invoice = bc_client.create_sales_invoice(
                proposed_invoice["proposed_bc_payload"],
                market=preview["market"],
            )
        except Exception as exc:
            logger.exception(
                "BC sales invoice header creation failed task_id=%s reference=%s invoice_group=%s",
                clickup_summary.get("task_id"),
                proposed_invoice["proposed_bc_payload"].get("externalDocumentNumber"),
                proposed_invoice.get("invoice_group"),
            )
            return {
                **preview,
                "status": "failed",
                "message": "Invoice header creation failed. Review the recorded state before retrying.",
                "created_invoices": created_invoices,
                "created_lines": created_lines,
            }

        mx_payment_fields_update = None
        if preview["market"] == "MX":
            try:
                mx_payment_fields_update = _set_mx_payment_fields_on_draft(
                    bc_client=bc_client,
                    sales_invoice_id=str(created_invoice["id"]),
                    market=preview["market"],
                    market_invoice_settings=preview.get("market_invoice_settings") or {},
                )
            except Exception as exc:
                logger.exception(
                    "BC MX payment-field update failed task_id=%s invoice_id=%s invoice_group=%s",
                    clickup_summary.get("task_id"),
                    created_invoice.get("id"),
                    proposed_invoice.get("invoice_group"),
                )
                return {
                    **preview,
                    "status": "failed_partial",
                    "failed_stage": "set_mx_payment_fields",
                    "message": "Invoice payment-field update failed. Review the existing draft before retrying.",
                    "created_invoice": {
                        "invoice_group": proposed_invoice.get("invoice_group"),
                        **created_invoice,
                    },
                    "created_invoices": [
                        {
                            "invoice_group": proposed_invoice.get("invoice_group"),
                            **created_invoice,
                        }
                    ],
                    "created_lines": created_lines,
                }

        created_invoices.append(
            {
                "invoice_group": proposed_invoice.get("invoice_group"),
                "mx_payment_fields_update": mx_payment_fields_update,
                **created_invoice,
            }
        )
        try:
            for line_payload in proposed_invoice["proposed_bc_line_payloads"]:
                created_line = bc_client.create_sales_invoice_line(
                    created_invoice["id"],
                    line_payload,
                    market=preview["market"],
                )
                created_lines.append(
                    {
                        "invoice_group": proposed_invoice.get("invoice_group"),
                        "invoice_id": created_invoice.get("id"),
                        **created_line,
                    }
                )
        except Exception as exc:
            logger.exception(
                "BC sales invoice line creation failed task_id=%s invoice_id=%s invoice_group=%s",
                clickup_summary.get("task_id"),
                created_invoice.get("id"),
                proposed_invoice.get("invoice_group"),
            )
            return {
                **preview,
                "status": "failed_partial",
                "message": "Invoice line creation failed. Review the existing draft before retrying.",
                "created_invoice": created_invoices[0] if created_invoices else None,
                "created_invoices": created_invoices,
                "created_lines": created_lines,
            }

        logger.info(
            "Created BC sales invoice task_id=%s reference=%s invoice_group=%s invoice_id=%s invoice_number=%s",
            clickup_summary.get("task_id"),
            proposed_invoice["proposed_bc_payload"].get("externalDocumentNumber"),
            proposed_invoice.get("invoice_group"),
            created_invoice.get("id"),
            created_invoice.get("number"),
        )

    writeback_invoice = _combined_created_invoice_for_writeback(created_invoices)
    invoice_writeback = prepare_clickup_bc_invoice_writeback(
        clickup_summary=clickup_summary,
        created_invoice=writeback_invoice,
        invoice_field_names={
            "invoice_number": config.bc_invoice_number_field_names[0],
            "invoice_id": config.bc_invoice_id_field_names[0],
        },
        require_all_fields=False,
    )
    return {
        **preview,
        "status": "applied",
        "created_invoice": writeback_invoice,
        "created_invoices": created_invoices,
        "created_lines": created_lines,
        "invoice_writeback": invoice_writeback,
    }


def issue_clickup_bc_sales_invoice(
    *,
    clickup_summary: dict[str, Any],
    bc_client: BusinessCentralClient,
    settings: InvoiceAutomationSettings | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    result = apply_clickup_bc_sales_invoice(
        clickup_summary=clickup_summary,
        bc_client=bc_client,
        settings=settings,
        today=today,
    )
    completed_stages: list[str] = []
    retry_existing_posted_invoices = _existing_duplicate_invoices_for_retry(result)
    if result.get("status") != "applied" and not retry_existing_posted_invoices:
        return {
            **result,
            "completed_stages": completed_stages,
            "failed_stage": result.get("failed_stage") or "create_sales_invoice",
        }

    config = settings or InvoiceAutomationSettings.from_env()
    market = result["market"]
    completed_stages.append("reuse_existing_posted_invoice" if retry_existing_posted_invoices else "create_sales_invoice")
    posted_invoices: list[dict[str, Any]] = []
    finalized_invoices: list[dict[str, Any]] = []
    current_stage = "post_sales_invoice"

    try:
        if retry_existing_posted_invoices:
            current_stage = "reuse_existing_posted_invoice"
            posted_invoices = retry_existing_posted_invoices
        else:
            for created_invoice in result.get("created_invoices") or []:
                invoice_group = created_invoice.get("invoice_group")
                invoice_id = str(created_invoice.get("id") or "").strip()
                if not invoice_id:
                    raise ValueError("Created invoice is missing its Business Central id.")

                post_response = bc_client.post_sales_invoice(invoice_id, market=market)
                posted_invoice = _resolve_posted_sales_invoice_after_post(
                    bc_client=bc_client,
                    created_invoice=created_invoice,
                    market=market,
                    expected_customer=next(
                        item["proposed_bc_payload"] for item in result["proposed_bc_invoices"]
                        if item.get("invoice_group") == invoice_group
                    ),
                )
                posted_invoices.append(
                    {
                        **posted_invoice,
                        "invoice_group": invoice_group,
                        "post_response": post_response,
                    }
                )

            completed_stages.append("post_sales_invoice")

        for posted_invoice in posted_invoices:
            current_stage = "sync_fel_descriptions"
            invoice_number = str(posted_invoice.get("number") or "").strip()
            if not invoice_number:
                raise ValueError("Posted invoice is missing its Business Central number.")

            fel_row = _wait_for_posted_invoice_fel_row(
                bc_client=bc_client,
                invoice_number=invoice_number,
                market=market,
            )
            if market == "MX":
                sync_response = {"status": "not_applicable", "market": "MX"}
                fel_row_after_sync = fel_row
                current_stage = "stamp_mx_invoice"
            else:
                if _is_stamp_received(fel_row):
                    sync_response = {"status": "already_stamp_received"}
                    fel_row_after_sync = fel_row
                else:
                    sync_response = bc_client.sync_posted_invoice_fel_line_descriptions(
                        fel_row["id"],
                        market=market,
                    )
                    fel_row_after_sync = (
                        bc_client.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
                        or fel_row
                    )
                if "sync_fel_descriptions" not in completed_stages:
                    completed_stages.append("sync_fel_descriptions")
                current_stage = "stamp_fel_invoice"

            if _is_stamp_received(fel_row_after_sync):
                stamp_response = {"status": "already_stamp_received"}
                fel_row_after_stamp = fel_row_after_sync
            elif market == "MX":
                stamp_response = bc_client.stamp_mx_invoice(fel_row["id"], market=market)
                fel_row_after_stamp = _wait_for_stamp_received(
                    bc_client=bc_client,
                    invoice_number=invoice_number,
                    market=market,
                )
            else:
                stamp_response = bc_client.stamp_posted_invoice_fel(fel_row["id"], market=market)
                fel_row_after_stamp = _wait_for_stamp_received(
                    bc_client=bc_client,
                    invoice_number=invoice_number,
                    market=market,
                )
            posted_invoice_after_stamp = (
                bc_client.get_entity("salesInvoices", posted_invoice["id"], market=market)
                or posted_invoice
            )
            gt_registered_invoice = bc_client.get_gt_registered_invoice_by_number(
                invoice_number,
                market=market,
            ) if market == "GT" else None
            finalized_invoices.append(
                {
                    "invoice_group": posted_invoice.get("invoice_group"),
                    "number": invoice_number,
                    "externalDocumentNumber": posted_invoice.get("externalDocumentNumber"),
                    "posted_invoice_after_stamp": posted_invoice_after_stamp,
                    "custom_api_row_after_sync": fel_row_after_sync,
                    "custom_api_row_after_stamp": fel_row_after_stamp,
                    "gt_registered_invoice_after_stamp": gt_registered_invoice,
                    "sync_descriptions_response": sync_response,
                    "stamp_response": stamp_response,
                }
            )
            completed_stamp_stage = "stamp_mx_invoice" if market == "MX" else "stamp_fel_invoice"
            if completed_stamp_stage not in completed_stages:
                completed_stages.append(completed_stamp_stage)
    except Exception as exc:
        logger.exception(
            "BC invoice post/FEL issue failed task_id=%s stage=%s",
            clickup_summary.get("task_id"),
            current_stage,
        )
        return {
            **result,
            "status": "failed_post_creation",
            "message": "Invoice finalization failed. Review the recorded posting and certification stages before retrying.",
            "completed_stages": completed_stages,
            "failed_stage": current_stage,
            "posted_invoices": posted_invoices,
            "finalized_invoices": finalized_invoices,
        }

    writeback_invoice = _combined_created_invoice_for_writeback(
        [
            {
                "invoice_group": invoice.get("invoice_group"),
                **(invoice.get("posted_invoice_after_stamp") or {}),
            }
            for invoice in finalized_invoices
        ]
    )
    invoice_writeback = prepare_clickup_bc_invoice_writeback(
        clickup_summary=clickup_summary,
        created_invoice=writeback_invoice,
        invoice_field_names={
            "invoice_number": config.bc_invoice_number_field_names[0],
            "invoice_id": config.bc_invoice_id_field_names[0],
        },
        require_all_fields=False,
    )
    return {
        **result,
        "status": "applied",
        "created_invoice": writeback_invoice,
        "invoice_writeback": invoice_writeback,
        "completed_stages": completed_stages,
        "posted_invoices": posted_invoices,
        "finalized_invoices": finalized_invoices,
    }


def _existing_duplicate_invoices_for_retry(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("status") != "duplicate_invoice":
        return []

    proposed_invoices = result.get("proposed_bc_invoices") or []
    duplicate_invoices = result.get("duplicate_invoices") or []
    if not proposed_invoices or len(duplicate_invoices) != len(proposed_invoices):
        return []

    posted_invoices: list[dict[str, Any]] = []
    market = str(result.get("market") or "").strip().upper() or None
    for duplicate in duplicate_invoices:
        existing_invoice = duplicate.get("existing_invoice") or {}
        expected = [item["proposed_bc_payload"] for item in proposed_invoices
                    if item.get("invoice_group") == duplicate.get("invoice_group")]
        if len(expected) != 1 or not _invoice_customer_matches(existing_invoice, expected[0]):
            return []
        invoice_number = str(existing_invoice.get("number") or "").strip()
        if not _looks_posted_invoice_number(invoice_number, market=market):
            return []
        fel_row = existing_invoice.get("existing_fel_row") or {}
        if _is_canceled_sales_invoice(existing_invoice):
            return []
        posted_invoices.append(
            {
                **existing_invoice,
                "invoice_group": duplicate.get("invoice_group"),
                "existing_fel_status": fel_row.get("electronicDocumentStatus"),
                "retry_existing_posted_invoice": True,
            }
        )

    return posted_invoices


def _resolve_posted_sales_invoice_after_post(
    *,
    bc_client: BusinessCentralClient,
    created_invoice: dict[str, Any],
    market: str,
    expected_customer: dict[str, Any],
) -> dict[str, Any]:
    invoice_id = str(created_invoice.get("id") or "").strip()
    external_document_number = str(created_invoice.get("externalDocumentNumber") or "").strip()
    for _attempt in range(3):
        posted_invoice = bc_client.get_entity("salesInvoices", invoice_id, market=market)
        if posted_invoice and _looks_posted_invoice_number(posted_invoice.get("number"), market=market):
            if not _invoice_customer_matches(posted_invoice, expected_customer):
                raise ValueError("Posted invoice customer does not match the intended customer.")
            return posted_invoice
        if external_document_number:
            posted_invoice = bc_client.get_posted_sales_invoice_by_external_document_number(
                external_document_number,
                market=market,
            )
            if posted_invoice and _looks_posted_invoice_number(posted_invoice.get("number"), market=market):
                if not _invoice_customer_matches(posted_invoice, expected_customer):
                    raise ValueError("Posted invoice customer does not match the intended customer.")
                return posted_invoice
        time.sleep(2)
    raise ValueError(
        f"Business Central did not return a posted sales invoice after posting draft {created_invoice.get('number')}."
    )


def _wait_for_posted_invoice_fel_row(
    *,
    bc_client: BusinessCentralClient,
    invoice_number: str,
    market: str,
) -> dict[str, Any]:
    for _attempt in range(5):
        row = bc_client.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
        if row:
            return row
        time.sleep(2)
    raise ValueError(f"Business Central FEL API row was not available for posted invoice {invoice_number}.")


def _wait_for_stamp_received(
    *,
    bc_client: BusinessCentralClient,
    invoice_number: str,
    market: str,
) -> dict[str, Any]:
    last_row: dict[str, Any] | None = None
    for _attempt in range(6):
        row = bc_client.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
        if row:
            last_row = row
            if _is_stamp_received(row):
                return row
        time.sleep(2)
    status = (last_row or {}).get("electronicDocumentStatus")
    error = (last_row or {}).get("errorDescription")
    raise ValueError(
        f"FEL stamp was not received for invoice {invoice_number}. "
        f"Status: {status or 'unknown'}. Error: {error or 'none'}."
    )


def _is_stamp_received(row: dict[str, Any]) -> bool:
    return " ".join(str(row.get("electronicDocumentStatus") or "").strip().lower().split()) == "stamp received"


def _looks_posted_invoice_number(value: Any, *, market: str | None = None) -> bool:
    text = str(value or "").strip().upper()
    if not text:
        return False
    prefixes = _posted_invoice_prefixes(market)
    return any(text.startswith(prefix) for prefix in prefixes)


def _posted_invoice_prefixes(market: str | None) -> tuple[str, ...]:
    normalized_market = str(market or "").strip().upper()
    env_name = f"BC_MARKET_{normalized_market}_POSTED_INVOICE_PREFIXES" if normalized_market else ""
    raw_value = os.getenv(env_name, "").strip() if env_name else ""
    if raw_value:
        return tuple(part.strip().upper() for part in raw_value.split(",") if part.strip())
    if normalized_market == "MX":
        return ("B",)
    if normalized_market == "GT":
        return ("GTFVR",)
    return ("GTFVR", "B")




def _build_proposed_invoice_groups(
    *,
    header_payload: dict[str, Any],
    line_payloads: list[dict[str, Any]],
    line_sources: list[dict[str, Any]],
    split_by_item_prefix: bool,
    shipment_metadata: dict[str, str],
) -> list[dict[str, Any]]:
    metadata_lines = _build_shipment_metadata_lines(shipment_metadata)
    if not split_by_item_prefix:
        return [
            {
                "invoice_group": "ALL",
                "reference": header_payload["externalDocumentNumber"],
                "proposed_bc_payload": dict(header_payload),
                "proposed_bc_line_payloads": [*metadata_lines, *line_payloads],
                "line_sources": line_sources,
                "total": _sum_line_amounts(line_payloads),
            }
        ]

    grouped_payloads: dict[str, list[dict[str, Any]]] = {}
    grouped_sources: dict[str, list[dict[str, Any]]] = {}
    for line_payload, line_source in zip(line_payloads, line_sources, strict=True):
        group = str(line_source.get("invoice_group") or "OTHER").upper()
        grouped_payloads.setdefault(group, []).append(line_payload)
        grouped_sources.setdefault(group, []).append(line_source)

    proposed_invoices: list[dict[str, Any]] = []
    for group in sorted(grouped_payloads, key=_invoice_group_sort_key):
        group_reference = _invoice_group_reference(header_payload["externalDocumentNumber"], group)
        group_header = {
            **header_payload,
            "externalDocumentNumber": group_reference,
            "customerPurchaseOrderReference": header_payload.get("customerPurchaseOrderReference")
            or header_payload["externalDocumentNumber"],
        }
        proposed_invoices.append(
            {
                "invoice_group": group,
                "reference": group_reference,
                "proposed_bc_payload": group_header,
                "proposed_bc_line_payloads": [*metadata_lines, *grouped_payloads[group]],
                "line_sources": grouped_sources[group],
                "total": _sum_line_amounts(grouped_payloads[group]),
            }
        )
    return proposed_invoices


def _resolve_customer_int_split_rule(
    *,
    customer_number: str | None,
    config: InvoiceAutomationSettings,
) -> dict[str, Any] | None:
    normalized_customer = str(customer_number or "").strip().upper()
    eligible_customers = {
        customer.strip().upper()
        for customer in config.int_split_customer_numbers
        if customer.strip()
    }
    if not normalized_customer or normalized_customer not in eligible_customers:
        return None

    return {
        "id": "gt_alb_int_freight_emergency_split",
        "customer_number": normalized_customer,
        "freight_charge_names": tuple(config.int_split_freight_charge_names),
        "emergency_charge_names": tuple(config.int_split_emergency_charge_names),
        "invoice_groups": ("INT", "INT-2"),
    }


def _validate_customer_int_split_rule(
    *,
    line_sources: list[dict[str, Any]],
    customer_split_rule: dict[str, Any] | None,
) -> dict[str, Any]:
    if not customer_split_rule:
        return {"status": "passed", "message": "No customer-specific INT split applies."}

    freight_names = {
        _normalize_charge_name(name)
        for name in customer_split_rule["freight_charge_names"]
    }
    emergency_names = {
        _normalize_charge_name(name)
        for name in customer_split_rule["emergency_charge_names"]
    }
    int_sources = [
        source
        for source in line_sources
        if str(source.get("invoice_group") or "").strip().upper() == "INT"
    ]
    if not int_sources:
        return {
            "status": "passed",
            "message": "No INT charges are present, so the customer-specific split does not apply.",
        }

    freight_sources = [
        source
        for source in int_sources
        if _normalize_charge_name(str(source.get("charge_name") or "")) in freight_names
    ]
    emergency_sources = [
        source
        for source in int_sources
        if _normalize_charge_name(str(source.get("charge_name") or "")) in emergency_names
    ]
    unsupported_sources = [
        source.get("charge_name") or "unnamed INT charge"
        for source in int_sources
        if _normalize_charge_name(str(source.get("charge_name") or "")) not in freight_names | emergency_names
    ]
    errors: list[str] = []
    if not freight_sources:
        errors.append("Freight (Ocean/Truck/Air) is required for the ALB INT split.")
    if not emergency_sources:
        errors.append("Emergency Surcharge is required for the ALB INT split.")
    if unsupported_sources:
        errors.append(
            "The ALB INT split only supports freight and emergency surcharge; "
            f"additional INT charges found: {', '.join(map(str, unsupported_sources))}."
        )
    if errors:
        return {"status": "failed", "message": " ".join(errors), "errors": errors}
    return {
        "status": "passed",
        "message": "ALB INT freight and emergency surcharge will be issued separately.",
        "freight_total": _sum_source_amounts(freight_sources),
        "emergency_total": _sum_source_amounts(emergency_sources),
    }


def _apply_customer_int_split_rule(
    *,
    line_sources: list[dict[str, Any]],
    customer_split_rule: dict[str, Any],
) -> list[dict[str, Any]]:
    freight_names = {
        _normalize_charge_name(name)
        for name in customer_split_rule["freight_charge_names"]
    }
    rewritten_sources: list[dict[str, Any]] = []
    for source in line_sources:
        rewritten = dict(source)
        if str(rewritten.get("invoice_group") or "").strip().upper() == "INT":
            charge_name = _normalize_charge_name(str(rewritten.get("charge_name") or ""))
            rewritten["invoice_group"] = "INT" if charge_name in freight_names else "INT-2"
        rewritten_sources.append(rewritten)
    return rewritten_sources


def _normalize_charge_name(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _invoice_group_from_item_number(item_number: str) -> str:
    normalized = (item_number or "").strip().upper()
    if normalized.startswith("INT"):
        return "INT"
    if normalized.startswith("NAT"):
        return "NAT"
    return "OTHER"


def _invoice_group_reference(reference: str, group: str) -> str:
    normalized_group = (group or "").strip().upper()
    if normalized_group in {"", "ALL"}:
        return reference
    suffix = f"-{normalized_group}"
    if reference.upper().endswith(suffix):
        return reference
    return f"{reference}{suffix}"


def _invoice_group_sort_key(group: str) -> tuple[int, str]:
    priority = {"INT": 0, "INT-2": 1, "NAT": 2, "OTHER": 3, "ACCOUNT": 4}
    normalized = (group or "").strip().upper()
    return priority.get(normalized, 9), normalized


def _sum_line_amounts(line_payloads: list[dict[str, Any]]) -> float:
    return round(
        sum(float(line.get("unitPrice") or 0) * float(line.get("quantity") or 0) for line in line_payloads),
        2,
    )


def _validate_proposed_invoice_totals(
    *,
    line_payloads: list[dict[str, Any]],
    line_sources: list[dict[str, Any]],
    proposed_invoices: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    field_total = _sum_source_amounts(line_sources)
    payload_total = _sum_line_amounts(line_payloads)
    invoice_total = round(sum(float(invoice.get("total") or 0) for invoice in proposed_invoices), 2)
    single_all_invoice = (
        len(proposed_invoices) == 1
        and str(proposed_invoices[0].get("invoice_group") or "").strip().upper() == "ALL"
    )
    if single_all_invoice:
        fields_by_group = {"ALL": field_total}
        invoices_by_group = {"ALL": invoice_total}
    else:
        fields_by_group = _sum_sources_by_group(line_sources)
        invoices_by_group = {
            str(invoice.get("invoice_group") or "OTHER").upper(): round(float(invoice.get("total") or 0), 2)
            for invoice in proposed_invoices
        }

    if not _amounts_equal(field_total, payload_total):
        errors.append(
            f"Mapped field total {field_total:.2f} does not match BC line total {payload_total:.2f}."
        )
    if not _amounts_equal(field_total, invoice_total):
        errors.append(
            f"Mapped field total {field_total:.2f} does not match proposed invoice total {invoice_total:.2f}."
        )

    for group, amount in fields_by_group.items():
        invoice_amount = invoices_by_group.get(group, 0.0)
        if not _amounts_equal(amount, invoice_amount):
            errors.append(
                f"Mapped {group} total {amount:.2f} does not match proposed {group} invoice total {invoice_amount:.2f}."
            )
    for group, amount in invoices_by_group.items():
        field_amount = fields_by_group.get(group, 0.0)
        if not _amounts_equal(field_amount, amount):
            errors.append(
                f"Proposed {group} invoice total {amount:.2f} does not match mapped {group} total {field_amount:.2f}."
            )

    for invoice in proposed_invoices:
        group = str(invoice.get("invoice_group") or "OTHER").upper()
        source_total = _sum_source_amounts(invoice.get("line_sources") or [])
        invoice_group_total = round(float(invoice.get("total") or 0), 2)
        if not _amounts_equal(source_total, invoice_group_total):
            errors.append(
                f"Proposed {group} invoice line-source total {source_total:.2f} does not match header total {invoice_group_total:.2f}."
            )

    return {
        "status": "failed" if errors else "passed",
        "errors": errors,
        "expected_total": field_total,
        "line_payload_total": payload_total,
        "proposed_invoice_total": invoice_total,
        "expected_totals_by_group": fields_by_group,
        "proposed_totals_by_group": invoices_by_group,
        "billable_fields": [
            {
                "charge_name": source.get("charge_name"),
                "invoice_group": source.get("invoice_group"),
                "amount": round(float(source.get("amount") or 0), 2),
                "quantity": source.get("quantity"),
                "unit_price": source.get("unit_price"),
                "quantity_basis": source.get("quantity_basis"),
                "source_field": source.get("source_field"),
                "source_field_id": source.get("source_field_id"),
                "item_number": source.get("item_number"),
                "description": source.get("description"),
                "description_override": source.get("description_override"),
            }
            for source in line_sources
        ],
    }


def _apply_product_charge_description_overrides(
    *,
    charge: dict[str, Any],
    product: dict[str, Any],
) -> dict[str, Any]:
    normalized_product = _normalize_text_for_invoice_gate(product.get("name") or "")
    if not _is_air_product_name(normalized_product):
        return charge

    normalized_charge_name = _normalize_text_for_invoice_gate(charge.get("charge_name") or "")
    normalized_source_field = _normalize_text_for_invoice_gate(charge.get("source_field") or "")
    is_freight_charge = (
        normalized_charge_name == "FREIGHT (OCEAN/TRUCK/AIR)"
        or normalized_source_field == "FREIGHT (OCEAN/TRUCK/AIR)"
    )
    if not is_freight_charge:
        return charge

    return {
        **charge,
        "description": "COORDINACION VIRTUAL DE TRANSPORTE AEREO",
        "description_override": {
            "reason": "air_product_freight_description",
            "original_description": charge.get("description"),
        },
    }


def _validate_product_invoice_gate(
    *,
    product: dict[str, Any],
    line_sources: list[dict[str, Any]],
    proposed_invoices: list[dict[str, Any]],
    shipment_metadata: dict[str, str],
) -> dict[str, Any]:
    normalized_product = _normalize_text_for_invoice_gate(product.get("name") or "")
    if not _is_air_product_name(normalized_product):
        return {
            "status": "passed",
            "product": product or None,
            "warnings": [],
        }

    invalid_terms = ("MARITIMO", "MARITIME", "OCEAN", "FCL", "LCL", "CONTAINER", "CONTENEDOR")
    failures: list[dict[str, Any]] = []
    for source in line_sources:
        description = str(source.get("description") or "")
        normalized_description = _normalize_text_for_invoice_gate(description)
        matched_terms = [term for term in invalid_terms if term in normalized_description]
        if not matched_terms:
            continue
        failures.append(
            {
                "reason": "air_product_conflicts_with_invoice_description",
                "matched_terms": matched_terms,
                "charge_name": source.get("charge_name"),
                "source_field": source.get("source_field"),
                "source_field_id": source.get("source_field_id"),
                "item_number": source.get("item_number"),
                "description": description,
                "amount": round(float(source.get("amount") or 0), 2),
                "invoice_group": source.get("invoice_group"),
            }
        )

    awb = _clean_marker_value(shipment_metadata.get("awb"))
    if not awb:
        failures.append(
            {
                "reason": "air_product_missing_awb",
                "message": "Air shipment requires an AWB or air reference before invoice creation.",
            }
        )

    warnings: list[dict[str, Any]] = []
    containers = _clean_marker_value(shipment_metadata.get("containers"))
    if containers and not awb:
        warnings.append(
            {
                "reason": "air_product_uses_container_metadata_placeholder",
                "message": (
                    "Air shipment is using the container metadata value as the only available air "
                    "reference. Populate AWB to avoid customer-facing ambiguity."
                ),
                "containers": containers,
            }
        )

    result = {
        "status": "failed" if failures else "passed",
        "message": (
            "Air shipment invoice lines contain ocean/maritime/container wording. "
            "Update the product mapping or BC description before creating invoices."
            if failures
            else "Product invoice validation passed."
        ),
        "product": product,
        "failures": failures,
        "warnings": warnings,
        "invoice_groups": [invoice.get("invoice_group") for invoice in proposed_invoices],
    }
    return result


def _resolve_product_for_invoice_gate(
    custom_fields: dict[str, dict[str, Any]],
    config: InvoiceAutomationSettings,
) -> dict[str, Any]:
    for field_name in config.product_field_names:
        field = custom_fields.get(field_name)
        option = resolve_dropdown_field(field)
        if option:
            return {
                "field_name": field_name,
                "field_id": field.get("id") if field else None,
                "name": option.get("name"),
                "option_id": option.get("id"),
            }
        raw_value = field_value(custom_fields, field_name=field_name)
        if raw_value:
            return {
                "field_name": field_name,
                "field_id": field.get("id") if field else None,
                "name": raw_value,
                "option_id": None,
            }
    return {}


def _is_air_product_name(normalized_product: str) -> bool:
    return normalized_product in {"AIR", "AEREO"}


def _normalize_text_for_invoice_gate(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(without_accents.upper().split())


def _sum_source_amounts(line_sources: list[dict[str, Any]]) -> float:
    return round(sum(float(source.get("amount") or 0) for source in line_sources), 2)


def _sum_sources_by_group(line_sources: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for source in line_sources:
        group = str(source.get("invoice_group") or "OTHER").upper()
        totals[group] = round(totals.get(group, 0.0) + float(source.get("amount") or 0), 2)
    return totals


def _amounts_equal(left: float, right: float) -> bool:
    return abs(round(left, 2) - round(right, 2)) < 0.01


def _resolve_shipment_metadata(
    clickup_summary: dict[str, Any],
    custom_fields: dict[str, dict[str, Any]],
    config: InvoiceAutomationSettings,
) -> dict[str, str]:
    product = _resolve_product_for_invoice_gate(custom_fields, config)
    return {
        "shipment_number": str(clickup_summary.get("name") or "").strip(),
        "booking": _first_present_field(custom_fields, config.shipment_booking_field_names),
        "containers": _first_present_field(custom_fields, config.shipment_container_field_names),
        "product": str(product.get("name") or "").strip(),
        "awb": _first_present_field(custom_fields, config.airway_bill_field_names),
    }


def _build_shipment_metadata_lines(shipment_metadata: dict[str, str]) -> list[dict[str, Any]]:
    booking = _clean_marker_value(shipment_metadata.get("booking"))
    containers = _clean_marker_value(shipment_metadata.get("containers"))
    product = _clean_marker_value(shipment_metadata.get("product"))
    awb = _clean_marker_value(shipment_metadata.get("awb"))
    lines: list[dict[str, Any]] = []

    if product:
        lines.append(
            {
                "lineType": "Comment",
                "description": _truncate_for_bc_description(f"MTM META PRODUCT {product}"),
            }
        )

    if _is_air_product_name(_normalize_text_for_invoice_gate(product)):
        if awb:
            lines.append(
                {
                    "lineType": "Comment",
                    "description": _truncate_for_bc_description(f"MTM META AWB {awb}"),
                }
            )
        return lines

    if booking:
        lines.append(
            {
                "lineType": "Comment",
                "description": _truncate_for_bc_description(f"MTM META BOOKING {booking}"),
            }
        )
    if containers:
        for chunk in _chunk_container_metadata(containers):
            lines.append(
                {
                    "lineType": "Comment",
                    "description": f"MTM META CONTAINER {chunk}",
                }
            )

    return lines


def _chunk_container_metadata(containers: str) -> list[str]:
    max_chunk_len = 100 - len("MTM META CONTAINER ")
    tokens = _container_tokens(containers)
    if not tokens:
        return []

    chunks: list[str] = []
    current = ""
    for token in tokens:
        candidate = token if not current else f"{current}, {token}"
        if len(candidate) <= max_chunk_len:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = token[:max_chunk_len]
    if current:
        chunks.append(current)
    return chunks


def _clean_marker_value(value: str | None) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _truncate_for_bc_description(value: str) -> str:
    return value[:100]


def _bounded_customer_purchase_order_reference(value: str, *, maximum_length: int = 35) -> tuple[str, list[str]]:
    """Fit the BC purchase-order header without changing the printed shipment reference."""
    normalized = _clean_marker_value(value)
    if len(normalized) <= maximum_length:
        return normalized, []

    bounded = normalized[:maximum_length]
    word_boundary = bounded.rfind(" ")
    if word_boundary > 0:
        bounded = bounded[:word_boundary].rstrip(" ,-/(")
    if not bounded:
        bounded = normalized[:maximum_length]

    return (
        bounded,
        [
            "Business Central Customer Purchase Order Reference was shortened to "
            f"{maximum_length} characters: {bounded}"
        ],
    )


def _resolve_invoice_line_quantity(
    *,
    charge: dict[str, Any],
    amount: Decimal,
    custom_fields: dict[str, dict[str, Any]],
    config: InvoiceAutomationSettings,
    product: dict[str, Any],
) -> dict[str, Any]:
    configured_basis = str(charge.get("quantity_basis") or "shipment").strip().lower() or "shipment"
    if configured_basis == "container_count" and _is_air_product_name(
        _normalize_text_for_invoice_gate(product.get("name") or "")
    ):
        basis = "shipment"
    else:
        basis = configured_basis

    if basis in {"", "shipment"}:
        return {
            "quantity": 1,
            "unit_price": float(amount),
            "quantity_basis": "shipment",
        }

    if basis != "container_count":
        return {
            "error": (
                f"Quantity basis {configured_basis!r} for {charge.get('charge_name') or 'charge'} "
                "is not supported."
            )
        }

    quantity = _resolve_container_count(custom_fields=custom_fields, config=config)
    if quantity is None:
        return {
            "error": (
                f"Charge {charge.get('charge_name') or 'charge'} is configured to bill by container count, "
                "but Number of Containers is missing or invalid."
            )
        }

    unit_price = amount / Decimal(quantity)
    line_total = unit_price * Decimal(quantity)
    if not _amounts_equal(float(amount), float(line_total)):
        return {
            "error": (
                f"Charge {charge.get('charge_name') or 'charge'} amount {float(amount):.2f} cannot be "
                f"validated against quantity {quantity} and unit price {float(unit_price):.5f}."
            )
        }

    return {
        "quantity": quantity,
        "unit_price": float(unit_price),
        "quantity_basis": "container_count",
    }


def _resolve_container_count(
    *,
    custom_fields: dict[str, dict[str, Any]],
    config: InvoiceAutomationSettings,
) -> int | None:
    raw_count = _first_present_field(custom_fields, config.shipment_container_count_field_names)
    count = _parse_positive_int(raw_count)
    if count is not None:
        return count

    containers = _first_present_field(custom_fields, config.shipment_container_field_names)
    tokens = _container_tokens(containers)
    if tokens:
        return len(tokens)

    return None


def _parse_positive_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    parsed = _parse_decimal(value)
    if parsed is None:
        return None
    if parsed <= 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _container_tokens(value: Any) -> list[str]:
    normalized = str(value or "").replace("\n", ",").replace(";", ",")
    return [token.strip() for token in normalized.split(",") if token.strip()]


def _combined_created_invoice_for_writeback(created_invoices: list[dict[str, Any]]) -> dict[str, Any]:
    if len(created_invoices) == 1:
        return created_invoices[0]
    return {
        "id": "; ".join(str(invoice.get("id") or "") for invoice in created_invoices if invoice.get("id")),
        "number": "; ".join(
            str(invoice.get("number") or "") for invoice in created_invoices if invoice.get("number")
        ),
    }


def _build_charge_input(
    *,
    custom_fields: dict[str, dict[str, Any]],
    amount_field_names: tuple[str, ...],
    charge_name: str,
    account_number: str | None,
    description: str,
) -> dict[str, Any]:
    raw_value, source_field = _first_present_field_with_name(custom_fields, amount_field_names)
    if not raw_value:
        return {
            "charge_name": charge_name,
            "amount": None,
            "account_number": account_number,
            "description": description,
            "source_field": source_field,
        }

    amount = _parse_decimal(raw_value)
    if amount is None:
        if _is_zero_charge_marker(raw_value):
            amount = Decimal("0")
        else:
            return {
                "charge_name": charge_name,
                "amount": None,
                "account_number": account_number,
                "description": description,
                "source_field": source_field,
                "error": f"Charge field {source_field or charge_name} has an invalid numeric value.",
            }

    return {
        "charge_name": charge_name,
        "amount": amount,
        "account_number": account_number,
        "description": description,
        "source_field": source_field,
    }


def _build_mapped_charge_input(
    *,
    custom_fields: dict[str, dict[str, Any]],
    mapping: InvoiceChargeMapping,
) -> dict[str, Any]:
    raw_value, source_field, source_field_id = _present_field_value_and_source(
        custom_fields,
        field_name=mapping.clickup_field_name,
        field_id=mapping.clickup_field_id,
    )
    if not raw_value:
        return {
            "charge_name": mapping.charge_name,
            "amount": None,
            "item_number": mapping.bc_item_number,
            "description": mapping.bc_description or mapping.charge_name,
            "source_field": source_field,
            "source_field_id": source_field_id,
            "tax_group": mapping.tax_group,
            "quantity_basis": mapping.quantity_basis,
        }

    amount = _parse_decimal(raw_value)
    if amount is None:
        if _is_zero_charge_marker(raw_value):
            amount = Decimal("0")
        else:
            return {
                "charge_name": mapping.charge_name,
                "amount": None,
                "item_number": mapping.bc_item_number,
                "description": mapping.bc_description or mapping.charge_name,
                "source_field": source_field,
                "source_field_id": source_field_id,
                "tax_group": mapping.tax_group,
                "quantity_basis": mapping.quantity_basis,
                "error": f"Charge field {source_field or mapping.charge_name} has an invalid numeric value.",
            }

    return {
        "charge_name": mapping.charge_name,
        "amount": amount,
        "item_number": mapping.bc_item_number,
        "description": mapping.bc_description or mapping.charge_name,
        "source_field": source_field,
        "source_field_id": source_field_id,
        "tax_group": mapping.tax_group,
        "quantity_basis": mapping.quantity_basis,
    }


def _invoice_customer_matches(invoice: dict[str, Any], expected: dict[str, Any]) -> bool:
    matched = False
    for key in ("customerNumber", "customerId"):
        wanted = str(expected.get(key) or "").strip()
        actual = str(invoice.get(key) or "").strip()
        if wanted and actual:
            if actual.casefold() != wanted.casefold():
                return False
            matched = True
    return matched


def _find_existing_invoice(
    *,
    bc_client: BusinessCentralClient,
    market: str,
    reference: str,
    customer_number: str | None,
    customer_id: str | None = None,
) -> dict[str, Any] | None:
    escaped_reference = reference.replace("'", "''")
    filters = [f"externalDocumentNumber eq '{escaped_reference}'"]
    if customer_number:
        escaped_customer_number = customer_number.replace("'", "''")
        filters.append(f"customerNumber eq '{escaped_customer_number}'")
    rows = bc_client.find_entities(
        "salesInvoices",
        filters=" and ".join(filters),
        top=5,
        market=market,
    )
    if not rows and customer_number is not None:
        rows = bc_client.find_entities(
            "salesInvoices",
            filters=f"externalDocumentNumber eq '{escaped_reference}'",
            top=5,
            market=market,
        )
    if not rows:
        return None
    for row in sorted(rows, key=_existing_invoice_retry_sort_key):
        if _is_canceled_sales_invoice(row):
            continue
        if not _invoice_customer_matches(row, {"customerNumber": customer_number, "customerId": customer_id}):
            return {**row, "customer_binding_error": "Existing invoice customer does not match; manual reconciliation required."}
        invoice_number = str(row.get("number") or "").strip()
        fel_row = (
            bc_client.get_posted_invoice_fel_description_by_number(invoice_number, market=market)
            if _looks_posted_invoice_number(invoice_number)
            else None
        )
        return {**row, "existing_fel_row": fel_row}
    return None


def _existing_invoice_retry_sort_key(invoice: dict[str, Any]) -> tuple[int, str]:
    number = str(invoice.get("number") or "")
    posted_rank = 0 if _looks_posted_invoice_number(number) else 1
    modified = str(invoice.get("lastModifiedDateTime") or invoice.get("postingDate") or invoice.get("invoiceDate") or "")
    return posted_rank, modified


def _is_canceled_sales_invoice(invoice: dict[str, Any]) -> bool:
    status = " ".join(str(invoice.get("status") or "").strip().lower().split())
    return status in {"canceled", "cancelled"}


def _validate_customer_fel_readiness(
    *,
    bc_client: BusinessCentralClient,
    bc_customer: dict[str, Any] | None,
    market: str,
    customer_number: str | None,
    customer_id: str | None,
) -> dict[str, Any]:
    if market != "GT":
        return {"status": "ready"}
    if not bc_customer:
        return {
            "status": "missing_customer",
            "message": (
                f"Business Central customer {customer_number or customer_id or 'UNKNOWN'} could not be read "
                "before FEL validation."
            ),
        }

    resolved_customer_number = str(bc_customer.get("number") or customer_number or "").strip()
    resolved_customer_id = str(bc_customer.get("id") or customer_id or "").strip()
    try:
        customer_invoicing = _read_customer_invoicing_fel_row(
            bc_client=bc_client,
            customer_number=resolved_customer_number,
            customer_id=resolved_customer_id,
            market=market,
        )
    except Exception:
        logger.exception("GT customer invoicing readiness failed customer=%s", resolved_customer_number)
        return {
            "status": "customer_fel_readiness_api_unavailable",
            "message": (
                f"Business Central customer {resolved_customer_number or resolved_customer_id or 'UNKNOWN'} "
                "could not be checked against the customer invoicing API before FEL issuance. "
                "Publish the customer invoicing API extension with FEL country fields or review BC access. Consult the server logs."
            ),
            "customer_number": resolved_customer_number,
            "customer_id": resolved_customer_id,
        }

    if not customer_invoicing:
        return {
            "status": "missing_customer_invoicing_row",
            "message": (
                f"Business Central customer {resolved_customer_number or resolved_customer_id or 'UNKNOWN'} "
                "could not be found in the customer invoicing API before FEL issuance."
            ),
            "customer_number": resolved_customer_number,
            "customer_id": resolved_customer_id,
        }

    resolved_fel_country = str(customer_invoicing.get("resolvedFelCountryCode") or "").strip().upper()
    fel_country_ready = customer_invoicing.get("felCountryReady")
    fel_pais = str(customer_invoicing.get("felPais") or "").strip().upper()
    country_region_code = str(customer_invoicing.get("countryRegionCode") or "").strip().upper()
    if "resolvedFelCountryCode" not in customer_invoicing or "felCountryReady" not in customer_invoicing:
        return {
            "status": "customer_fel_country_fields_not_exposed",
            "message": (
                f"Business Central customer {resolved_customer_number or resolved_customer_id or 'UNKNOWN'} "
                "could not be validated because the customer invoicing API does not expose the FEL country fields. "
                "Publish the updated customer invoicing extension before issuing invoices automatically."
            ),
            "customer_number": resolved_customer_number,
            "customer_id": resolved_customer_id,
            "customer_invoicing": customer_invoicing,
        }
    if fel_country_ready is False or not resolved_fel_country:
        return {
            "status": "missing_fel_country_source",
            "message": (
                f"Business Central customer {resolved_customer_number or resolved_customer_id or 'UNKNOWN'} "
                "is missing the FEL/SAT Pais source. Update Pais or Country/Region Code on the customer card "
                "before issuing invoices."
            ),
            "customer_number": resolved_customer_number,
            "customer_id": resolved_customer_id,
            "felPais": fel_pais,
            "countryRegionCode": country_region_code,
            "resolvedFelCountryCode": resolved_fel_country,
        }
    return {
        "status": "ready",
        "customer_number": resolved_customer_number,
        "customer_id": resolved_customer_id,
        "felPais": fel_pais,
        "countryRegionCode": country_region_code,
        "resolvedFelCountryCode": resolved_fel_country,
    }


def _resolve_market_invoice_settings(
    *,
    bc_client: BusinessCentralClient,
    bc_customer: dict[str, Any] | None,
    market: str,
    customer_number: str | None,
    customer_id: str | None,
) -> dict[str, Any]:
    if market != "MX":
        return {"status": "ready", "market": market}

    if not bc_customer:
        return {
            "status": "missing_customer",
            "message": (
                f"Business Central customer {customer_number or customer_id or 'UNKNOWN'} could not be read "
                "before Mexico CFDI validation."
            ),
            "market": market,
        }

    resolved_customer_number = str(bc_customer.get("number") or customer_number or "").strip()
    resolved_customer_id = str(bc_customer.get("id") or customer_id or "").strip()
    try:
        customer_invoicing = _read_customer_invoicing_fel_row(
            bc_client=bc_client,
            customer_number=resolved_customer_number,
            customer_id=resolved_customer_id,
            market=market,
        )
    except Exception:
        logger.exception("MX customer invoicing readiness failed customer=%s", resolved_customer_number)
        return {
            "status": "customer_invoicing_api_unavailable",
            "message": (
                f"Business Central customer {resolved_customer_number or resolved_customer_id or 'UNKNOWN'} "
                "could not be checked against the customer invoicing API before Mexico CFDI issuance. "
                "Consult the server logs."
            ),
            "market": market,
            "customer_number": resolved_customer_number,
            "customer_id": resolved_customer_id,
        }

    if not customer_invoicing:
        return {
            "status": "missing_customer_invoicing_row",
            "message": (
                f"Business Central customer {resolved_customer_number or resolved_customer_id or 'UNKNOWN'} "
                "could not be found in the customer invoicing API before Mexico CFDI issuance."
            ),
            "market": market,
            "customer_number": resolved_customer_number,
            "customer_id": resolved_customer_id,
        }

    payment_terms_code = str(customer_invoicing.get("paymentTermsCode") or "").strip()
    payment_method_code = str(customer_invoicing.get("paymentMethodCode") or "").strip()
    missing: list[str] = []
    if not payment_terms_code:
        missing.append("Business Central customer Payment Terms Code")
    if not payment_method_code:
        missing.append("Business Central customer Payment Method Code")
    if missing:
        return {
            "status": "missing_mx_payment_fields",
            "message": (
                "Mexico CFDI issuance requires customer payment terms and payment method codes before posting. "
                f"Missing: {', '.join(missing)}."
            ),
            "market": market,
            "customer_number": resolved_customer_number,
            "customer_id": resolved_customer_id,
            "missing_fields": missing,
            "customer_invoicing": customer_invoicing,
        }

    return {
        "status": "ready",
        "market": market,
        "customer_number": resolved_customer_number,
        "customer_id": resolved_customer_id,
        "paymentTermsCode": payment_terms_code,
        "paymentMethodCode": payment_method_code,
        "customer_invoicing": customer_invoicing,
        "paymentComplement": {
            "status": "pending_future_architecture",
            "message": "Payment complement automation is intentionally outside the invoice-only issuance path.",
        },
    }


def _set_mx_payment_fields_on_draft(
    *,
    bc_client: BusinessCentralClient,
    sales_invoice_id: str,
    market: str,
    market_invoice_settings: dict[str, Any],
) -> dict[str, Any]:
    payment_terms_code = str(market_invoice_settings.get("paymentTermsCode") or "").strip()
    payment_method_code = str(market_invoice_settings.get("paymentMethodCode") or "").strip()
    if not payment_terms_code or not payment_method_code:
        raise ValueError("Mexico invoice draft is missing paymentTermsCode or paymentMethodCode.")
    if not hasattr(bc_client, "set_mx_payment_fields"):
        raise ValueError(
            "Business Central client does not expose set_mx_payment_fields. "
            "Publish/use the MTM MX sales invoice draft API before automated Mexico issuance."
        )
    return bc_client.set_mx_payment_fields(
        sales_invoice_id,
        payment_terms_code=payment_terms_code,
        payment_method_code=payment_method_code,
        market=market,
    )


def _read_customer_invoicing_fel_row(
    *,
    bc_client: BusinessCentralClient,
    customer_number: str,
    customer_id: str,
    market: str,
) -> dict[str, Any] | None:
    if customer_number and hasattr(bc_client, "get_customer_invoicing_by_number"):
        row = bc_client.get_customer_invoicing_by_number(customer_number, market=market)
        if row:
            return row
    if customer_id and hasattr(bc_client, "get_customer_invoicing_by_id"):
        return bc_client.get_customer_invoicing_by_id(customer_id, market=market)
    return None


def _resolve_reference(
    clickup_summary: dict[str, Any],
    custom_fields: dict[str, dict[str, Any]],
    config: InvoiceAutomationSettings,
) -> str:
    explicit = _first_present_field(custom_fields, config.reference_field_names)
    if explicit:
        return explicit
    market = str(clickup_summary.get("market") or "").strip().upper()
    if market == "MX":
        shipment_name = str(clickup_summary.get("name") or "").strip()
        if shipment_name:
            return shipment_name
    for fallback in (clickup_summary.get("custom_id"), clickup_summary.get("task_id")):
        if fallback is not None and str(fallback).strip():
            return str(fallback).strip()
    return ""


def _resolve_currency_code(
    custom_fields: dict[str, dict[str, Any]],
    config: InvoiceAutomationSettings,
) -> str:
    for field_name in config.currency_field_names:
        field = custom_fields.get(field_name)
        if not field:
            continue
        label = ((resolve_dropdown_field(field) or {}).get("name") or "").strip()
        if label:
            return label.upper()
        raw = field_value(custom_fields, field_name=field_name)
        if raw:
            return raw.upper()
    return config.supported_currency


def _resolve_invoice_customer(
    *,
    custom_fields: dict[str, dict[str, Any]],
    bc_client: BusinessCentralClient,
    market: str,
    config: InvoiceAutomationSettings,
    customer_id: str,
    customer_number: str,
) -> dict[str, Any]:
    if customer_id or customer_number:
        return {
            "status": "explicit",
            "source": "business_central_field",
            "customer_id": customer_id or None,
            "customer_number": customer_number or None,
            "customer_name": None,
            "source_field": None,
            "source_field_id": None,
        }

    customer_name, source_field, source_field_id = _first_present_dropdown_or_field_value(
        custom_fields,
        field_names=config.bc_customer_name_field_names,
        field_ids=config.bc_customer_name_field_ids,
    )
    if not customer_name:
        return {
            "status": "missing_source",
            "source": None,
            "customer_id": None,
            "customer_number": None,
            "customer_name": None,
            "source_field": None,
            "source_field_id": None,
        }

    try:
        customer = bc_client.resolve_customer_by_name(customer_name, market=market)
    except ValueError as exc:
        return {
            "status": "ambiguous",
            "message": str(exc),
            "source": "clickup_customer_name",
            "customer_id": None,
            "customer_number": None,
            "customer_name": customer_name,
            "source_field": source_field,
            "source_field_id": source_field_id,
        }

    if not customer:
        return {
            "status": "not_found",
            "source": "clickup_customer_name",
            "customer_id": None,
            "customer_number": None,
            "customer_name": customer_name,
            "source_field": source_field,
            "source_field_id": source_field_id,
        }

    return {
        "status": "resolved",
        "source": "clickup_customer_name",
        "customer_id": customer.get("id") or None,
        "customer_number": customer.get("number") or None,
        "customer_name": customer_name,
        "bc_display_name": customer.get("displayName") or customer.get("name") or None,
        "source_field": source_field,
        "source_field_id": source_field_id,
    }


def _resolve_bc_customer_card(
    *,
    bc_client: BusinessCentralClient,
    market: str,
    customer_id: str | None,
    customer_number: str | None,
) -> dict[str, Any] | None:
    if customer_id:
        return bc_client.get_customer_by_id(customer_id, market=market)
    if not customer_number:
        return None

    escaped = customer_number.replace("'", "''")
    rows = bc_client.find_entities(
        "customers",
        filters=f"number eq '{escaped}'",
        top=2,
        market=market,
    )
    if not rows:
        return None
    if len(rows) > 1:
        raise ValueError(f"More than one Business Central customer matched {customer_number}.")
    return rows[0]


def _resolve_invoice_status(
    custom_fields: dict[str, dict[str, Any]],
    config: InvoiceAutomationSettings,
    *,
    fallback: str | None,
) -> dict[str, Any]:
    for field_name in config.invoice_status_field_names:
        field = custom_fields.get(field_name)
        if not field:
            continue
        resolved = resolve_dropdown_field(field)
        label = ((resolved or {}).get("name") or "").strip()
        raw = field_value(custom_fields, field_name=field_name)
        status = label or raw
        if status:
            return {
                "status": status,
                "source": "custom_field",
                "field_name": field_name,
                "field_id": field.get("id"),
                "ready_option_id": _dropdown_option_id(field, config.ready_status),
            }

    for field_id in config.invoice_status_field_ids:
        field = _find_field_by_id(custom_fields, field_id)
        if not field:
            continue
        resolved = resolve_dropdown_field(field)
        label = ((resolved or {}).get("name") or "").strip()
        raw = (field.get("value") if field.get("value") is not None else "")
        status = label or str(raw).strip()
        if status:
            return {
                "status": status,
                "source": "custom_field_id",
                "field_name": _field_name_by_id(custom_fields, field_id),
                "field_id": field.get("id") or field_id,
                "ready_option_id": _dropdown_option_id(field, config.ready_status),
            }

    return {
        "status": fallback,
        "source": "task_status",
        "field_name": None,
        "field_id": None,
        "ready_option_id": None,
    }


def _dropdown_option_id(field: dict[str, Any], option_name: str) -> str | int | None:
    target = _normalize_status(option_name)
    for option in (field.get("type_config") or {}).get("options", []):
        if _normalize_status(option.get("name")) == target:
            return option.get("id") or option.get("orderindex")
    return None


def _resolve_eta_date(
    clickup_summary: dict[str, Any],
    config: InvoiceAutomationSettings,
) -> date | None:
    custom_fields = clickup_summary.get("custom_fields") or {}
    eta = _resolve_date_from_fields(
        custom_fields,
        config.eta_field_names,
        field_ids=config.eta_field_ids,
    )
    if eta is not None:
        return eta
    return _parse_date(clickup_summary.get("due_date"))


def _resolve_date_from_fields(
    custom_fields: dict[str, dict[str, Any]],
    field_names: tuple[str, ...],
    *,
    field_ids: tuple[str, ...] = (),
) -> date | None:
    for field_name in field_names:
        field = custom_fields.get(field_name) or {}
        value = field.get("value")
        parsed = _parse_date(value)
        if parsed is not None:
            return parsed
    for field_id in field_ids:
        field = _find_field_by_id(custom_fields, field_id) or {}
        parsed = _parse_date(field.get("value"))
        if parsed is not None:
            return parsed
    return None


def _first_present_field(custom_fields: dict[str, dict[str, Any]], field_names: tuple[str, ...]) -> str:
    for field_name in field_names:
        value = field_value(custom_fields, field_name=field_name)
        if value:
            return value
    return ""


def _first_present_field_with_name(
    custom_fields: dict[str, dict[str, Any]],
    field_names: tuple[str, ...],
) -> tuple[str, str | None]:
    for field_name in field_names:
        value = field_value(custom_fields, field_name=field_name)
        if value:
            return value, field_name
    return "", None


def _first_present_dropdown_or_field_value(
    custom_fields: dict[str, dict[str, Any]],
    *,
    field_names: tuple[str, ...],
    field_ids: tuple[str, ...],
) -> tuple[str, str | None, str | None]:
    for field_name in field_names:
        field = custom_fields.get(field_name)
        value = _dropdown_or_raw_field_value(field)
        if value:
            return value, field_name, (field or {}).get("id")

    for field_id in field_ids:
        field = _find_field_by_id(custom_fields, field_id)
        value = _dropdown_or_raw_field_value(field)
        if value:
            return value, _field_name_by_id(custom_fields, field_id), field_id

    return "", None, None


def _dropdown_or_raw_field_value(field: dict[str, Any] | None) -> str:
    label = ((resolve_dropdown_field(field) or {}).get("name") or "").strip()
    if label:
        return label
    value = (field or {}).get("value")
    if value is None:
        return ""
    return str(value).strip()


def _find_field_by_id(
    custom_fields: dict[str, dict[str, Any]],
    field_id: str,
) -> dict[str, Any] | None:
    if not field_id:
        return None
    for details in custom_fields.values():
        if details.get("id") == field_id:
            return details
    return None


def _field_name_by_id(
    custom_fields: dict[str, dict[str, Any]],
    field_id: str,
) -> str | None:
    if not field_id:
        return None
    for field_name, details in custom_fields.items():
        if details.get("id") == field_id:
            return field_name
    return None


def _present_field_value_and_source(
    custom_fields: dict[str, dict[str, Any]],
    *,
    field_name: str,
    field_id: str,
) -> tuple[str, str | None, str | None]:
    field = None
    if field_id:
        for candidate_name, details in custom_fields.items():
            if details.get("id") == field_id:
                field = details
                source_name = candidate_name
                break
        else:
            source_name = None
    else:
        source_name = None

    if field is None and field_name:
        field = custom_fields.get(field_name)
        source_name = field_name if field is not None else source_name

    value = (field or {}).get("value")
    if value is None:
        return "", source_name, field_id or None
    return str(value).strip(), source_name, (field or {}).get("id") or field_id or None


def _status_equals(left: str | None, right: str | None) -> bool:
    return _normalize_status(left) == _normalize_status(right)


def _normalize_status(value: str | None) -> str:
    return "".join(char for char in (value or "").strip().lower() if char.isalnum())


def _parse_date(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=UTC).date()

    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        timestamp = int(raw)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC).date()
    for parser in (date.fromisoformat,):
        try:
            return parser(raw)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    cleaned = str(value).strip()
    if not cleaned:
        return None
    normalized = cleaned.replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _is_zero_charge_marker(value: Any) -> bool:
    normalized = " ".join(str(value or "").strip().casefold().split())
    compact = "".join(ch for ch in normalized if ch.isalnum())
    if not normalized.startswith("no"):
        return False
    return any(
        marker in compact
        for marker in (
            "noalmacenaje",
            "nodd",
            "nodandd",
            "nodyd",
            "nodemurrage",
            "nodetention",
        )
    )


def load_invoice_charge_mappings(path: str | os.PathLike[str]) -> tuple[InvoiceChargeMapping, ...]:
    mapping_path = Path(path)
    if not mapping_path.exists():
        raise ValueError(f"Invoice charge mapping file does not exist: {mapping_path}")

    payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    rows = payload.get("mappings")
    if not isinstance(rows, list):
        raise ValueError(f"Invoice charge mapping file must contain a mappings list: {mapping_path}")

    mappings: list[InvoiceChargeMapping] = []
    seen_field_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Invoice charge mapping row {index} must be an object.")

        charge_name = str(row.get("charge_name") or row.get("clickup_field_name") or "").strip()
        clickup_field_name = str(row.get("clickup_field_name") or charge_name).strip()
        clickup_field_id = str(row.get("clickup_field_id") or "").strip()
        bc_item_number = str(row.get("bc_item_number") or "").strip()
        bc_description = str(row.get("bc_description") or charge_name).strip()
        tax_group = str(row.get("tax_group") or "").strip() or None
        quantity_basis = str(row.get("quantity_basis") or "shipment").strip().lower() or "shipment"
        if quantity_basis not in {"shipment", "container_count"}:
            raise ValueError(
                f"Invoice charge mapping row {index} has unsupported quantity_basis: {quantity_basis}"
            )
        missing = [
            name
            for name, value in (
                ("charge_name", charge_name),
                ("clickup_field_name", clickup_field_name),
                ("clickup_field_id", clickup_field_id),
                ("bc_item_number", bc_item_number),
                ("bc_description", bc_description),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Invoice charge mapping row {index} is missing: {', '.join(missing)}")
        if clickup_field_id in seen_field_ids:
            raise ValueError(f"Duplicate ClickUp field ID in invoice charge mapping: {clickup_field_id}")
        seen_field_ids.add(clickup_field_id)
        mappings.append(
            InvoiceChargeMapping(
                charge_name=charge_name,
                clickup_field_name=clickup_field_name,
                clickup_field_id=clickup_field_id,
                bc_item_number=bc_item_number,
                bc_description=bc_description,
                tax_group=tax_group,
                quantity_basis=quantity_basis,
            )
        )

    return tuple(mappings)


def load_invoice_charge_mapping_split_by_item_prefix(
    path: str | os.PathLike[str],
    *,
    default: bool,
) -> bool:
    mapping_path = Path(path)
    if not mapping_path.exists():
        raise ValueError(f"Invoice charge mapping file does not exist: {mapping_path}")

    payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    value = payload.get("split_by_item_prefix")
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _env_invoice_charge_mapping_path(market: str) -> str | None:
    explicit = os.getenv("CLICKUP_INVOICE_CHARGE_MAPPING_PATH", "").strip()
    if explicit:
        return explicit

    default_path = Path("config") / "invoice_charge_mappings" / f"{market.lower()}.json"
    if default_path.exists():
        return str(default_path)
    return None


def _env_csv(name: str, *, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    values = tuple(value.strip() for value in raw.split(",") if value.strip())
    return values or default


def _env_int(name: str, *, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
