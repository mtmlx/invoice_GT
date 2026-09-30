from __future__ import annotations

import re
import unicodedata
from typing import Any

import requests

from business_central_client.auth import TokenProvider
from business_central_client.config import Settings


def _business_central_error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        detail = response.text
    else:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")
        else:
            detail = response.text

    return detail.strip()[:2000]


def _raise_for_status_with_detail(response: requests.Response) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = _business_central_error_detail(response)
        if detail:
            raise requests.HTTPError(
                f"{exc}. Business Central detail: {detail}",
                response=response,
            ) from exc
        raise


class BusinessCentralClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.token_provider = TokenProvider(settings)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": settings.user_agent,
            }
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token_provider.get_token()}"}

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        response = self.session.request(
            method=method,
            url=url,
            headers=request_headers,
            params=params,
            json=json,
            timeout=timeout_seconds or self.settings.timeout_seconds,
        )
        _raise_for_status_with_detail(response)
        if not response.content:
            return {}
        return response.json()

    def _request_bytes(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        response = self.session.request(
            method=method,
            url=url,
            headers=request_headers,
            params=params,
            timeout=self.settings.timeout_seconds,
        )
        _raise_for_status_with_detail(response)
        return response.content

    def get_environments(self) -> dict[str, Any]:
        return self._request("GET", self.settings.environments_url)

    def get_companies(self) -> dict[str, Any]:
        return self._request("GET", f"{self.settings.api_base_url}/companies")

    def get_company_metadata(
        self,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        resolved_company_id = self._resolve_company_id(company_id=company_id, market=market)
        if not resolved_company_id:
            return None

        companies = self.get_companies().get("value", [])
        for company in companies:
            if company.get("id") == resolved_company_id:
                return company
        return None

    def get_entities(
        self,
        entity_name: str,
        *,
        top: int | None = None,
        filters: str | None = None,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )

        params: dict[str, Any] = {}
        if top is not None:
            params["$top"] = top
        if filters:
            params["$filter"] = filters

        url = f"{self.settings.api_base_url}/companies({company})/{entity_name}"
        return self._request("GET", url, params=params or None)

    def get_entity(
        self,
        entity_name: str,
        entity_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )

        url = f"{self.settings.api_base_url}/companies({company})/{entity_name}({entity_id})"
        return self._request("GET", url)

    def get_posted_sales_invoices(
        self,
        *,
        top: int | None = None,
        filters: str | None = None,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.get_entities(
            "salesInvoices",
            top=top,
            filters=filters,
            company_id=company_id,
            market=market,
        ).get("value", [])

    def get_posted_sales_invoice_by_number(
        self,
        invoice_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        escaped = invoice_number.replace("'", "''")
        rows = self.find_entities(
            "salesInvoices",
            filters=f"number eq '{escaped}'",
            top=2,
            company_id=company_id,
            market=market,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"More than one Business Central sales invoice matched {invoice_number}.")
        return rows[0]

    def get_posted_sales_invoice_by_external_document_number(
        self,
        external_document_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        escaped = external_document_number.replace("'", "''")
        rows = self.find_entities(
            "salesInvoices",
            filters=f"externalDocumentNumber eq '{escaped}'",
            top=2,
            company_id=company_id,
            market=market,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(
                f"More than one Business Central sales invoice matched external document {external_document_number}."
            )
        return rows[0]

    def get_posted_sales_invoice_lines(
        self,
        sales_invoice_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )
        url = (
            f"{self.settings.api_base_url}/companies({company})/"
            f"salesInvoices({sales_invoice_id})/salesInvoiceLines"
        )
        return self._request("GET", url).get("value", [])

    def get_sales_invoice_pdf_content(
        self,
        sales_invoice_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> bytes:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )

        url = (
            f"{self.settings.api_base_url}/companies({company})/"
            f"salesInvoices({sales_invoice_id})/pdfDocument"
        )
        pdf_document = self._request("GET", url)
        content_url = _pdf_document_content_url(pdf_document)
        if not content_url:
            raise ValueError(f"Business Central did not expose a PDF content link for invoice {sales_invoice_id}.")

        return self._request_bytes("GET", content_url, headers={"Accept": "application/pdf"})

    def get_sales_credit_memos(
        self,
        *,
        top: int | None = None,
        filters: str | None = None,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.get_entities(
            "salesCreditMemos",
            top=top,
            filters=filters,
            company_id=company_id,
            market=market,
        ).get("value", [])

    def get_sales_credit_memo_by_number(
        self,
        credit_memo_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        escaped = credit_memo_number.replace("'", "''")
        rows = self.find_entities(
            "salesCreditMemos",
            filters=f"number eq '{escaped}'",
            top=2,
            company_id=company_id,
            market=market,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"More than one Business Central sales credit memo matched {credit_memo_number}.")
        return rows[0]

    def get_sales_credit_memo_by_external_document_number(
        self,
        external_document_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        escaped = external_document_number.replace("'", "''")
        rows = self.find_entities(
            "salesCreditMemos",
            filters=f"externalDocumentNumber eq '{escaped}'",
            top=2,
            company_id=company_id,
            market=market,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(
                f"More than one Business Central sales credit memo matched external document {external_document_number}."
            )
        return rows[0]

    def get_sales_credit_memo_lines(
        self,
        sales_credit_memo_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )
        url = (
            f"{self.settings.api_base_url}/companies({company})/"
            f"salesCreditMemos({sales_credit_memo_id})/salesCreditMemoLines"
        )
        return self._request("GET", url).get("value", [])

    def get_sales_credit_memo_pdf_content(
        self,
        sales_credit_memo_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> bytes:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )

        url = (
            f"{self.settings.api_base_url}/companies({company})/"
            f"salesCreditMemos({sales_credit_memo_id})/pdfDocument"
        )
        pdf_document = self._request("GET", url)
        content_url = _pdf_document_content_url(pdf_document)
        if not content_url:
            raise ValueError(
                f"Business Central did not expose a PDF content link for credit memo {sales_credit_memo_id}."
            )

        return self._request_bytes("GET", content_url, headers={"Accept": "application/pdf"})

    def get_customer_by_id(
        self,
        customer_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        try:
            return self.get_entity(
                "customers",
                customer_id,
                company_id=company_id,
                market=market,
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def get_customer_invoicing_by_number(
        self,
        customer_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        needle = (customer_number or "").strip()
        if not needle:
            return None

        escaped = needle.replace("'", "''")
        rows = self._get_customer_invoicing_rows(
            filters=f"number eq '{escaped}'",
            top=2,
            company_id=company_id,
            market=market,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"More than one customer invoicing row matched {customer_number}.")
        return rows[0]

    def get_customer_invoicing_by_id(
        self,
        customer_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        needle = (customer_id or "").strip()
        if not needle:
            return None

        escaped = needle.replace("'", "''")
        rows = self._get_customer_invoicing_rows(
            filters=f"id eq {escaped}",
            top=2,
            company_id=company_id,
            market=market,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"More than one customer invoicing row matched customer id {customer_id}.")
        return rows[0]

    def get_customer_ledger_entries_by_document_no(
        self,
        document_no: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
        top: int = 5,
    ) -> list[dict[str, Any]]:
        from urllib.parse import quote

        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )
        company_metadata = self.get_company_metadata(company_id=company, market=market)
        company_name = (company_metadata or {}).get("name")
        if not company_name:
            return []

        escaped_company = "'" + company_name.replace("'", "''") + "'"
        escaped_document_no = document_no.replace("'", "''")
        url = (
            f"https://api.businesscentral.dynamics.com/v2.0/{self.settings.environment}/"
            f"ODataV4/Company({quote(escaped_company, safe='()')})/CustomerLedgerEntires"
        )
        return self._request(
            "GET",
            url,
            params={
                "$top": top,
                "$filter": f"Document_No eq '{escaped_document_no}'",
            },
        ).get("value", [])

    def get_gt_registered_invoice_by_number(
        self,
        invoice_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        from urllib.parse import quote

        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )
        company_metadata = self.get_company_metadata(company_id=company, market=market)
        company_name = (company_metadata or {}).get("name")
        if not company_name:
            return None

        escaped_company = "'" + company_name.replace("'", "''") + "'"
        escaped_invoice_number = invoice_number.replace("'", "''")
        url = (
            f"https://api.businesscentral.dynamics.com/v2.0/{self.settings.environment}/"
            f"ODataV4/Company({quote(escaped_company, safe='()')})/GT_Facturasregistradas"
        )
        rows = self._request(
            "GET",
            url,
            params={
                "$top": 2,
                "$filter": f"No eq '{escaped_invoice_number}'",
            },
        ).get("value", [])
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"More than one GT registered invoice matched {invoice_number}.")
        return rows[0]

    def build_sales_invoice_url(
        self,
        *,
        company_name: str,
        invoice_number: str,
    ) -> str:
        from urllib.parse import quote

        base = f"https://businesscentral.dynamics.com/{self.settings.tenant_id}/{quote(self.settings.environment, safe='')}/"
        escaped_invoice_number = invoice_number.replace("'", "''")
        filter_expr = f"'Sales Invoice Header'.'No.' IS '{escaped_invoice_number}'"
        query = (
            f"?company={quote(company_name, safe='')}"
            f"&page=132"
            f"&filter={quote(filter_expr, safe='')}"
            f"&dc=0"
        )
        return base + query

    def patch_entity(
        self,
        entity_name: str,
        entity_id: str,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
        if_match: str = "*",
    ) -> dict[str, Any]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )

        url = f"{self.settings.api_base_url}/companies({company})/{entity_name}({entity_id})"
        return self._request(
            "PATCH",
            url,
            json=payload,
            headers={"If-Match": if_match},
        )

    def patch_company_path(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
        if_match: str = "*",
        **path_params: str,
    ) -> dict[str, Any]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )

        normalized_path = path.strip()
        if normalized_path.startswith("http://") or normalized_path.startswith("https://"):
            url = normalized_path
        else:
            url = self._expand_relative_path(normalized_path, company, **path_params)

        return self._request(
            "PATCH",
            url,
            json=payload,
            headers={"If-Match": if_match},
        )

    def post_to_company(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )

        normalized_path = path.strip()
        if normalized_path.startswith("http://") or normalized_path.startswith("https://"):
            url = normalized_path
        else:
            url = self._expand_relative_path(normalized_path, company)

        return self._request("POST", url, json=payload, timeout_seconds=timeout_seconds)

    def find_entities(
        self,
        entity_name: str,
        *,
        filters: str,
        top: int = 1,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.get_entities(
            entity_name,
            top=top,
            filters=filters,
            company_id=company_id,
            market=market,
        ).get("value", [])

    def create_sales_invoice(
        self,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            "/companies({company_id})/salesInvoices",
            payload,
            company_id=company_id,
            market=market,
        )

    def create_sales_invoice_line(
        self,
        sales_invoice_id: str,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            f"/companies({{company_id}})/salesInvoices({sales_invoice_id})/salesInvoiceLines",
            payload,
            company_id=company_id,
            market=market,
        )

    def create_purchase_invoice(
        self,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            "/companies({company_id})/purchaseInvoices",
            payload,
            company_id=company_id,
            market=market,
        )

    def create_purchase_invoice_line(
        self,
        purchase_invoice_id: str,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            f"/companies({{company_id}})/purchaseInvoices({purchase_invoice_id})/purchaseInvoiceLines",
            payload,
            company_id=company_id,
            market=market,
        )

    def post_purchase_invoice(
        self,
        purchase_invoice_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            f"/companies({{company_id}})/purchaseInvoices({purchase_invoice_id})/Microsoft.NAV.post",
            {},
            company_id=company_id,
            market=market,
        )

    def get_purchase_invoice_lines(
        self,
        purchase_invoice_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )
        url = (
            f"{self.settings.api_base_url}/companies({company})/"
            f"purchaseInvoices({purchase_invoice_id})/purchaseInvoiceLines"
        )
        return self._request("GET", url).get("value", [])

    def post_sales_invoice(
        self,
        sales_invoice_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            f"/companies({{company_id}})/salesInvoices({sales_invoice_id})/Microsoft.NAV.post",
            {},
            company_id=company_id,
            market=market,
        )

    def set_mx_payment_fields(
        self,
        sales_invoice_id: str,
        *,
        payment_terms_code: str,
        payment_method_code: str,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            (
                "/api/mtmlogix/invoiceSync/v1.0/companies({company_id})/"
                f"mxSalesInvoiceDrafts({sales_invoice_id})/Microsoft.NAV.SetMxPaymentFields"
            ),
            {
                "paymentTermsCode": payment_terms_code,
                "paymentMethodCode": payment_method_code,
            },
            company_id=company_id,
            market=market,
        )

    def create_sales_credit_memo(
        self,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            "/companies({company_id})/salesCreditMemos",
            payload,
            company_id=company_id,
            market=market,
        )

    def create_sales_credit_memo_line(
        self,
        sales_credit_memo_id: str,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            f"/companies({{company_id}})/salesCreditMemos({sales_credit_memo_id})/salesCreditMemoLines",
            payload,
            company_id=company_id,
            market=market,
        )

    def post_sales_credit_memo(
        self,
        sales_credit_memo_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            f"/companies({{company_id}})/salesCreditMemos({sales_credit_memo_id})/Microsoft.NAV.post",
            {},
            company_id=company_id,
            market=market,
        )

    def cancel_sales_credit_memo(
        self,
        sales_credit_memo_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            f"/companies({{company_id}})/salesCreditMemos({sales_credit_memo_id})/Microsoft.NAV.cancel",
            {},
            company_id=company_id,
            market=market,
        )

    def get_posted_invoice_fel_description_by_number(
        self,
        invoice_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        escaped = invoice_number.replace("'", "''")
        rows = self._get_posted_invoice_fel_descriptions(
            filters=f"number eq '{escaped}'",
            top=2,
            company_id=company_id,
            market=market,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"More than one posted invoice FEL row matched {invoice_number}.")
        return rows[0]

    def get_posted_invoice_fel_descriptions(
        self,
        *,
        filters: str,
        top: int = 100,
        order_by: str | None = None,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._get_posted_invoice_fel_descriptions(
            filters=filters,
            top=top,
            order_by=order_by,
            company_id=company_id,
            market=market,
        )

    def get_posted_credit_memo_fel_description_by_number(
        self,
        credit_memo_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        escaped = credit_memo_number.replace("'", "''")
        rows = self._get_posted_credit_memo_fel_descriptions(
            filters=f"number eq '{escaped}'",
            top=2,
            company_id=company_id,
            market=market,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"More than one posted credit memo FEL row matched {credit_memo_number}.")
        return rows[0]

    def sync_posted_invoice_fel_line_descriptions(
        self,
        posted_invoice_fel_row_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self._post_posted_invoice_fel_action(
            posted_invoice_fel_row_id,
            "SyncFelLineDescriptions",
            company_id=company_id,
            market=market,
        )

    def stamp_posted_invoice_fel(
        self,
        posted_invoice_fel_row_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self._post_posted_invoice_fel_action(
            posted_invoice_fel_row_id,
            "StampFelInvoice",
            company_id=company_id,
            market=market,
        )

    def send_posted_invoice_customer_email(
        self,
        posted_invoice_fel_row_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        """Submit the approved MTM PDF through BC's configured email scenario."""
        return self._post_posted_invoice_fel_action(
            posted_invoice_fel_row_id,
            "SendApprovedInvoiceEmail",
            company_id=company_id,
            market=market,
            timeout_seconds=max(self.settings.timeout_seconds, 120),
        )

    def send_posted_invoice_test_email_to_mario(
        self,
        posted_invoice_fel_row_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        """Send an existing approved invoice to the fixed internal canary recipient."""
        return self._post_posted_invoice_fel_action(
            posted_invoice_fel_row_id,
            "SendApprovedInvoiceTestEmailToMario",
            company_id=company_id,
            market=market,
            timeout_seconds=max(self.settings.timeout_seconds, 120),
        )

    def get_invoice_email_canary_evidence_by_number(
        self,
        invoice_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure "
                "BC_MARKET_<CODE>_COMPANY_ID, or pass company_id explicitly."
            )
        escaped = invoice_number.replace("'", "''")
        url = (
            f"https://api.businesscentral.dynamics.com/v2.0/{self.settings.environment}"
            f"/api/mtmlogix/invoiceSync/v1.0/companies({company})/"
            "invoiceEmailCanaryEvidenceEntries"
        )
        rows = self._request(
            "GET",
            url,
            params={"$top": 2, "$filter": f"number eq '{escaped}'"},
        ).get("value", [])
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(
                f"More than one Business Central canary evidence row matched {invoice_number}."
            )
        return rows[0]

    def get_invoice_email_delivery_by_posted_invoice_id(
        self,
        posted_invoice_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        needle = (posted_invoice_id or "").strip()
        if not needle:
            return None
        rows = self._get_invoice_email_deliveries(
            filters=f"postedInvoiceId eq {needle}",
            top=2,
            company_id=company_id,
            market=market,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"More than one Business Central email audit row matched {posted_invoice_id}.")
        return rows[0]

    def stamp_posted_credit_memo_fel(
        self,
        posted_credit_memo_fel_row_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self._post_posted_credit_memo_fel_action(
            posted_credit_memo_fel_row_id,
            "StampFelCreditMemo",
            company_id=company_id,
            market=market,
        )

    def cancel_posted_credit_memo_fel_with_motive(
        self,
        posted_credit_memo_fel_row_id: str,
        motive_text: str,
        *,
        issue_datetime_text: str | None = None,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        body = {"motiveText": motive_text}
        if issue_datetime_text:
            body["issueDateTimeText"] = issue_datetime_text
            action_name = "CancelFelCreditMemoWithMotiveAndIssueDateTime"
        else:
            action_name = "CancelFelCreditMemoWithMotive"

        return self._post_posted_credit_memo_fel_action(
            posted_credit_memo_fel_row_id,
            action_name,
            body=body,
            company_id=company_id,
            market=market,
        )

    def set_mx_substitution_relation(
        self,
        posted_invoice_fel_row_id: str,
        old_invoice_number: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self._post_posted_invoice_fel_action(
            posted_invoice_fel_row_id,
            "SetMxSubstitutionRelation",
            body={"oldInvoiceNumber": old_invoice_number},
            company_id=company_id,
            market=market,
        )

    def stamp_mx_invoice(
        self,
        posted_invoice_fel_row_id: str,
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self._post_posted_invoice_fel_action(
            posted_invoice_fel_row_id,
            "StampMxInvoice",
            company_id=company_id,
            market=market,
        )

    def cancel_mx_invoice_with_substitution(
        self,
        posted_invoice_fel_row_id: str,
        substitution_invoice_number: str,
        *,
        cancellation_reason_id: str = "01",
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self._post_posted_invoice_fel_action(
            posted_invoice_fel_row_id,
            "CancelMxInvoiceWithSubstitution",
            body={
                "substitutionInvoiceNumber": substitution_invoice_number,
                "cancellationReasonId": cancellation_reason_id,
            },
            company_id=company_id,
            market=market,
        )

    def request_mx_cancellation(self, posted_invoice_fel_row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit once. A successful HTTP response is not fiscal cancellation proof."""
        return self._post_posted_invoice_fel_action(
            posted_invoice_fel_row_id, "RequestMxCancellation", body=payload, market="MX",
            timeout_seconds=180,
        )

    def get_mx_cancellation(self, invoice_number: str) -> dict[str, Any] | None:
        company = self._resolve_company_id(market="MX")
        if not company:
            raise ValueError("Mexico company is not configured")
        url = (
            f"https://api.businesscentral.dynamics.com/v2.0/{self.settings.environment}"
            f"/api/mtmlogix/invoiceSync/v1.0/companies({company})/mxCancellations"
        )
        reference = invoice_number.replace("'", "''")
        rows = self._request("GET", url, params={"$filter": f"invoiceNumber eq '{reference}'", "$top": 2}).get("value", [])
        if len(rows) > 1:
            raise ValueError("Ambiguous Mexico cancellation identity")
        return rows[0] if rows else None

    def refresh_mx_cancellation(self, operation_id: str) -> dict[str, Any]:
        return self._mx_cancellation_action(operation_id, "RefreshStatus")

    def finalize_mx_cancellation(self, operation_id: str) -> dict[str, Any]:
        return self._mx_cancellation_action(operation_id, "FinalizeAccounting")

    def _mx_cancellation_action(self, operation_id: str, action: str) -> dict[str, Any]:
        from uuid import UUID

        operation_id = str(UUID(operation_id))
        return self.post_to_company(
            "/api/mtmlogix/invoiceSync/v1.0/companies({company_id})/"
            f"mxCancellations({operation_id})/Microsoft.NAV.{action}",
            {}, market="MX", timeout_seconds=180,
        )

    def _get_posted_invoice_fel_descriptions(
        self,
        *,
        filters: str,
        top: int = 1,
        order_by: str | None = None,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )
        url = (
            f"https://api.businesscentral.dynamics.com/v2.0/{self.settings.environment}"
            f"/api/mtmlogix/invoiceSync/v1.0/companies({company})/postedInvoiceFelDescriptions"
        )
        params: dict[str, Any] = {"$top": top, "$filter": filters}
        if order_by:
            params["$orderby"] = order_by
        return self._request("GET", url, params=params).get("value", [])

    def _get_invoice_email_deliveries(
        self,
        *,
        filters: str,
        top: int = 1,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )
        url = (
            f"https://api.businesscentral.dynamics.com/v2.0/{self.settings.environment}"
            f"/api/mtmlogix/invoiceSync/v1.0/companies({company})/invoiceEmailDeliveries"
        )
        return self._request(
            "GET",
            url,
            params={"$top": top, "$filter": filters},
        ).get("value", [])

    def _get_posted_credit_memo_fel_descriptions(
        self,
        *,
        filters: str,
        top: int = 1,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )
        url = (
            f"https://api.businesscentral.dynamics.com/v2.0/{self.settings.environment}"
            f"/api/mtmlogix/invoiceSync/v1.0/companies({company})/postedCreditMemoFelDescriptions"
        )
        return self._request(
            "GET",
            url,
            params={"$top": top, "$filter": filters},
        ).get("value", [])

    def _get_customer_invoicing_rows(
        self,
        *,
        filters: str,
        top: int = 1,
        company_id: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        company = self._resolve_company_id(company_id=company_id, market=market)
        if not company:
            raise ValueError(
                "A company ID is required. Set BC_COMPANY_ID, configure BC_MARKET_<CODE>_COMPANY_ID, "
                "or pass company_id explicitly."
            )
        url = (
            f"https://api.businesscentral.dynamics.com/v2.0/{self.settings.environment}"
            f"/api/mtmlogix/customerSync/v1.0/companies({company})/customerInvoicing"
        )
        return self._request(
            "GET",
            url,
            params={"$top": top, "$filter": filters},
        ).get("value", [])

    def _post_posted_invoice_fel_action(
        self,
        posted_invoice_fel_row_id: str,
        action_name: str,
        *,
        body: dict[str, Any] | None = None,
        company_id: str | None = None,
        market: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        path = (
            "/api/mtmlogix/invoiceSync/v1.0/companies({company_id})/"
            f"postedInvoiceFelDescriptions({posted_invoice_fel_row_id})/Microsoft.NAV.{action_name}"
        )
        if timeout_seconds is None:
            return self.post_to_company(
                path,
                body or {},
                company_id=company_id,
                market=market,
            )
        return self.post_to_company(
            path,
            body or {},
            company_id=company_id,
            market=market,
            timeout_seconds=timeout_seconds,
        )

    def _post_posted_credit_memo_fel_action(
        self,
        posted_credit_memo_fel_row_id: str,
        action_name: str,
        *,
        body: dict[str, Any] | None = None,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        return self.post_to_company(
            (
                "/api/mtmlogix/invoiceSync/v1.0/companies({company_id})/"
                f"postedCreditMemoFelDescriptions({posted_credit_memo_fel_row_id})/Microsoft.NAV.{action_name}"
            ),
            body or {},
            company_id=company_id,
            market=market,
        )

    def resolve_account_by_number(
        self,
        account_number: str,
        *,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        needle = (account_number or "").strip()
        if not needle:
            return None

        escaped = needle.replace("'", "''")
        rows = self.find_entities(
            "accounts",
            filters=f"number eq '{escaped}'",
            top=1,
            market=market,
        )
        if not rows:
            return None
        return rows[0]

    def resolve_item_by_number(
        self,
        item_number: str,
        *,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        needle = (item_number or "").strip()
        if not needle:
            return None

        escaped = needle.replace("'", "''")
        rows = self.find_entities(
            "items",
            filters=f"number eq '{escaped}'",
            top=1,
            market=market,
        )
        if not rows:
            return None
        return rows[0]

    def resolve_customer_by_name(
        self,
        customer_name: str,
        *,
        market: str | None = None,
    ) -> dict[str, Any] | None:
        needle = _normalize_match_text(customer_name)
        if not needle:
            return None
        needle_name_keys = _customer_name_match_keys(customer_name)

        rows = self.get_entities("customers", top=1000, market=market).get("value", [])
        exact_matches = [
            row
            for row in rows
            if needle in {_normalize_match_text(row.get("number") or "")}
            or needle_name_keys
            & (
                _customer_name_match_keys(row.get("displayName") or "")
                | _customer_name_match_keys(row.get("name") or "")
            )
        ]
        if exact_matches:
            return _single_customer_match(exact_matches, customer_name)

        if len(needle) < 4:
            return None

        contained_matches = []
        for row in rows:
            name_candidates = (
                _customer_name_match_keys(row.get("displayName") or "")
                | _customer_name_match_keys(row.get("name") or "")
            )
            contact_candidates = (
                _normalize_match_text(row.get("email") or ""),
                _normalize_match_text(row.get("website") or ""),
            )
            if any(
                candidate
                and needle_key
                and (needle_key in candidate or candidate in needle_key)
                for candidate in name_candidates
                for needle_key in needle_name_keys
            ) or any(candidate and (needle in candidate or candidate in needle) for candidate in contact_candidates):
                contained_matches.append(row)

        if not contained_matches:
            return None
        return _single_customer_match(contained_matches, customer_name)

    def _expand_relative_path(self, path: str, company_id: str, **path_params: str) -> str:
        cleaned = path if path.startswith("/") else f"/{path}"
        rendered = cleaned.format(company_id=company_id, **path_params)
        if rendered.startswith("/api/"):
            return f"https://api.businesscentral.dynamics.com/v2.0/{self.settings.environment}{rendered}"
        if rendered.startswith("/companies("):
            return f"{self.settings.api_base_url}{rendered}"
        raise ValueError(
            "Unsupported relative path. Use an absolute URL, a path starting with "
            "'/api/', or a company-scoped path starting with '/companies('."
        )

    def resolve_payment_term(self, code_or_name: str, *, market: str | None = None) -> dict[str, Any] | None:
        needle = (code_or_name or "").strip().upper()
        if not needle:
            return None

        rows = self.get_entities("paymentTerms", top=500, market=market).get("value", [])
        for row in rows:
            if (row.get("code") or "").strip().upper() == needle:
                return row
        for row in rows:
            if (row.get("displayName") or "").strip().upper() == needle:
                return row
        return None

    def resolve_payment_method(self, code_or_name: str, *, market: str | None = None) -> dict[str, Any] | None:
        needle = (code_or_name or "").strip().upper()
        if not needle:
            return None

        rows = self.get_entities("paymentMethods", top=500, market=market).get("value", [])
        for row in rows:
            if (row.get("code") or "").strip().upper() == needle:
                return row
        for row in rows:
            if (row.get("displayName") or "").strip().upper() == needle:
                return row
        return None

    def _resolve_company_id(
        self,
        *,
        company_id: str | None,
        market: str | None,
    ) -> str | None:
        if company_id:
            return company_id

        market_settings = self.settings.get_market(market)
        if market_settings:
            return market_settings.company_id

        return self.settings.company_id


def _normalize_match_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _pdf_document_content_url(pdf_document: dict[str, Any]) -> str:
    for key in ("pdfDocumentContent@odata.mediaReadLink", "content@odata.mediaReadLink"):
        value = pdf_document.get(key)
        if value:
            return str(value)

    value = pdf_document.get("value")
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            for key in ("pdfDocumentContent@odata.mediaReadLink", "content@odata.mediaReadLink"):
                nested_value = item.get(key)
                if nested_value:
                    return str(nested_value)

    return ""


def _customer_name_match_keys(value: str) -> set[str]:
    normalized = _normalize_match_text(value)
    if not normalized:
        return set()

    keys = {normalized}
    without_suffix = _strip_common_company_suffix(normalized)
    if without_suffix:
        keys.add(without_suffix)
    return keys


def _strip_common_company_suffix(value: str) -> str:
    cleaned = value
    suffixes = (
        "sociedad anonima",
        "s a",
        "sa",
    )
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if cleaned == suffix:
                return ""
            if cleaned.endswith(f" {suffix}"):
                cleaned = cleaned[: -len(suffix)].strip()
                changed = True
    return cleaned


def _single_customer_match(rows: list[dict[str, Any]], customer_name: str) -> dict[str, Any]:
    unique_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("id") or row.get("number") or row.get("displayName") or id(row))
        unique_by_id[key] = row
    unique_rows = list(unique_by_id.values())
    if len(unique_rows) > 1:
        names = ", ".join(
            str(row.get("number") or row.get("displayName") or row.get("id") or "")
            for row in unique_rows[:5]
        )
        raise ValueError(f"More than one Business Central customer matched {customer_name}: {names}")
    return unique_rows[0]
