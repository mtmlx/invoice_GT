from types import SimpleNamespace

from clickup_integration.bc_sync import prepare_clickup_to_bc_customer_sync
from clickup_integration.bc_sync import apply_clickup_to_bc_customer_sync


class FakeBCClient:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            tenant_id="tenant-id",
            environment="Production",
            customer_invoicing_sync_path=None,
        )

    def get_entity(self, entity_name: str, entity_id: str, *, market: str | None = None):
        assert entity_name == "customers"
        assert entity_id == "bc-id-1"
        assert market == "GT"
        return {
            "id": "bc-id-1",
            "number": "C00025",
            "displayName": "GIAI INNOVATIONS, SOCIEDAD ANONIMA",
            "email": "old@example.com",
            "phoneNumber": "50401010",
            "website": "",
            "taxRegistrationNumber": "96271256",
            "addressLine1": "OLD ADDRESS",
            "paymentTermsId": "old-term",
            "paymentMethodId": "old-method",
            "creditLimit": 0,
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

    def patch_entity(self, entity_name: str, entity_id: str, payload: dict, *, market: str | None = None):
        assert entity_name == "customers"
        assert entity_id == "bc-id-1"
        assert market == "GT"
        return {
            "id": entity_id,
            **payload,
        }


class FakeBCClientWithBrokenExtension(FakeBCClient):
    def __init__(self) -> None:
        super().__init__()
        self.settings.customer_invoicing_sync_path = "/api/customers({customer_id})/invoicing"

    def patch_company_path(self, path: str, payload: dict, *, company_id: str | None = None, market: str | None = None, customer_id: str | None = None):
        raise RuntimeError("custom invoicing endpoint unavailable")


def test_prepare_clickup_to_bc_customer_sync() -> None:
    clickup_summary = {
        "task_id": "task-1",
        "status": "current customer",
        "market": "GT",
        "custom_fields": {
            "Business Central Customer ID": {"value": "bc-id-1"},
            "Business Central Customer Number": {"value": "C00025"},
            "BC Match Status": {
                "value": 2,
                "type_config": {
                    "options": [
                        {"id": "opt-0", "name": "Unmatched", "orderindex": 0},
                        {"id": "opt-1", "name": "Likely Match", "orderindex": 1},
                        {"id": "opt-2", "name": "Confirmed", "orderindex": 2},
                    ]
                },
            },
            "Business Central Legal Name": {"value": "GIAI INNOVATIONS, SOCIEDAD ANONIMA"},
            "Customer Tax ID": {"value": "96271256"},
            "Contact E-mail 1": {"value": "new@example.com"},
            "Contact Phone 1": {"value": "+502 5950 9758"},
            "Webpage": {"value": "https://biorgani.tech/"},
            "Customer Address": {"value": "NEW ADDRESS"},
            "Credit Days Required": {"value": "7"},
            "Credit amount approved": {
                "id": "54574add-833f-42a5-b027-3b0d64ef95af",
                "value": "21400.23",
            },
            "Contact Name 1": {"value": "Magdalena Perez"},
        },
    }

    preview = prepare_clickup_to_bc_customer_sync(
        clickup_summary=clickup_summary,
        bc_client=FakeBCClient(),
    )

    assert preview["status"] == "dry_run_ready"
    assert preview["proposed_bc_patch"] == {
        "email": "new@example.com",
        "phoneNumber": "+502 5950 9758",
        "website": "https://biorgani.tech/",
        "addressLine1": "NEW ADDRESS",
        "paymentTermsId": "term-7",
        "paymentMethodId": "method-credit",
        "creditLimit": 21400.23,
    }
    assert preview["clickup_sources"]["phone_field"] == "Contact Phone 1"
    assert preview["clickup_sources"]["email_field"] == "Contact E-mail 1"
    assert preview["proposed_bc_invoicing_payload"] == {
        "cfdiCustomerName": "GIAI INNOVATIONS, SOCIEDAD ANONIMA",
        "vatRegistrationNumber": "96271256",
        "invoiceEmail": "new@example.com",
        "correoFactura": "new@example.com",
        "contactName": "Magdalena Perez",
        "contactEmail": "new@example.com",
        "contactPhone": "+502 5950 9758",
        "paymentTermsCode": "7 DÍAS",
        "paymentMethodCode": "CREDITO",
        "cashFlowPaymentTermsCode": "7 DÍAS",
        "copySellToAddressTo": "Company",
        "taxIdentificationType": "Legal Entity",
        "generalBusinessPostingGroupCode": "NAC",
        "customerPostingGroupCode": "NAC",
    }


def test_prepare_clickup_to_bc_customer_sync_blocks_unconfirmed() -> None:
    clickup_summary = {
        "task_id": "task-1",
        "status": "current customer",
        "market": "GT",
        "custom_fields": {
            "Business Central Customer ID": {"value": "bc-id-1"},
            "BC Match Status": {
                "value": 1,
                "type_config": {
                    "options": [
                        {"id": "opt-0", "name": "Unmatched", "orderindex": 0},
                        {"id": "opt-1", "name": "Likely Match", "orderindex": 1},
                        {"id": "opt-2", "name": "Confirmed", "orderindex": 2},
                    ]
                },
            },
            "Contact Phone 1": {"value": "+502 5950 9758"},
            "Webpage": {"value": "https://biorgani.tech/"},
        },
    }

    preview = prepare_clickup_to_bc_customer_sync(
        clickup_summary=clickup_summary,
        bc_client=FakeBCClient(),
    )

    assert preview["status"] == "not_confirmed"


def test_apply_clickup_to_bc_customer_sync_tolerates_extension_failure() -> None:
    clickup_summary = {
        "task_id": "task-1",
        "status": "current customer",
        "market": "GT",
        "custom_fields": {
            "Business Central Customer ID": {"value": "bc-id-1"},
            "Business Central Customer Number": {"value": "C00025"},
            "BC Match Status": {
                "value": 2,
                "type_config": {
                    "options": [
                        {"id": "opt-0", "name": "Unmatched", "orderindex": 0},
                        {"id": "opt-1", "name": "Likely Match", "orderindex": 1},
                        {"id": "opt-2", "name": "Confirmed", "orderindex": 2},
                    ]
                },
            },
            "Business Central Legal Name": {"value": "GIAI INNOVATIONS, SOCIEDAD ANONIMA"},
            "Customer Tax ID": {"value": "96271256"},
            "Contact E-mail 1": {"value": "new@example.com"},
            "Contact Phone 1": {"value": "+502 5950 9758"},
            "Webpage": {"value": "https://biorgani.tech/"},
            "Customer Address": {"value": "NEW ADDRESS"},
            "Credit Days Required": {"value": "7"},
            "Credit amount approved": {
                "id": "54574add-833f-42a5-b027-3b0d64ef95af",
                "value": "21400.23",
            },
            "Contact Name 1": {"value": "Magdalena Perez"},
        },
    }

    result = apply_clickup_to_bc_customer_sync(
        clickup_summary=clickup_summary,
        bc_client=FakeBCClientWithBrokenExtension(),
    )

    assert result["status"] == "applied"
    assert result["updated_customer"]["email"] == "new@example.com"
    assert result["invoicing_extension"]["status"] == "failed"
    assert "extension update failed" in result["invoicing_extension"]["message"]
    assert "custom invoicing endpoint unavailable" not in str(result)
