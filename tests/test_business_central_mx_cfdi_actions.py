from types import SimpleNamespace
from typing import Any

from business_central_client.client import BusinessCentralClient


class FakeBusinessCentralClient(BusinessCentralClient):
    def __init__(self) -> None:
        self.settings = SimpleNamespace()
        self.posts: list[dict[str, Any]] = []

    def post_to_company(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        company_id: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        post = {
            "path": path,
            "payload": payload,
            "company_id": company_id,
            "market": market,
        }
        self.posts.append(post)
        return post


def test_set_mx_substitution_relation_posts_old_invoice_number() -> None:
    client = FakeBusinessCentralClient()

    result = client.set_mx_substitution_relation(
        "posted-row-id",
        "B0003330",
        market="MX",
    )

    assert result["path"].endswith(
        "postedInvoiceFelDescriptions(posted-row-id)/Microsoft.NAV.SetMxSubstitutionRelation"
    )
    assert result["payload"] == {"oldInvoiceNumber": "B0003330"}
    assert result["market"] == "MX"


def test_stamp_mx_invoice_posts_empty_payload() -> None:
    client = FakeBusinessCentralClient()

    result = client.stamp_mx_invoice("posted-row-id", market="MX")

    assert result["path"].endswith(
        "postedInvoiceFelDescriptions(posted-row-id)/Microsoft.NAV.StampMxInvoice"
    )
    assert result["payload"] == {}
    assert result["market"] == "MX"


def test_set_mx_payment_fields_posts_terms_and_method() -> None:
    client = FakeBusinessCentralClient()

    result = client.set_mx_payment_fields(
        "draft-row-id",
        payment_terms_code="PPD",
        payment_method_code="99",
        market="MX",
    )

    assert result["path"].endswith(
        "mxSalesInvoiceDrafts(draft-row-id)/Microsoft.NAV.SetMxPaymentFields"
    )
    assert result["payload"] == {
        "paymentTermsCode": "PPD",
        "paymentMethodCode": "99",
    }
    assert result["market"] == "MX"


def test_cancel_mx_invoice_with_substitution_posts_reason_and_substitute() -> None:
    client = FakeBusinessCentralClient()

    result = client.cancel_mx_invoice_with_substitution(
        "posted-row-id",
        "B0003331",
        cancellation_reason_id="01",
        market="MX",
    )

    assert result["path"].endswith(
        "postedInvoiceFelDescriptions(posted-row-id)/Microsoft.NAV.CancelMxInvoiceWithSubstitution"
    )
    assert result["payload"] == {
        "substitutionInvoiceNumber": "B0003331",
        "cancellationReasonId": "01",
    }
    assert result["market"] == "MX"


def test_sales_credit_memo_lifecycle_posts_standard_api_actions() -> None:
    client = FakeBusinessCentralClient()

    create_result = client.create_sales_credit_memo({"customerId": "customer-id"}, market="GT")
    line_result = client.create_sales_credit_memo_line(
        "credit-memo-id",
        {"lineType": "Item", "lineObjectNumber": "DINT000000002"},
        market="GT",
    )
    post_result = client.post_sales_credit_memo("credit-memo-id", market="GT")
    cancel_result = client.cancel_sales_credit_memo("credit-memo-id", market="GT")

    assert create_result["path"] == "/companies({company_id})/salesCreditMemos"
    assert create_result["payload"] == {"customerId": "customer-id"}
    assert line_result["path"] == "/companies({company_id})/salesCreditMemos(credit-memo-id)/salesCreditMemoLines"
    assert line_result["payload"] == {"lineType": "Item", "lineObjectNumber": "DINT000000002"}
    assert post_result["path"] == "/companies({company_id})/salesCreditMemos(credit-memo-id)/Microsoft.NAV.post"
    assert post_result["payload"] == {}
    assert cancel_result["path"] == "/companies({company_id})/salesCreditMemos(credit-memo-id)/Microsoft.NAV.cancel"
    assert cancel_result["payload"] == {}
    assert all(post["market"] == "GT" for post in client.posts)


def test_stamp_posted_credit_memo_fel_posts_empty_payload() -> None:
    client = FakeBusinessCentralClient()

    result = client.stamp_posted_credit_memo_fel("posted-credit-memo-row-id", market="GT")

    assert result["path"].endswith(
        "postedCreditMemoFelDescriptions(posted-credit-memo-row-id)/Microsoft.NAV.StampFelCreditMemo"
    )
    assert result["payload"] == {}
    assert result["market"] == "GT"


def test_cancel_posted_credit_memo_fel_with_motive_posts_motive() -> None:
    client = FakeBusinessCentralClient()

    result = client.cancel_posted_credit_memo_fel_with_motive(
        "posted-credit-memo-row-id",
        "Customer requested cancellation",
        market="GT",
    )

    assert result["path"].endswith(
        "postedCreditMemoFelDescriptions(posted-credit-memo-row-id)/Microsoft.NAV.CancelFelCreditMemoWithMotive"
    )
    assert result["payload"] == {"motiveText": "Customer requested cancellation"}
    assert result["market"] == "GT"


def test_cancel_posted_credit_memo_fel_with_issue_datetime_posts_override() -> None:
    client = FakeBusinessCentralClient()

    result = client.cancel_posted_credit_memo_fel_with_motive(
        "posted-credit-memo-row-id",
        "Customer requested cancellation",
        issue_datetime_text="2026-06-17T12:00:00",
        market="GT",
    )

    assert result["path"].endswith(
        "postedCreditMemoFelDescriptions(posted-credit-memo-row-id)/Microsoft.NAV.CancelFelCreditMemoWithMotiveAndIssueDateTime"
    )
    assert result["payload"] == {
        "motiveText": "Customer requested cancellation",
        "issueDateTimeText": "2026-06-17T12:00:00",
    }
    assert result["market"] == "GT"


def test_corrected_cancellation_uses_additive_action_without_changing_legacy(monkeypatch) -> None:
    client = FakeBusinessCentralClient()
    calls = []
    def post(invoice_id, action, **kwargs):
        calls.append((invoice_id, action, kwargs))
        return {}
    monkeypatch.setattr(client, "_post_posted_invoice_fel_action", post)
    client.request_mx_cancellation("old-row", {"expectedAmountIncludingVat": 15307.2})
    client.request_mx_cancellation("old-row", {"expectedAmountIncludingVat": 15347.2,
                                               "expectedOriginalAmountIncludingVat": 15307.2})
    assert [call[1] for call in calls] == ["RequestMxCancellation", "RequestMxCorrectedCancellation"]
    assert all(call[2]["market"] == "MX" and call[2]["timeout_seconds"] == 180 for call in calls)
