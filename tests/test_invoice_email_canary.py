import pytest

from scripts.send_gt_invoice_email_canary_once import _evidence_blocker, _select_invoice, main
from test_invoice_delivery import FakeMXEmailBC, MX_READINESS, MX_FISCAL_UUID


def native_evidence(kind="NotFound", *, sender="consuelo@mtmlogix.com", **extra):
    fields = {"Sender": sender, "AccountId": "scenario-account",
              "FiscalUuid": MX_FISCAL_UUID, "PdfAttachmentSha256": "a" * 64,
              "XmlAttachmentSha256": "b" * 64, "AttachmentCount": "2", **extra}
    return kind + "|" + "|".join(f"{key}={value}" for key, value in fields.items())


def test_mexico_canary_requires_explicit_invoice_and_native_attachment_route():
    bc = FakeMXEmailBC()
    with pytest.raises(SystemExit, match="explicit invoice"):
        _select_invoice(bc, invoice_number=None, market="MX")
    assert _select_invoice(bc, invoice_number="B0003383", market="MX")["invoiceEmailReadiness"] == MX_READINESS
    bc.readiness = ""
    with pytest.raises(ValueError, match="native Mexico delivery route"):
        _select_invoice(bc, invoice_number="B0003383", market="MX")


@pytest.mark.parametrize("evidence", [
    "Unavailable", "NotFound", "ConfigurationError|Account missing", "SentWrongAccount|MessageId=x",
    native_evidence(sender="carlos@mtmlogix.com"),
    native_evidence("Sent", Recipient="customer@example.com", MessageId="native-id"),
    native_evidence("Sent", Recipient="mario@mtmlogix.com"),
])
def test_canary_never_treats_missing_or_wrong_native_evidence_as_safe(evidence):
    assert _evidence_blocker(evidence, market="MX")


def test_canary_accepts_only_matching_native_sent_or_notfound_evidence():
    assert _evidence_blocker(native_evidence(), market="MX") is None
    assert _evidence_blocker(native_evidence("Sent", Recipient="mario@mtmlogix.com", MessageId="native-id"), market="MX", fiscal_uuid=MX_FISCAL_UUID, expected_pdf_sha256="a" * 64) is None
    assert _evidence_blocker(native_evidence(sender="consuelo@mtmlogix.com"), market="GT") is None


@pytest.mark.parametrize("changed", [
    {"FiscalUuid": "different-uuid"}, {"AttachmentCount": "1"},
    {"PdfAttachmentSha256": ""}, {"XmlAttachmentSha256": "not-a-hash"},
])
def test_mexico_canary_holds_incomplete_native_attachment_proof(changed):
    evidence = native_evidence("Sent", Recipient="mario@mtmlogix.com", MessageId="native-id", **changed)
    assert _evidence_blocker(evidence, market="MX", fiscal_uuid=MX_FISCAL_UUID, expected_pdf_sha256="a" * 64)


def test_mexico_canary_rejects_unreviewed_or_changed_pdf_hash():
    evidence = native_evidence("Sent", Recipient="mario@mtmlogix.com", MessageId="native-id")
    assert _evidence_blocker(evidence, market="MX", fiscal_uuid=MX_FISCAL_UUID)
    assert _evidence_blocker(evidence, market="MX", fiscal_uuid=MX_FISCAL_UUID, expected_pdf_sha256="c" * 64)


def test_mexico_cli_requires_reviewed_pdf_hash_before_connection(monkeypatch):
    monkeypatch.setattr("sys.argv", ["canary", "--market", "MX", "--invoice-number", "B0003383", "--apply"])
    monkeypatch.setattr("scripts.send_gt_invoice_email_canary_once.BusinessCentralClient",
                        lambda *args: pytest.fail("No connection should be created without the reviewed hash"))
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
