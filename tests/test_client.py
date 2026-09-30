import json

import pytest
import requests

from business_central_client.client import BusinessCentralClient, _raise_for_status_with_detail
from business_central_client.config import MarketSettings, Settings


def make_settings() -> Settings:
    return Settings(
        tenant_id="tenant-id",
        client_id="client-id",
        client_secret="secret",
        environment="Production",
        company_id="company-id",
        default_market="MX",
        markets={
            "MX": MarketSettings(
                key="MX",
                company_id="mx-company-id",
                local_currency_code="MXN",
                supported_currency_codes=("MXN", "USD"),
            ),
            "GT": MarketSettings(
                key="GT",
                company_id="gt-company-id",
                local_currency_code="GTQ",
                supported_currency_codes=("GTQ", "USD"),
            ),
        },
        api_version="v2.0",
        timeout_seconds=30,
        user_agent="ContractingTool/0.1",
        custom_pricing_path=None,
    )


def test_api_base_url() -> None:
    settings = make_settings()
    assert (
        settings.api_base_url
        == "https://api.businesscentral.dynamics.com/v2.0/Production/api/v2.0"
    )


def test_mx_cancellation_read_uses_configured_mexico_company(monkeypatch) -> None:
    client = BusinessCentralClient(make_settings())
    requests_seen = []

    def request(method, url, **kwargs):
        requests_seen.append((method, url, kwargs))
        return {"value": []}

    monkeypatch.setattr(client, "_request", request)
    assert client.get_mx_cancellation("B0003376") is None
    method, url, kwargs = requests_seen[0]
    assert method == "GET"
    assert "/companies(mx-company-id)/mxCancellations" in url
    assert kwargs["params"]["$filter"] == "invoiceNumber eq 'B0003376'"


def test_expand_company_scoped_path() -> None:
    client = BusinessCentralClient(make_settings())
    assert (
        client._expand_relative_path("/companies({company_id})/items", "abc")
        == "https://api.businesscentral.dynamics.com/v2.0/Production/api/v2.0/companies(abc)/items"
    )


def test_expand_custom_api_path() -> None:
    client = BusinessCentralClient(make_settings())
    assert (
        client._expand_relative_path(
            "/api/contoso/pricing/v1.0/companies({company_id})/priceCalculations",
            "abc",
        )
        == "https://api.businesscentral.dynamics.com/v2.0/Production/api/contoso/pricing/v1.0/companies(abc)/priceCalculations"
    )


def test_resolve_market_company_id() -> None:
    client = BusinessCentralClient(make_settings())
    assert client._resolve_company_id(company_id=None, market="GT") == "gt-company-id"
    assert client._resolve_company_id(company_id=None, market="MX") == "mx-company-id"
    assert client._resolve_company_id(company_id="override-id", market="GT") == "override-id"


def test_mx_email_preparation_binds_cc_and_invoice_identity_without_send(monkeypatch):
    client = BusinessCentralClient(make_settings())
    actions = []

    def post_action(row_id, action_name, **kwargs):
        actions.append({"row_id": row_id, "action": action_name, **kwargs})
        return actions[-1]

    monkeypatch.setattr(client, "_post_posted_invoice_fel_action", post_action)
    client.prepare_invoice_email_delivery(
        "posted-header-id", cc_recipients="mario@mtmlogix.com",
        expected_fiscal_uuid="bb14b70f-21d0-423b-8c6d-f2d46bdad658",
        expected_pdf_sha256="a" * 64,
        expected_external_document_number="UW-26-ES-002", expected_amount_including_vat=15307.20,
        expected_due_date="2026-11-03",
    )
    assert len(actions) == 1
    assert actions[0]["action"] == "PrepareInvoiceEmailDelivery"
    assert actions[0]["market"] == "MX"
    assert actions[0]["body"] == {
        "ccRecipients": "mario@mtmlogix.com", "expectedFiscalUuid": "bb14b70f-21d0-423b-8c6d-f2d46bdad658",
        "expectedPdfSha256": "a" * 64,
        "expectedExternalDocumentNumber": "UW-26-ES-002", "expectedAmountIncludingVat": 15307.20,
        "expectedDueDate": "2026-11-03",
    }
    with pytest.raises(ValueError, match="only for Mexico"):
        client.prepare_invoice_email_delivery(
            "posted-header-id", cc_recipients="", expected_fiscal_uuid="uuid",
            expected_pdf_sha256="a" * 64,
            expected_external_document_number="GT-SHIPMENT", expected_amount_including_vat=1,
            expected_due_date="2026-11-03", market="GT",
        )
    assert len(actions) == 1


def test_mx_canary_requires_reviewed_pdf_hash_and_uses_separate_action(monkeypatch):
    client = BusinessCentralClient(make_settings())
    actions = []
    monkeypatch.setattr(client, "_post_posted_invoice_fel_action", lambda row_id, action, **kwargs: actions.append((row_id, action, kwargs)))
    with pytest.raises(ValueError, match="independently reviewed"):
        client.send_posted_invoice_test_email_to_mario("posted-header-id", market="MX")
    assert actions == []
    client.send_posted_invoice_test_email_to_mario("posted-header-id", market="MX", expected_pdf_sha256="a" * 64)
    assert actions[0][1] == "SendApprovedMxInvoiceTestEmailToMario"
    assert actions[0][2]["body"] == {"expectedPdfSha256": "a" * 64}
    client.send_posted_invoice_test_email_to_mario("gt-posted-header-id", market="GT")
    assert actions[1][1] == "SendApprovedInvoiceTestEmailToMario"
    assert actions[1][2]["body"] is None


def test_raise_for_status_includes_business_central_error_message() -> None:
    response = requests.Response()
    response.status_code = 400
    response.url = "https://api.businesscentral.dynamics.com/v2.0/Production/api/v2.0/companies(x)/salesInvoices"
    response._content = json.dumps(
        {
            "error": {
                "code": "Application_FieldValidationException",
                "message": "Gen. Bus. Posting Group must have a value in Customer: No.=C00107.",
            }
        }
    ).encode("utf-8")

    with pytest.raises(requests.HTTPError) as exc_info:
        _raise_for_status_with_detail(response)

    message = str(exc_info.value)
    assert "400 Client Error" in message
    assert "Business Central detail" in message
    assert "Gen. Bus. Posting Group must have a value" in message


def test_resolve_customer_by_name_accepts_unique_contained_match() -> None:
    class CustomerClient(BusinessCentralClient):
        def get_entities(self, entity_name, *, top=None, filters=None, company_id=None, market=None):
            assert entity_name == "customers"
            assert top == 1000
            assert market == "GT"
            return {
                "value": [
                    {
                        "id": "customer-1",
                        "number": "C0001",
                        "displayName": "MASESA, SOCIEDAD ANONIMA",
                        "email": "",
                        "website": "",
                    },
                    {
                        "id": "customer-2",
                        "number": "C0002",
                        "displayName": "OTHER CUSTOMER",
                        "email": "",
                        "website": "",
                    },
                ]
            }

    client = CustomerClient(make_settings())

    assert client.resolve_customer_by_name("MASESA", market="GT") == {
        "id": "customer-1",
        "number": "C0001",
        "displayName": "MASESA, SOCIEDAD ANONIMA",
        "email": "",
        "website": "",
    }


def test_resolve_customer_by_name_accepts_unique_email_or_website_match() -> None:
    class CustomerClient(BusinessCentralClient):
        def get_entities(self, entity_name, *, top=None, filters=None, company_id=None, market=None):
            assert entity_name == "customers"
            return {
                "value": [
                    {
                        "id": "customer-1",
                        "number": "C0001",
                        "displayName": "MOTOCOM, SOCIEDAD ANONIMA",
                        "email": "kevin.ortigoza@masesa.com",
                        "website": "https://masesa.com",
                    },
                    {
                        "id": "customer-2",
                        "number": "C0002",
                        "displayName": "OTHER CUSTOMER",
                        "email": "",
                        "website": "",
                    },
                ]
            }

    client = CustomerClient(make_settings())

    assert client.resolve_customer_by_name("MASESA", market="GT") == {
        "id": "customer-1",
        "number": "C0001",
        "displayName": "MOTOCOM, SOCIEDAD ANONIMA",
        "email": "kevin.ortigoza@masesa.com",
        "website": "https://masesa.com",
    }


def test_resolve_customer_by_name_matches_sa_to_sociedad_anonima() -> None:
    class CustomerClient(BusinessCentralClient):
        def get_entities(self, entity_name, *, top=None, filters=None, company_id=None, market=None):
            assert entity_name == "customers"
            return {
                "value": [
                    {
                        "id": "customer-1",
                        "number": "C00058",
                        "displayName": "SUPER AUTO REPUESTOS, SOCIEDAD ANONIMA",
                    }
                ]
            }

    client = CustomerClient(make_settings())

    assert client.resolve_customer_by_name("Super Auto Repuestos S.A.", market="GT") == {
        "id": "customer-1",
        "number": "C00058",
        "displayName": "SUPER AUTO REPUESTOS, SOCIEDAD ANONIMA",
    }
