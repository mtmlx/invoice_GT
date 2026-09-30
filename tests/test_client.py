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
