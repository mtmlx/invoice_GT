import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "bc_extension" / "customer_invoicing_sync" / "src"
APP_JSON = ROOT / "bc_extension" / "customer_invoicing_sync" / "app.json"


def _read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_manual_invoice_email_install_is_disabled_and_has_no_backfill() -> None:
    setup = _read("MtmManualInvoiceEmailSetupMgt.Codeunit.al")
    install = _read("MtmManualInvoiceEmailInstall.Codeunit.al")
    upgrade = _read("MtmManualInvoiceEmailUpgrade.Codeunit.al")

    assert 'Setup."Capture Enabled" := false;' in setup
    assert 'Setup."Customer Send Enabled" := false;' in setup
    assert "EnsureJobQueue(Setup);" not in install
    assert "EnsureJobQueue(Setup);" not in upgrade
    assert "Sales Invoice Header" not in install
    assert "Sales Invoice Header" not in upgrade


def test_posting_subscriber_only_enqueues_and_never_sends_inline() -> None:
    source = _read("MtmManualInvoiceEmailEnqueue.Codeunit.al")

    assert "OnAfterInsertEvent" in source
    assert 'if not Setup."Capture Enabled" then' in source
    assert "TryEnqueue(Rec, Setup)" in source
    assert "SendApprovedInvoiceEmail" not in source
    assert "Email.Send" not in source
    assert "Commit();" not in source


def test_worker_waits_for_fel_and_uses_verified_idempotent_sender() -> None:
    source = _read("MtmManualInvoiceEmailWorker.Codeunit.al")

    stamp_check = source.index("if not IsStamped(PostedInvoice) then begin")
    send_call = source.index("TrySendApprovedInvoice(PostedInvoice)")
    assert stamp_check < send_call
    assert 'if not Setup."Customer Send Enabled" then begin' in source
    assert "InvoiceCustomerEmailMgt.SendApprovedInvoiceEmail(PostedInvoice);" in source
    assert '(Audit.Status <> Audit.Status::Sent) or (not Audit."Native Sent Verified")' in source


def test_worker_stops_ambiguous_failures_for_operator_review() -> None:
    source = _read("MtmManualInvoiceEmailWorker.Codeunit.al")

    assert "Queue.Status := Queue.Status::ReviewRequired;" in source
    assert 'Queue."Next Attempt At" := 0DT;' in source
    assert "No se reenviara automaticamente" in source
    assert "ResetForOperatorRetry" in source
    assert "Queue.Status := Queue.Status::Sending;" in source
    assert "if Queue.Status = Queue.Status::Sending then begin" in source
    assert "if Queue.Status <> Queue.Status::ReviewRequired then" in source


def test_setup_api_does_not_allow_arbitrary_state_patch() -> None:
    source = _read("MtmManualInvoiceEmailSetupApi.Page.al")

    assert "ModifyAllowed = false;" in source
    assert "procedure PrepareCaptureOnly" in source
    assert "procedure EnableCustomerSend" in source
    assert "procedure DisableCustomerSend" in source
    assert "procedure DisableAll" in source
    assert 'Permissions = tabledata "MTM Manual Inv Email Setup" = R;' in source


def test_extension_version_contains_manual_invoice_queue_release() -> None:
    manifest = json.loads(APP_JSON.read_text(encoding="utf-8"))
    assert tuple(map(int, manifest["version"].split("."))) >= (0, 1, 8, 51)
