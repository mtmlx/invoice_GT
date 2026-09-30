from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POSTED_INVOICE_API = (
    ROOT
    / "bc_extension"
    / "customer_invoicing_sync"
    / "src"
    / "PostedInvoiceFelDescriptionApi.Page.al"
)
EMAIL_MGT = (
    ROOT
    / "bc_extension"
    / "customer_invoicing_sync"
    / "src"
    / "MtmInvoiceCustomerEmailMgt.Codeunit.al"
)
EMAIL_AUDIT = (
    ROOT
    / "bc_extension"
    / "customer_invoicing_sync"
    / "src"
    / "MtmInvoiceEmailAudit.Table.al"
)
CANARY_EVIDENCE_API = (
    ROOT
    / "bc_extension"
    / "customer_invoicing_sync"
    / "src"
    / "MtmInvoiceEmailCanaryEvidenceApi.Page.al"
)
SMTP_PREFLIGHT = ROOT / "scripts" / "check_bc_smtp_oauth_preflight.py"
EMAIL_LOGO = (
    ROOT
    / "webhook_bridge"
    / "assets"
    / "mtm-logix-email-logo-porcelain-v1.png"
)


def _send_fel_invoice_body() -> str:
    source = POSTED_INVOICE_API.read_text(encoding="utf-8")
    start = source.index("procedure SendFelInvoice")
    end = source.index("[ServiceEnabled]", start + 1)
    return source[start:end]


def test_send_fel_invoice_fails_closed_before_legacy_provider_send() -> None:
    body = _send_fel_invoice_body()

    assert "Error(" in body
    assert "LEGACY FEL CUSTOMER SEND IS DISABLED" in body
    assert "EnvioFactura" not in body


def test_native_invoice_email_requires_exact_scenario_account_and_sent_evidence() -> None:
    source = EMAIL_MGT.read_text(encoding="utf-8")

    assert "IsThereEmailAccountSetForScenario" in source
    assert "GetEmailAccount" in source
    assert "consuelo@mtmlogix.com" in source
    assert "Email.Send(EmailMessage, SenderAccount)" in source
    assert "GetSentEmailsForRecord" in source
    assert "GetMessageId()" in source
    assert "GetAccountId()" in source
    assert "Document-Mailing" not in source


def test_native_invoice_email_uses_controlled_https_command_era_logo() -> None:
    source = EMAIL_MGT.read_text(encoding="utf-8")

    assert EMAIL_LOGO.is_file()
    assert EMAIL_LOGO.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (
        "https://mhth6mu5g8.execute-api.us-east-1.amazonaws.com/"
        "assets/mtm-logix-email-logo-porcelain-v1.png"
    ) in source
    assert "<img src=\"' + LogoUrlLbl + '\"" in source
    assert "NavApp.GetResource" not in source
    assert "'image/png', true" not in source


def test_native_invoice_email_uses_approved_footer_line() -> None:
    source = EMAIL_MGT.read_text(encoding="utf-8")

    assert "Beyond Visibility. Into Command." in source
    assert "MTM Logix | Logistics with command and clarity" not in source


def test_invoice_pdf_render_is_filtered_to_one_posted_invoice() -> None:
    source = EMAIL_MGT.read_text(encoding="utf-8")
    start = source.index("local procedure TryRenderApprovedInvoicePdf")
    end = source.index("[TryFunction]", start + 1)
    body = source[start:end]

    assert body.index("PostedInvoice.SetRecFilter();") < body.index("InvoiceRef.GetTable(PostedInvoice);")
    assert body.index("InvoiceRef.GetTable(PostedInvoice);") < body.index("Report.SaveAs(")


def test_email_audit_persists_native_message_and_verification_state() -> None:
    source = EMAIL_AUDIT.read_text(encoding="utf-8")

    assert '"BC Email Message Id"' in source
    assert '"Sender Account Id"' in source
    assert '"Native Send Accepted"' in source
    assert '"Native Sent Verified"' in source


def test_failed_email_audit_commits_before_api_error() -> None:
    source = EMAIL_MGT.read_text(encoding="utf-8")
    start = source.index("local procedure FailAudit")
    end = source.index("[TryFunction]", start)
    body = source[start:end]

    assert body.index("Commit();") < body.index("Error(ErrorText);")


def test_failed_native_send_captures_outbox_provider_evidence() -> None:
    source = EMAIL_MGT.read_text(encoding="utf-8")
    start = source.index("if not TrySendInvoiceEmail(MessageId, SenderAccount) then begin")
    end = source.index("        end;", start) + len("        end;")
    body = source[start:end]

    assert "GetSendFailureEvidence(PostedInvoice, MessageId, GetLastErrorText())" in body
    assert "FailAudit" in body


def test_internal_canary_is_hard_bound_and_does_not_consume_customer_audit() -> None:
    source = EMAIL_MGT.read_text(encoding="utf-8")
    start = source.index("procedure SendApprovedInvoiceTestEmailToMario")
    end = source.index("\n    procedure ", start + len("procedure SendApprovedInvoiceTestEmailToMario"))
    wrapper = source[start:end]
    start = source.index("local procedure SendInternalCanary")
    end = source.index("\n    procedure ", start + len("local procedure SendInternalCanary"))
    body = source[start:end]

    assert "mario@mtmlogix.com" in source
    assert "SendInternalCanary(PostedInvoice, '')" in wrapper
    assert "TestRecipientLbl" in body
    assert "ResolveRequiredSenderAccount" in body
    assert "TryRenderApprovedInvoicePdf" in body
    assert "HasNativeSentEmailEvidence" in body
    assert "MTM Invoice Email Audit" not in body


def test_internal_canary_is_exposed_as_a_separate_service_action() -> None:
    source = POSTED_INVOICE_API.read_text(encoding="utf-8")

    assert "procedure SendApprovedInvoiceTestEmailToMario" in source
    assert "InvoiceCustomerEmailMgt.SendApprovedInvoiceTestEmailToMario(Rec);" in source


def test_internal_canary_exposes_scoped_sent_or_outbox_evidence() -> None:
    management_source = EMAIL_MGT.read_text(encoding="utf-8")
    evidence_page_source = CANARY_EVIDENCE_API.read_text(encoding="utf-8")

    assert "procedure GetApprovedInvoiceTestEmailEvidence" in management_source
    assert "Email.GetSentEmailsForRecord" in management_source
    assert "Email.GetEmailOutboxForRecord" in management_source
    assert "IsMatchingInternalCanaryMessage" in management_source
    assert "EntitySetName = 'invoiceEmailCanaryEvidenceEntries'" in evidence_page_source
    assert "field(evidence; GetInternalCanaryEmailEvidence())" in evidence_page_source


def test_smtp_preflight_rejects_entra_application_role_claims() -> None:
    source = SMTP_PREFLIGHT.read_text(encoding="utf-8")

    assert 'if token_evidence["roles"]:' in source
    assert "Exchange Application RBAC" in source
    assert "legacy mailbox-permission path" in source
    assert '"sent_email": False' in source
