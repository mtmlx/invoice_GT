from types import SimpleNamespace

from clickup_integration.create_preview import (
    apply_clickup_bc_customer_create,
    prepare_clickup_bc_created_customer_writeback,
)


class FakeBCClient:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            tenant_id="tenant-id",
            environment="Production",
            customer_invoicing_sync_path=None,
        )

    def get_company_metadata(self, *, market: str | None = None, company_id: str | None = None):
        if market == "GT":
            return {"name": "MTM_GT_PROD", "displayName": "MTM LOGIX GUATEMALA, SOCIEDAD ANONIMA"}
        return None

    def post_to_company(self, path: str, payload: dict, *, company_id: str | None = None, market: str | None = None):
        assert path == "/companies({company_id})/customers"
        assert market == "GT"
        assert payload["paymentTermsId"] == "term-7"
        assert payload["paymentMethodId"] == "method-credit"
        assert payload["creditLimit"] == 21400.23
        return {
            "id": "bc-created-id",
            "number": "C00123",
            "displayName": payload["displayName"],
        }

    def resolve_payment_term(self, code_or_name: str, *, market: str | None = None):
        assert market == "GT"
        if code_or_name == "7 DÍAS":
            return {"id": "term-7", "code": "7 DÍAS"}
        return None

    def resolve_payment_method(self, code_or_name: str, *, market: str | None = None):
        assert market == "GT"
        if code_or_name == "CREDITO":
            return {"id": "method-credit", "code": "CREDITO"}
        return None


class FakeBCClientWithBrokenExtension(FakeBCClient):
    def __init__(self) -> None:
        super().__init__()
        self.settings.customer_invoicing_sync_path = "/api/customers({customer_id})/invoicing"

    def patch_company_path(self, path: str, payload: dict, *, company_id: str | None = None, market: str | None = None, customer_id: str | None = None):
        raise RuntimeError("custom invoicing endpoint unavailable")


def make_clickup_summary() -> dict:
    return {
        "task_id": "task-1",
        "custom_id": "MTM-2035664",
        "name": "FPK",
        "status": "current customer",
        "market": "GT",
        "custom_fields": {
            "Business Central Legal Name": {"value": "FPK Electronicos, S.A.", "id": "legal-id"},
            "Clientes/": {
                "value": 73,
                "type_config": {"options": [{"name": "FPK ELECTRONICOS.S.A.", "orderindex": 73}]},
            },
            "Customer Tax ID": {"value": "304932-9"},
            "Contact E-mail 1": {"value": "mayra@fpkelectronicos.com"},
            "Contact Phone 1": {"value": "+502 5511 2349"},
            "Webpage": {"value": "https://fpk.com.gt"},
            "Customer Address": {"value": "11 Calle 5 - 59, Guatemala"},
            "Credit Days Required": {"value": "7"},
            "Credit amount approved": {
                "id": "54574add-833f-42a5-b027-3b0d64ef95af",
                "value": "21400.23",
            },
            "Business Central Customer Number": {"id": "field-number"},
            "Business Central Customer ID": {"id": "field-id"},
            "Business Central Customer Link": {"id": "field-link"},
            "BC Match Status": {
                "id": "field-status",
                "type_config": {
                    "options": [
                        {"id": "opt-2", "name": "Confirmed", "orderindex": 2},
                    ]
                },
            },
        },
    }


def test_prepare_clickup_bc_created_customer_writeback() -> None:
    payload = prepare_clickup_bc_created_customer_writeback(
        clickup_summary=make_clickup_summary(),
        created_customer={
            "id": "bc-created-id",
            "number": "C00123",
            "displayName": "FPK ELECTRONICOS, S.A.",
        },
        market="GT",
        bc_client=FakeBCClient(),
    )

    assert payload["bc_customer_number"] == "C00123"
    assert payload["bc_customer_id"] == "bc-created-id"
    assert payload["bc_match_status"] == "opt-2"


def test_apply_clickup_bc_customer_create() -> None:
    result = apply_clickup_bc_customer_create(
        clickup_summary=make_clickup_summary(),
        current_match_result={"status": "no_match", "market": "GT", "candidates": []},
        bc_client=FakeBCClient(),
    )

    assert result["status"] == "applied"
    assert result["created_customer"]["number"] == "C00123"
    assert result["created_customer"]["displayName"] == "FPK ELECTRONICOS, S.A."
    assert result["writeback"]["bc_customer_number"] == "C00123"


def test_apply_clickup_bc_customer_create_blocks_duplicate_risk() -> None:
    result = apply_clickup_bc_customer_create(
        clickup_summary=make_clickup_summary(),
        current_match_result={
            "status": "likely_match",
            "market": "GT",
            "candidates": [{"number": "C00001", "displayName": "Existing"}],
        },
        bc_client=FakeBCClient(),
    )

    assert result["status"] == "blocked_duplicate_risk"


def test_apply_clickup_bc_customer_create_tolerates_extension_failure() -> None:
    result = apply_clickup_bc_customer_create(
        clickup_summary=make_clickup_summary(),
        current_match_result={"status": "no_match", "market": "GT", "candidates": []},
        bc_client=FakeBCClientWithBrokenExtension(),
    )

    assert result["status"] == "applied"
    assert result["created_customer"]["number"] == "C00123"
    assert result["invoicing_extension"]["status"] == "failed"
    assert "extension update failed" in result["invoicing_extension"]["message"]
    assert "custom invoicing endpoint unavailable" not in str(result)
