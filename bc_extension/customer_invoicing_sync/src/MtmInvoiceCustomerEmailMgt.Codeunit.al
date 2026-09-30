codeunit 71013 "MTM Invoice Customer Email Mgt"
{
    var
        ExpectedSenderLbl: Label 'consuelo@mtmlogix.com', Locked = true;
        MexicoSenderLbl: Label 'carlos@mtmlogix.com', Locked = true;
        TestRecipientLbl: Label 'mario@mtmlogix.com', Locked = true;
        LayoutNameLbl: Label 'MTMGTInvoiceStandard202606OnePage', Locked = true;
        LogoUrlLbl: Label 'https://mhth6mu5g8.execute-api.us-east-1.amazonaws.com/assets/mtm-logix-email-logo-porcelain-v1.png', Locked = true;
        ScenarioNotConfiguredErr: Label 'The MTM Invoice Customer Delivery email scenario is not assigned to an email account.';
        WrongSenderErr: Label 'The MTM Invoice Customer Delivery email scenario is assigned to %1. It must be assigned to %2.';

    procedure SendApprovedInvoiceEmail(PostedInvoice: Record "Sales Invoice Header")
    var
        Audit: Record "MTM Invoice Email Audit";
        Customer: Record Customer;
        SenderAccount: Record "Email Account" temporary;
        CustomerInvoicingMgt: Codeunit "MTM Customer Invoicing Mgt";
        AttachmentBlob: Codeunit "Temp Blob";
        XmlAttachmentBlob: Codeunit "Temp Blob";
        Recipient: Text[250];
        CfdiCustomerName: Text[250];
        CorreoFactura: Text[250];
        CopySellToAddressTo: Text[50];
        TaxIdentificationType: Text[50];
        CashFlowPaymentTermsCode: Code[20];
        AttachmentName: Text[250];
        EmailSubject: Text[250];
        EmailBody: Text;
        EvidenceError: Text;
        MessageId: Guid;
    begin
        EnsureSupportedCompany();
        if IsMexicoCompany() then
            ValidateMexicoDeliveryEligibility(PostedInvoice);
        GetOrCreateAudit(Audit, PostedInvoice);
        if Audit.Status = Audit.Status::Sent then
            exit;

        if not IsStamped(PostedInvoice) then
            FailAudit(Audit, StrSubstNo('Invoice %1 cannot be emailed until FEL status is Stamp Received.', PostedInvoice."No."));

        if not IsNullGuid(Audit."BC Email Message Id") then begin
            if ReconcileNativeSentEmail(Audit, PostedInvoice, EvidenceError) then
                exit;
            if EvidenceError <> '' then
                FailAudit(Audit, EvidenceError);
            if IsMexicoCompany() then
                FailAudit(Audit, 'A prior Mexico email message has no verified native Sent Email evidence. Review the exact message before any retry.');
            if Audit."Native Send Accepted" then
                FailAudit(
                    Audit,
                    StrSubstNo(
                        'Business Central accepted email message %1 for invoice %2, but native Sent Email evidence is not available. The invoice will not be emailed again automatically.',
                        Audit."BC Email Message Id",
                        PostedInvoice."No."));
        end;

        if not ResolveRequiredSenderAccount(SenderAccount, EvidenceError) then
            FailAudit(Audit, EvidenceError);

        if not Customer.Get(PostedInvoice."Sell-to Customer No.") then
            FailAudit(Audit, StrSubstNo('Customer %1 was not found.', PostedInvoice."Sell-to Customer No."));

        CustomerInvoicingMgt.LoadCustomFieldValues(
            Customer,
            CfdiCustomerName,
            CorreoFactura,
            CopySellToAddressTo,
            TaxIdentificationType,
            CashFlowPaymentTermsCode);
        Recipient := CopyStr(CorreoFactura, 1, MaxStrLen(Recipient));
        if Recipient = '' then
            Recipient := Customer."E-Mail";
        if (Recipient = '') or (StrPos(Recipient, '@') = 0) then
            FailAudit(
                Audit,
                StrSubstNo(
                    'Invoice %1 has no valid invoice recipient. Update Correo Factura or E-Mail on the BC customer.',
                    PostedInvoice."No."));

        if IsMexicoCompany() then
            Recipient := NormalizeRecipients(Recipient);
        if not TryRenderApprovedInvoicePdf(PostedInvoice, AttachmentBlob) then
            FailAudit(Audit, CopyStr(GetLastErrorText(), 1, MaxStrLen(Audit."Error Text")));
        if IsMexicoCompany() then begin
            if not TryBuildMexicoCertifiedXml(PostedInvoice, XmlAttachmentBlob) then
                FailAudit(Audit, GetLastErrorText());
            if not Audit."Delivery Prepared" then
                FailAudit(Audit, 'Mexico customer delivery requires explicit per-invoice preparation.');
            if not TryVerifyPreparedMexicoIntent(Audit, PostedInvoice, Recipient, AttachmentBlob, XmlAttachmentBlob) then
                FailAudit(Audit, GetLastErrorText());
        end;

        AttachmentName := CopyStr(StrSubstNo('Factura_%1.pdf', PostedInvoice."No."), 1, MaxStrLen(AttachmentName));
        EmailSubject := CopyStr(StrSubstNo('MTM Logix | Factura electronica %1', PostedInvoice."No."), 1, MaxStrLen(EmailSubject));
        EmailBody := BuildCommandEraEmailBody(PostedInvoice, Customer);
        if not TryPrepareInvoiceEmail(
            PostedInvoice,
            AttachmentBlob,
            XmlAttachmentBlob,
            AttachmentName,
            EmailSubject,
            EmailBody,
            Recipient,
            GetDeliveryCc(Audit),
            MessageId)
        then
            FailAudit(Audit, CopyStr(GetLastErrorText(), 1, MaxStrLen(Audit."Error Text")));

        if IsMexicoCompany() then begin
            Audit.LockTable();
            Audit.Get(PostedInvoice.SystemId);
            if not IsNullGuid(Audit."BC Email Message Id") then
                Error('Another send attempt already claimed this Mexico invoice. Review its native email evidence.');
            VerifyPreparedMexicoIntent(Audit, PostedInvoice, Recipient, AttachmentBlob, XmlAttachmentBlob);
        end else
            Audit.Get(PostedInvoice.SystemId);
        Audit."Attempt Count" += 1;
        Audit.Status := Audit.Status::Pending;
        Audit.Recipient := Recipient;
        Audit."Sender Email" := CopyStr(SenderAccount."Email Address", 1, MaxStrLen(Audit."Sender Email"));
        Audit."Sender Account Id" := SenderAccount."Account Id";
        Audit."BC Email Message Id" := MessageId;
        Audit."Native Send Accepted" := false;
        Audit."Native Sent Verified" := false;
        Audit."Last Attempt At" := CurrentDateTime();
        Clear(Audit."Sent At");
        Audit."Error Text" := '';
        Audit."Report Layout Name" := LayoutNameLbl;
        if IsMexicoCompany() then
            Audit."Report Layout Name" := 'MX_PAC_PDF_CFDI_XML_V1';
        Audit.Modify(true);
        Commit();

        if not TrySendInvoiceEmail(MessageId, SenderAccount) then begin
            Audit.Get(PostedInvoice.SystemId);
            Audit."Native Send Accepted" := false;
            EvidenceError := GetSendFailureEvidence(PostedInvoice, MessageId, GetLastErrorText());
            FailAudit(Audit, CopyStr(EvidenceError, 1, MaxStrLen(Audit."Error Text")));
        end;

        Audit.Get(PostedInvoice.SystemId);
        Audit."Native Send Accepted" := true;
        Audit.Modify(true);
        Commit();

        if ReconcileNativeSentEmail(Audit, PostedInvoice, EvidenceError) then
            exit;
        if EvidenceError = '' then
            EvidenceError := StrSubstNo(
                'Business Central accepted email message %1 for invoice %2, but its exact native Sent Email record was not found.',
                MessageId,
                PostedInvoice."No.");
        FailAudit(Audit, EvidenceError);
    end;

    procedure SendApprovedInvoiceTestEmailToMario(PostedInvoice: Record "Sales Invoice Header")
    begin
        if IsMexicoCompany() then
            Error('Mexico canary requires SendApprovedMxInvoiceTestEmailToMario and an independently reviewed PAC PDF hash.');
        SendInternalCanary(PostedInvoice, '');
    end;

    procedure SendApprovedMxInvoiceTestEmailToMario(PostedInvoice: Record "Sales Invoice Header"; ExpectedPdfSha256: Text)
    begin
        if not IsMexicoCompany() then
            Error('The reviewed Mexico canary is scoped to MTM_MX_PROD.');
        SendInternalCanary(PostedInvoice, ExpectedPdfSha256);
    end;

    local procedure SendInternalCanary(PostedInvoice: Record "Sales Invoice Header"; ExpectedPdfSha256: Text)
    var
        Customer: Record Customer;
        SenderAccount: Record "Email Account" temporary;
        AttachmentBlob: Codeunit "Temp Blob";
        XmlAttachmentBlob: Codeunit "Temp Blob";
        AttachmentName: Text[250];
        EmailSubject: Text[250];
        EmailBody: Text;
        EvidenceError: Text;
        MessageId: Guid;
    begin
        if not IsStamped(PostedInvoice) then
            Error('Invoice %1 cannot be used for the email canary until FEL status is Stamp Received.', PostedInvoice."No.");

        if IsMexicoCompany() then begin
            EvidenceError := GetApprovedInvoiceTestEmailEvidence(PostedInvoice);
            if (CopyStr(EvidenceError, 1, 9) <> 'NotFound|') and
               ((StrPos(EvidenceError, 'Outbox|') <> 1) or (StrPos(EvidenceError, '|Status=Failed|') = 0))
            then
                Error('An internal canary already exists or sender configuration is incomplete. Review existing evidence before retry: %1', EvidenceError);
        end;
        if not ResolveRequiredSenderAccount(SenderAccount, EvidenceError) then
            Error(EvidenceError);

        if not Customer.Get(PostedInvoice."Sell-to Customer No.") then
            Error('Customer %1 was not found.', PostedInvoice."Sell-to Customer No.");

        if not TryRenderApprovedInvoicePdf(PostedInvoice, AttachmentBlob) then
            Error(GetLastErrorText());
        if IsMexicoCompany() then begin
            if not TryBuildMexicoCertifiedXml(PostedInvoice, XmlAttachmentBlob) then
                Error(GetLastErrorText());
            RequireReviewedPdfHash(AttachmentBlob, ExpectedPdfSha256);
        end;

        AttachmentName := CopyStr(StrSubstNo('Factura_%1.pdf', PostedInvoice."No."), 1, MaxStrLen(AttachmentName));
        EmailSubject := CopyStr(StrSubstNo('PRUEBA INTERNA | MTM Logix | Factura electronica %1', PostedInvoice."No."), 1, MaxStrLen(EmailSubject));
        EmailBody := BuildCommandEraEmailBody(PostedInvoice, Customer);
        if not TryPrepareInvoiceEmail(
            PostedInvoice,
            AttachmentBlob,
            XmlAttachmentBlob,
            AttachmentName,
            EmailSubject,
            EmailBody,
            TestRecipientLbl,
            '',
            MessageId)
        then
            Error(GetLastErrorText());

        if not TrySendInvoiceEmail(MessageId, SenderAccount) then
            Error(GetLastErrorText());

        if not HasNativeSentEmailEvidence(
            PostedInvoice,
            MessageId,
            SenderAccount."Account Id",
            EvidenceError)
        then begin
            if EvidenceError = '' then
                EvidenceError := StrSubstNo(
                    'Business Central accepted internal canary message %1 for invoice %2, but its exact native Sent Email record was not found.',
                    MessageId,
                    PostedInvoice."No.");
            Error(EvidenceError);
        end;
        if IsMexicoCompany() then
            if not TryVerifyMexicoCanaryMessage(PostedInvoice, MessageId) then
                Error(GetLastErrorText());
    end;

    procedure GetApprovedInvoiceTestEmailEvidence(PostedInvoice: Record "Sales Invoice Header"): Text
    var
        EmailOutbox: Record "Email Outbox" temporary;
        SenderAccount: Record "Email Account" temporary;
        SentEmail: Record "Sent Email" temporary;
        Email: Codeunit Email;
        EvidenceError: Text;
        ExpectedSubject: Text[250];
        MessageId: Guid;
        SenderEvidence: Text;
    begin
        if not ResolveRequiredSenderAccount(SenderAccount, EvidenceError) then
            exit('ConfigurationError|' + EvidenceError);

        SenderEvidence := BuildSenderEvidence(SenderAccount);

        ExpectedSubject := CopyStr(
            StrSubstNo('PRUEBA INTERNA | MTM Logix | Factura electronica %1', PostedInvoice."No."),
            1,
            MaxStrLen(ExpectedSubject));

        Email.GetSentEmailsForRecord(Database::"Sales Invoice Header", PostedInvoice.SystemId, SentEmail);
        if SentEmail.FindSet() then
            repeat
                MessageId := SentEmail.GetMessageId();
                if IsMatchingInternalCanaryMessage(MessageId, ExpectedSubject) then begin
                    if SentEmail.GetAccountId() <> SenderAccount."Account Id" then
                        exit(StrSubstNo('SentWrongAccount|MessageId=%1|%2', MessageId, SenderEvidence));
                    if IsMexicoCompany() then
                        if not TryVerifyMexicoCanaryMessage(PostedInvoice, MessageId) then
                            exit(StrSubstNo('SentWrongIntent|MessageId=%1|Error=%2', MessageId, GetLastErrorText().Replace('|', '/')));
                    if IsMexicoCompany() then
                        exit(StrSubstNo('Sent|MessageId=%1|Recipient=%2|%3', MessageId, TestRecipientLbl, SenderEvidence) +
                            BuildMexicoAttachmentEvidence(PostedInvoice));
                    exit(StrSubstNo('Sent|MessageId=%1|Recipient=%2|%3', MessageId, TestRecipientLbl, SenderEvidence));
                end;
            until SentEmail.Next() = 0;

        Email.GetEmailOutboxForRecord(PostedInvoice, EmailOutbox);
        if EmailOutbox.FindSet() then
            repeat
                MessageId := EmailOutbox.GetMessageId();
                if IsMatchingInternalCanaryMessage(MessageId, ExpectedSubject) then
                    exit(
                        StrSubstNo(
                            'Outbox|MessageId=%1|Status=%2|Recipient=%3',
                            MessageId,
                            Format(Email.GetOutboxEmailRecordStatus(MessageId)),
                            TestRecipientLbl) + '|' + SenderEvidence + '|' + BuildOutboxEvidence(EmailOutbox));
            until EmailOutbox.Next() = 0;

        exit('NotFound|' + SenderEvidence);
    end;

    procedure PrepareInvoiceEmailDelivery(
        PostedInvoice: Record "Sales Invoice Header";
        CcRecipients: Text;
        ExpectedFiscalUuid: Text;
        ExpectedExternalDocumentNumber: Text;
        ExpectedAmountIncludingVat: Decimal;
        ExpectedDueDate: Date;
        ExpectedPdfSha256: Text)
    var
        Audit: Record "MTM Invoice Email Audit";
        PdfBlob: Codeunit "Temp Blob";
        XmlBlob: Codeunit "Temp Blob";
        Recipient: Text[250];
        NormalizedCc: Text[250];
    begin
        if not IsMexicoCompany() then
            Error('Explicit fiscal email preparation is scoped to MTM_MX_PROD.');
        if (ExpectedFiscalUuid = '') or (ExpectedExternalDocumentNumber = '') or
           (ExpectedAmountIncludingVat <= 0) or (ExpectedDueDate = 0D)
        then
            Error('Complete approved fiscal invoice identity is required for Mexico delivery.');
        if StrLen(ExpectedExternalDocumentNumber) > 35 then
            Error('Approved shipment reference exceeds the BC field limit.');
        PostedInvoice.TestField("External Document No.", ExpectedExternalDocumentNumber);
        PostedInvoice.TestField("Due Date", ExpectedDueDate);
        PostedInvoice.CalcFields("Amount Including VAT");
        if PostedInvoice."Amount Including VAT" <> ExpectedAmountIncludingVat then
            Error('The posted invoice total differs from the approved delivery identity.');
        if UpperCase(GetFiscalUuid(PostedInvoice)) <> UpperCase(ExpectedFiscalUuid) then
            Error('The posted invoice UUID differs from the approved delivery identity.');
        if not TryRenderApprovedInvoicePdf(PostedInvoice, PdfBlob) then
            Error(GetLastErrorText());
        if not TryBuildMexicoCertifiedXml(PostedInvoice, XmlBlob) then
            Error(GetLastErrorText());
        RequireReviewedPdfHash(PdfBlob, ExpectedPdfSha256);
        Recipient := ResolveCustomerRecipient(PostedInvoice);
        NormalizedCc := NormalizeRecipients(CcRecipients);
        GetOrCreateAudit(Audit, PostedInvoice);
        Audit.LockTable();
        Audit.Get(PostedInvoice.SystemId);
        // An existing intent is immutable, including before/after an ambiguous send.
        if Audit."Delivery Prepared" then begin
            Audit.TestField("CC Recipients", NormalizedCc);
            VerifyPreparedMexicoIntent(Audit, PostedInvoice, Recipient, PdfBlob, XmlBlob);
            exit;
        end;
        if (Audit."Attempt Count" <> 0) or not IsNullGuid(Audit."BC Email Message Id") then
            Error('An existing send attempt cannot acquire a new delivery intent.');
        Audit.Recipient := Recipient;
        Audit."CC Recipients" := NormalizedCc;
        Audit."Fiscal UUID" := CopyStr(UpperCase(ExpectedFiscalUuid), 1, MaxStrLen(Audit."Fiscal UUID"));
        Audit."PDF Attachment SHA256" := GenerateSha256(PdfBlob);
        Audit."XML Attachment SHA256" := GenerateSha256(XmlBlob);
        Audit."Expected External Document No." := PostedInvoice."External Document No.";
        Audit."Expected Amount Including VAT" := ExpectedAmountIncludingVat;
        Audit."Expected Due Date" := ExpectedDueDate;
        Audit."Delivery Prepared" := true;
        Audit."Prepared At" := CurrentDateTime();
        Audit."Error Text" := '';
        Audit.Modify(true);
    end;

    procedure GetInvoiceEmailReadiness(PostedInvoice: Record "Sales Invoice Header"): Text
    var
        PdfBlob: Codeunit "Temp Blob";
        XmlBlob: Codeunit "Temp Blob";
        ErrorText: Text;
    begin
        if not IsMexicoCompany() then
            exit('Market=GT|Capability=GT_FACTURAGTM_V1|Sender=' + GetExpectedSender());
        if not TryRenderApprovedInvoicePdf(PostedInvoice, PdfBlob) then
            ErrorText := GetLastErrorText()
        else
            if not TryBuildMexicoCertifiedXml(PostedInvoice, XmlBlob) then
                ErrorText := GetLastErrorText();
        if ErrorText <> '' then
            exit('Market=MX|Capability=MX_PAC_PDF_CFDI_XML_V1|AttachmentsValidated=false|Error=' + ErrorText.Replace('|', '/'));
        exit('Market=MX|Capability=MX_PAC_PDF_CFDI_XML_V1|AttachmentsValidated=true|FiscalUuid=' +
            GetFiscalUuid(PostedInvoice) + '|Sender=' + GetExpectedSender());
    end;

    local procedure IsMexicoCompany(): Boolean
    begin
        exit(CompanyName() = 'MTM_MX_PROD');
    end;

    local procedure EnsureSupportedCompany()
    begin
        if not (CompanyName() in ['MTM_GT_PROD', 'MTM_MX_PROD']) then
            Error('Approved invoice delivery is scoped to MTM_GT_PROD and MTM_MX_PROD.');
    end;

    local procedure GetExpectedSender(): Text
    begin
        EnsureSupportedCompany();
        if IsMexicoCompany() then
            exit(MexicoSenderLbl);
        exit(ExpectedSenderLbl);
    end;

    local procedure GetDeliveryCc(Audit: Record "MTM Invoice Email Audit"): Text
    begin
        if IsMexicoCompany() then
            exit(Audit."CC Recipients");
        exit('');
    end;

    local procedure GetSenderDisplayName(): Text
    begin
        if IsMexicoCompany() then
            exit('Carlos | MTM Logix');
        exit('Consuelo Velasquez');
    end;

    local procedure GetFiscalUuid(PostedInvoice: Record "Sales Invoice Header"): Text
    var
        InvoiceRef: RecordRef;
    begin
        InvoiceRef.GetTable(PostedInvoice);
        exit(ReadTextField(InvoiceRef, 'Fiscal Invoice Number PAC'));
    end;

    local procedure ResolveCustomerRecipient(PostedInvoice: Record "Sales Invoice Header"): Text[250]
    var
        Customer: Record Customer;
        CustomerInvoicingMgt: Codeunit "MTM Customer Invoicing Mgt";
        CfdiCustomerName: Text[250];
        CorreoFactura: Text[250];
        CopySellToAddressTo: Text[50];
        TaxIdentificationType: Text[50];
        CashFlowPaymentTermsCode: Code[20];
    begin
        Customer.Get(PostedInvoice."Sell-to Customer No.");
        CustomerInvoicingMgt.LoadCustomFieldValues(Customer, CfdiCustomerName, CorreoFactura,
            CopySellToAddressTo, TaxIdentificationType, CashFlowPaymentTermsCode);
        if CorreoFactura = '' then
            CorreoFactura := Customer."E-Mail";
        if CorreoFactura = '' then
            Error('The BC customer has no invoice email recipient.');
        exit(NormalizeRecipients(CorreoFactura));
    end;

    local procedure NormalizeRecipients(Value: Text): Text[250]
    var
        Address: Text;
        Addresses: List of [Text];
        Result: Text;
    begin
        if Value = '' then
            exit('');
        Addresses := Value.Replace(',', ';').Split(';');
        foreach Address in Addresses do begin
            Address := LowerCase(DelChr(Address, '<>', ' '));
            if (Address = '') or (StrPos(Address, '@') <= 1) or
               (StrPos(Address, ' ') <> 0) or (StrPos(Address, '<') <> 0) or
               (StrPos(Address, '>') <> 0)
            then
                Error('The approved email recipient list contains an invalid address.');
            if Result <> '' then
                Result += ';';
            Result += Address;
        end;
        if StrLen(Result) > 250 then
            Error('The approved email recipient list exceeds the BC audit field limit.');
        exit(CopyStr(Result, 1, 250));
    end;

    [TryFunction]
    local procedure TryVerifyPreparedMexicoIntent(Audit: Record "MTM Invoice Email Audit";
        PostedInvoice: Record "Sales Invoice Header"; Recipient: Text;
        var PdfBlob: Codeunit "Temp Blob"; var XmlBlob: Codeunit "Temp Blob")
    begin
        VerifyPreparedMexicoIntent(Audit, PostedInvoice, Recipient, PdfBlob, XmlBlob);
    end;

    local procedure VerifyPreparedMexicoIntent(Audit: Record "MTM Invoice Email Audit";
        PostedInvoice: Record "Sales Invoice Header"; Recipient: Text;
        var PdfBlob: Codeunit "Temp Blob"; var XmlBlob: Codeunit "Temp Blob")
    begin
        Audit.TestField("Delivery Prepared", true);
        Audit.TestField("Posted Invoice No.", PostedInvoice."No.");
        Audit.TestField("Customer No.", PostedInvoice."Sell-to Customer No.");
        Audit.TestField(Recipient, NormalizeRecipients(Recipient));
        Audit.TestField("Expected External Document No.", PostedInvoice."External Document No.");
        Audit.TestField("Expected Due Date", PostedInvoice."Due Date");
        PostedInvoice.CalcFields("Amount Including VAT");
        Audit.TestField("Expected Amount Including VAT", PostedInvoice."Amount Including VAT");
        Audit.TestField("Fiscal UUID", UpperCase(GetFiscalUuid(PostedInvoice)));
        Audit.TestField("PDF Attachment SHA256", GenerateSha256(PdfBlob));
        Audit.TestField("XML Attachment SHA256", GenerateSha256(XmlBlob));
    end;

    local procedure BuildMexicoPacPdf(PostedInvoice: Record "Sales Invoice Header"; var PdfBlob: Codeunit "Temp Blob")
    var
        EncodedBlob: Codeunit "Temp Blob";
        Base64Convert: Codeunit "Base64 Convert";
        Stream: InStream;
        PdfOutStream: OutStream;
        EncodedPdf: Text;
        Header: Text[5];
    begin
        CopyInvoiceBlob(PostedInvoice, 'PDF_64', EncodedBlob);
        EncodedBlob.CreateInStream(Stream);
        Stream.ReadText(EncodedPdf);
        Clear(PdfBlob);
        PdfBlob.CreateOutStream(PdfOutStream);
        Base64Convert.FromBase64(EncodedPdf, PdfOutStream);
        if not PdfBlob.HasValue() then
            Error('Mexico invoice %1 has no stamped PAC PDF.', PostedInvoice."No.");
        PdfBlob.CreateInStream(Stream);
        if (Stream.ReadText(Header, 5) <> 5) or (Header <> '%PDF-') then
            Error('Mexico invoice %1 PAC PDF has an invalid header.', PostedInvoice."No.");
    end;

    [TryFunction]
    local procedure TryBuildMexicoCertifiedXml(PostedInvoice: Record "Sales Invoice Header"; var XmlBlob: Codeunit "Temp Blob")
    var
        StoredBlob: Codeunit "Temp Blob";
        Stream: InStream;
        XmlOutStream: OutStream;
        Document: XmlDocument;
        Token: JsonToken;
        Raw: Text;
    begin
        CopyInvoiceBlob(PostedInvoice, 'Original Document XML', StoredBlob);
        StoredBlob.CreateInStream(Stream, TextEncoding::Windows);
        Stream.Read(Raw);
        if not XmlDocument.ReadFrom(Raw, Document) then begin
            // Reuse the legacy PAC JSON-string decoding used by MX cancellation.
            if not Token.ReadFrom('"' + Raw + '"') then
                Error('Mexico invoice %1 has unreadable stamped XML.', PostedInvoice."No.");
            Raw := Token.AsValue().AsText();
            if not XmlDocument.ReadFrom(Raw, Document) then
                Error('Mexico invoice %1 has invalid stamped XML.', PostedInvoice."No.");
        end;
        ValidateMexicoXmlIdentity(PostedInvoice, Document);
        Clear(XmlBlob);
        XmlBlob.CreateOutStream(XmlOutStream, TextEncoding::UTF8);
        XmlOutStream.WriteText(Raw);
    end;

    local procedure ValidateMexicoXmlIdentity(PostedInvoice: Record "Sales Invoice Header"; Document: XmlDocument)
    var
        Company: Record "Company Information";
        Customer: Record Customer;
        CompanyRef: RecordRef;
        CustomerRef: RecordRef;
        InvoiceRef: RecordRef;
        GeneralLedgerSetup: Record "General Ledger Setup";
        Ns: XmlNamespaceManager;
        InvoiceUuid: Guid;
        Currency: Text;
        Folio: Text;
        Series: Text;
        FiscalUuid: Text;
        IssuerRfc: Text;
        RecipientRfc: Text;
        TaxTotal: Decimal;
        Node: XmlNode;
    begin
        ValidateMexicoDeliveryEligibility(PostedInvoice);
        PostedInvoice.CalcFields(Cancelled, Amount, "Amount Including VAT");
        PostedInvoice.TestField(Cancelled, false);
        if not IsStamped(PostedInvoice) then
            Error('Mexico invoice %1 is not stamped.', PostedInvoice."No.");
        InvoiceRef.GetTable(PostedInvoice);
        if ReadTextField(InvoiceRef, 'Date/Time Canceled') <> '' then
            Error('Mexico invoice %1 has a fiscal cancellation date.', PostedInvoice."No.");
        FiscalUuid := GetFiscalUuid(PostedInvoice);
        if (FiscalUuid = '') or not Evaluate(InvoiceUuid, FiscalUuid) or IsNullGuid(InvoiceUuid) then
            Error('Mexico invoice %1 has no valid fiscal UUID.', PostedInvoice."No.");
        Company.Get();
        CompanyRef.GetTable(Company);
        IssuerRfc := ReadTextField(CompanyRef, 'RFC Number');
        Customer.Get(PostedInvoice."Bill-to Customer No.");
        CustomerRef.GetTable(Customer);
        RecipientRfc := ReadTextField(CustomerRef, 'RFC No.');
        if (IssuerRfc = '') or (RecipientRfc = '') then
            Error('Mexico invoice fiscal issuer or customer RFC is missing.');
        Currency := PostedInvoice."Currency Code";
        if Currency = '' then begin
            GeneralLedgerSetup.Get();
            Currency := GeneralLedgerSetup."LCY Code";
        end;
        Ns.AddNamespace('c', 'http://www.sat.gob.mx/cfd/4');
        Ns.AddNamespace('t', 'http://www.sat.gob.mx/TimbreFiscalDigital');
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/@Version', '4.0');
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/@TipoDeComprobante', 'I');
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/c:Complemento/t:TimbreFiscalDigital/@UUID', FiscalUuid);
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/c:Emisor/@Rfc', IssuerRfc);
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/c:Receptor/@Rfc', RecipientRfc);
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/@Moneda', Currency);
        Folio := GetXmlAttribute(Document, Ns, '/c:Comprobante/@Folio');
        Series := GetXmlAttribute(Document, Ns, '/c:Comprobante/@Serie');
        if UpperCase(Series + Folio) <> UpperCase(PostedInvoice."No.") then
            Error('Stamped XML invoice number differs from the posted invoice.');
        RequireXmlAmount(Document, Ns, '/c:Comprobante/@SubTotal', PostedInvoice.Amount);
        RequireXmlAmount(Document, Ns, '/c:Comprobante/@Total', PostedInvoice."Amount Including VAT");
        TaxTotal := PostedInvoice."Amount Including VAT" - PostedInvoice.Amount;
        if Document.SelectSingleNode('/c:Comprobante/c:Impuestos/@TotalImpuestosTrasladados', Ns, Node) then
            RequireXmlAmount(Document, Ns, '/c:Comprobante/c:Impuestos/@TotalImpuestosTrasladados', TaxTotal)
        else
            if TaxTotal <> 0 then
                Error('Stamped XML VAT total is missing.');
    end;

    local procedure ValidateMexicoDeliveryEligibility(PostedInvoice: Record "Sales Invoice Header")
    var
        Line: Record "Sales Invoice Line";
        HasOceanFreight: Boolean;
    begin
        if not IsMexicoCompany() then
            Error('Mexico invoice delivery is scoped to MTM_MX_PROD.');
        PostedInvoice.TestField("Sell-to Customer No.", 'C00067');
        PostedInvoice.TestField("Bill-to Customer No.", 'C00067');
        PostedInvoice.TestField("Currency Code", 'USD');
        PostedInvoice.TestField("External Document No.");
        Line.SetRange("Document No.", PostedInvoice."No.");
        if Line.FindSet() then
            repeat
                if Line.Type <> Line.Type::" " then begin
                    Line.TestField(Type, Line.Type::Item);
                    if not (Line."No." in ['INT000000026', 'INT000000011', 'INT000000017',
                        'INT000000028', 'INT000000022', 'INT000000031', 'INT000000007', 'INT000000016',
                        'NAT00000037', 'NAT00000009', 'NAT00000010', 'NAT00000015'])
                    then
                        Error('Item %1 is outside the approved Mexico USD Ocean delivery path.', Line."No.");
                    if Line."No." = 'INT000000026' then
                        HasOceanFreight := true;
                    if CopyStr(Line."No.", 1, 3) = 'INT' then
                        Line.TestField("VAT %", 0)
                    else
                        Line.TestField("VAT %", 16);
                end;
            until Line.Next() = 0;
        if not HasOceanFreight then
            Error('Mexico invoice delivery requires the approved Ocean Freight line.');
    end;

    local procedure RequireReviewedPdfHash(var PdfBlob: Codeunit "Temp Blob"; ExpectedPdfSha256: Text)
    begin
        ExpectedPdfSha256 := LowerCase(ExpectedPdfSha256);
        if (StrLen(ExpectedPdfSha256) <> 64) or
           (DelChr(ExpectedPdfSha256, '=', '0123456789abcdef') <> '')
        then
            Error('An independently reviewed 64-character PAC PDF SHA-256 hash is required.');
        if GenerateSha256(PdfBlob) <> ExpectedPdfSha256 then
            Error('The stored PAC PDF differs from the independently reviewed invoice PDF.');
    end;

    local procedure RequireXmlAttribute(Document: XmlDocument; Ns: XmlNamespaceManager; Path: Text; Expected: Text)
    begin
        if UpperCase(GetXmlAttribute(Document, Ns, Path)) <> UpperCase(Expected) then
            Error('Stamped XML differs from the posted fiscal identity: %1.', Path);
    end;

    local procedure GetXmlAttribute(Document: XmlDocument; Ns: XmlNamespaceManager; Path: Text): Text
    var
        Node: XmlNode;
    begin
        if not Document.SelectSingleNode(Path, Ns, Node) then
            Error('Required stamped XML identity is missing: %1.', Path);
        exit(Node.AsXmlAttribute().Value());
    end;

    local procedure RequireXmlAmount(Document: XmlDocument; Ns: XmlNamespaceManager; Path: Text; Expected: Decimal)
    var
        Actual: Decimal;
    begin
        if not Evaluate(Actual, GetXmlAttribute(Document, Ns, Path), 9) or (Actual <> Expected) then
            Error('Stamped XML amount differs from the posted invoice: %1.', Path);
    end;

    local procedure CopyInvoiceBlob(PostedInvoice: Record "Sales Invoice Header"; FieldName: Text; var Blob: Codeunit "Temp Blob")
    var
        SourceRef: RecordRef;
        SourceField: FieldRef;
        Index: Integer;
    begin
        SourceRef.GetTable(PostedInvoice);
        for Index := 1 to SourceRef.FieldCount() do begin
            SourceField := SourceRef.FieldIndex(Index);
            if SourceField.Name() = FieldName then begin
                SourceField.CalcField();
                Clear(Blob);
                Blob.FromFieldRef(SourceField);
                if not Blob.HasValue() then
                    Error('Mexico invoice %1 has no %2 attachment.', PostedInvoice."No.", FieldName);
                exit;
            end;
        end;
        Error('Required Mexico attachment field %1 is unavailable.', FieldName);
    end;

    local procedure GenerateSha256(var Blob: Codeunit "Temp Blob"): Text[64]
    var
        CryptographyManagement: Codeunit "Cryptography Management";
        Stream: InStream;
        HexHash: Text;
        HashAlgorithmType: Option MD5,SHA1,SHA256,SHA384,SHA512;
    begin
        Blob.CreateInStream(Stream);
        // The stream overload returns hexadecimal (ConvertByteHashToString),
        // despite the Base64 wording in its public method documentation.
        HexHash := LowerCase(CryptographyManagement.GenerateHash(Stream, HashAlgorithmType::SHA256));
        if (StrLen(HexHash) <> 64) or (DelChr(HexHash, '=', '0123456789abcdef') <> '') then
            Error('BC returned an invalid SHA-256 digest.');
        exit(CopyStr(HexHash, 1, 64));
    end;

    local procedure BuildMexicoAttachmentEvidence(PostedInvoice: Record "Sales Invoice Header"): Text
    var
        PdfBlob: Codeunit "Temp Blob";
        XmlBlob: Codeunit "Temp Blob";
    begin
        BuildMexicoPacPdf(PostedInvoice, PdfBlob);
        if not TryBuildMexicoCertifiedXml(PostedInvoice, XmlBlob) then
            Error(GetLastErrorText());
        exit('|FiscalUuid=' + GetFiscalUuid(PostedInvoice) + '|PdfAttachmentSha256=' + GenerateSha256(PdfBlob) +
            '|XmlAttachmentSha256=' + GenerateSha256(XmlBlob) + '|AttachmentCount=2');
    end;

    [TryFunction]
    local procedure TryVerifyMexicoCanaryMessage(PostedInvoice: Record "Sales Invoice Header"; MessageId: Guid)
    var
        CanaryIntent: Record "MTM Invoice Email Audit" temporary;
        PdfBlob: Codeunit "Temp Blob";
        XmlBlob: Codeunit "Temp Blob";
    begin
        BuildMexicoPacPdf(PostedInvoice, PdfBlob);
        if not TryBuildMexicoCertifiedXml(PostedInvoice, XmlBlob) then
            Error(GetLastErrorText());
        CanaryIntent."BC Email Message Id" := MessageId;
        CanaryIntent.Recipient := TestRecipientLbl;
        CanaryIntent."PDF Attachment SHA256" := GenerateSha256(PdfBlob);
        CanaryIntent."XML Attachment SHA256" := GenerateSha256(XmlBlob);
        if not TryVerifyNativeMexicoMessage(CanaryIntent, PostedInvoice) then
            Error(GetLastErrorText());
    end;

    [TryFunction]
    local procedure TryVerifyNativeMexicoMessage(Audit: Record "MTM Invoice Email Audit"; PostedInvoice: Record "Sales Invoice Header")
    var
        EmailMessage: Codeunit "Email Message";
        Blob: Codeunit "Temp Blob";
        Stream: InStream;
        BlobOutStream: OutStream;
        AttachmentCount: Integer;
        PdfFound: Boolean;
        XmlFound: Boolean;
    begin
        if not EmailMessage.Get(Audit."BC Email Message Id") then
            Error('The native Mexico email message could not be loaded.');
        RequireMessageRecipients(EmailMessage, Enum::"Email Recipient Type"::"To", Audit.Recipient);
        RequireMessageRecipients(EmailMessage, Enum::"Email Recipient Type"::Cc, Audit."CC Recipients");
        RequireMessageRecipients(EmailMessage, Enum::"Email Recipient Type"::Bcc, '');
        if EmailMessage.Attachments_First() then
            repeat
                AttachmentCount += 1;
                EmailMessage.Attachments_GetContent(Stream);
                Clear(Blob);
                Blob.CreateOutStream(BlobOutStream);
                CopyStream(BlobOutStream, Stream);
                case EmailMessage.Attachments_GetName() of
                    StrSubstNo('Factura_%1.pdf', PostedInvoice."No."):
                        begin
                            if EmailMessage.Attachments_GetContentType() <> 'application/pdf' then
                                Error('The native Mexico PDF MIME type differs.');
                            Audit.TestField("PDF Attachment SHA256", GenerateSha256(Blob));
                            PdfFound := true;
                        end;
                    StrSubstNo('Factura_%1.xml', PostedInvoice."No."):
                        begin
                            if EmailMessage.Attachments_GetContentType() <> 'application/xml' then
                                Error('The native Mexico XML MIME type differs.');
                            Audit.TestField("XML Attachment SHA256", GenerateSha256(Blob));
                            XmlFound := true;
                        end;
                end;
            until EmailMessage.Attachments_Next() = 0;
        if (AttachmentCount <> 2) or not PdfFound or not XmlFound then
            Error('The native Mexico email must contain exactly the approved PAC PDF and CFDI XML.');
    end;

    local procedure RequireMessageRecipients(var EmailMessage: Codeunit "Email Message"; RecipientType: Enum "Email Recipient Type"; Expected: Text)
    var
        ActualRecipients: List of [Text];
        ExpectedRecipients: List of [Text];
        Address: Text;
        Index: Integer;
    begin
        EmailMessage.GetRecipients(RecipientType, ActualRecipients);
        if Expected <> '' then
            ExpectedRecipients := NormalizeRecipients(Expected).Split(';');
        if ActualRecipients.Count() <> ExpectedRecipients.Count() then
            Error('Native email recipients differ from the approved delivery intent.');
        for Index := 1 to ActualRecipients.Count() do begin
            Address := LowerCase(ActualRecipients.Get(Index));
            if not ExpectedRecipients.Contains(Address) then
                Error('Native email recipients differ from the approved delivery intent.');
        end;
    end;

    local procedure BuildSenderEvidence(SenderAccount: Record "Email Account" temporary): Text
    begin
        exit(
            StrSubstNo(
                'Sender=%1|AccountId=%2|Connector=%3|AccountName=%4',
                SenderAccount."Email Address",
                SenderAccount."Account Id",
                Format(SenderAccount.Connector),
                SenderAccount.Name));
    end;

    local procedure BuildOutboxEvidence(EmailOutbox: Record "Email Outbox" temporary): Text
    var
        OutboxRef: RecordRef;
        ErrorMessage: Text;
        SendFrom: Text;
        DateFailed: Text;
    begin
        OutboxRef.GetTable(EmailOutbox);
        ErrorMessage := ReadTextField(OutboxRef, 'Error Message');
        ErrorMessage := ErrorMessage.Replace('|', '/').Replace('\r', ' ').Replace('\n', ' ');
        SendFrom := ReadTextField(OutboxRef, 'Send From');
        DateFailed := ReadTextField(OutboxRef, 'Date Failed');
        exit(StrSubstNo('SendFrom=%1|DateFailed=%2|ProviderError=%3', SendFrom, DateFailed, ErrorMessage));
    end;

    local procedure GetSendFailureEvidence(
        PostedInvoice: Record "Sales Invoice Header";
        MessageId: Guid;
        SendError: Text): Text
    var
        EmailOutbox: Record "Email Outbox" temporary;
        Email: Codeunit Email;
    begin
        Email.GetEmailOutboxForRecord(PostedInvoice, EmailOutbox);
        if EmailOutbox.FindSet() then
            repeat
                if EmailOutbox.GetMessageId() = MessageId then
                    exit(SendError + ' | ' + BuildOutboxEvidence(EmailOutbox));
            until EmailOutbox.Next() = 0;

        exit(SendError);
    end;

    local procedure IsStamped(PostedInvoice: Record "Sales Invoice Header"): Boolean
    var
        InvoiceRef: RecordRef;
        ElectronicStatus: Text;
    begin
        InvoiceRef.GetTable(PostedInvoice);
        ElectronicStatus := ReadTextField(InvoiceRef, 'Electronic Document Status');
        exit(UpperCase(ElectronicStatus) = 'STAMP RECEIVED');
    end;

    local procedure GetOrCreateAudit(var Audit: Record "MTM Invoice Email Audit"; PostedInvoice: Record "Sales Invoice Header")
    begin
        if Audit.Get(PostedInvoice.SystemId) then
            exit;

        Audit.Init();
        Audit."Posted Invoice System Id" := PostedInvoice.SystemId;
        Audit."Posted Invoice No." := PostedInvoice."No.";
        Audit."Customer No." := PostedInvoice."Sell-to Customer No.";
        Audit.Status := Audit.Status::Pending;
        Audit.Insert(true);
        Commit();
    end;

    local procedure ResolveRequiredSenderAccount(var SenderAccount: Record "Email Account" temporary; var ErrorText: Text): Boolean
    var
        EmailScenario: Codeunit "Email Scenario";
    begin
        ErrorText := '';
        if not EmailScenario.IsThereEmailAccountSetForScenario(Enum::"Email Scenario"::"MTM Invoice Customer Delivery") then begin
            ErrorText := ScenarioNotConfiguredErr;
            exit(false);
        end;
        if not EmailScenario.GetEmailAccount(Enum::"Email Scenario"::"MTM Invoice Customer Delivery", SenderAccount) then begin
            ErrorText := ScenarioNotConfiguredErr;
            exit(false);
        end;
        if LowerCase(SenderAccount."Email Address") <> LowerCase(GetExpectedSender()) then begin
            ErrorText := StrSubstNo(WrongSenderErr, SenderAccount."Email Address", GetExpectedSender());
            exit(false);
        end;
        exit(true);
    end;

    local procedure ReconcileNativeSentEmail(
        var Audit: Record "MTM Invoice Email Audit";
        PostedInvoice: Record "Sales Invoice Header";
        var EvidenceError: Text): Boolean
    begin
        if not HasNativeSentEmailEvidence(
            PostedInvoice,
            Audit."BC Email Message Id",
            Audit."Sender Account Id",
            EvidenceError)
        then
            exit(false);

        Audit.Get(PostedInvoice.SystemId);
        if IsMexicoCompany() then
            if not TryVerifyNativeMexicoMessage(Audit, PostedInvoice) then begin
                EvidenceError := GetLastErrorText();
                exit(false);
            end;
        Audit.Status := Audit.Status::Sent;
        Audit."Native Send Accepted" := true;
        Audit."Native Sent Verified" := true;
        Audit."Sent At" := CurrentDateTime();
        Audit."Error Text" := '';
        Audit.Modify(true);
        Commit();
        exit(true);
    end;

    local procedure HasNativeSentEmailEvidence(
        PostedInvoice: Record "Sales Invoice Header";
        MessageId: Guid;
        SenderAccountId: Guid;
        var EvidenceError: Text): Boolean
    var
        SentEmail: Record "Sent Email" temporary;
        Email: Codeunit Email;
    begin
        EvidenceError := '';
        if IsNullGuid(MessageId) then
            exit(false);

        Email.GetSentEmailsForRecord(Database::"Sales Invoice Header", PostedInvoice.SystemId, SentEmail);
        if not SentEmail.FindSet() then
            exit(false);

        repeat
            if SentEmail.GetMessageId() = MessageId then begin
                if SentEmail.GetAccountId() <> SenderAccountId then begin
                    EvidenceError := StrSubstNo(
                        'Native Sent Email evidence for message %1 used an unexpected Business Central sender account.',
                        MessageId);
                    exit(false);
                end;
                exit(true);
            end;
        until SentEmail.Next() = 0;

        exit(false);
    end;

    local procedure IsMatchingInternalCanaryMessage(MessageId: Guid; ExpectedSubject: Text): Boolean
    var
        EmailMessage: Codeunit "Email Message";
        Recipient: Text;
        ToRecipients: List of [Text];
    begin
        if not EmailMessage.Get(MessageId) then
            exit(false);
        if EmailMessage.GetSubject() <> ExpectedSubject then
            exit(false);

        EmailMessage.GetRecipients(Enum::"Email Recipient Type"::"To", ToRecipients);
        foreach Recipient in ToRecipients do
            if LowerCase(Recipient) = LowerCase(TestRecipientLbl) then
                exit(true);

        exit(false);
    end;

    local procedure FailAudit(var Audit: Record "MTM Invoice Email Audit"; ErrorText: Text)
    begin
        Audit.Get(Audit."Posted Invoice System Id");
        Audit.Status := Audit.Status::Failed;
        Audit."Error Text" := CopyStr(ErrorText, 1, MaxStrLen(Audit."Error Text"));
        Audit.Modify(true);
        Commit();
        Error(ErrorText);
    end;

    [TryFunction]
    local procedure TryRenderApprovedInvoicePdf(PostedInvoice: Record "Sales Invoice Header"; var AttachmentBlob: Codeunit "Temp Blob")
    var
        AttachmentOutStream: OutStream;
        InvoiceRef: RecordRef;
    begin
        if IsMexicoCompany() then begin
            BuildMexicoPacPdf(PostedInvoice, AttachmentBlob);
            exit;
        end;
        PostedInvoice.SetRecFilter();
        InvoiceRef.GetTable(PostedInvoice);
        AttachmentBlob.CreateOutStream(AttachmentOutStream);
        Report.SaveAs(Report::FacturaGTM, '', ReportFormat::Pdf, AttachmentOutStream, InvoiceRef);
    end;

    [TryFunction]
    local procedure TryPrepareInvoiceEmail(
        PostedInvoice: Record "Sales Invoice Header";
        var AttachmentBlob: Codeunit "Temp Blob";
        var XmlAttachmentBlob: Codeunit "Temp Blob";
        AttachmentName: Text;
        EmailSubject: Text;
        EmailBody: Text;
        Recipient: Text;
        CcRecipients: Text;
        var MessageId: Guid)
    var
        EmailMessage: Codeunit "Email Message";
        Email: Codeunit Email;
        AttachmentInStream: InStream;
    begin
        EmailMessage.Create(Recipient, EmailSubject, EmailBody, true);
        if CcRecipients <> '' then
            EmailMessage.SetRecipients(Enum::"Email Recipient Type"::Cc, CcRecipients);
        AttachmentBlob.CreateInStream(AttachmentInStream);
        EmailMessage.AddAttachment(AttachmentName, 'application/pdf', AttachmentInStream);
        if IsMexicoCompany() then begin
            XmlAttachmentBlob.CreateInStream(AttachmentInStream);
            EmailMessage.AddAttachment(StrSubstNo('Factura_%1.xml', PostedInvoice."No."), 'application/xml', AttachmentInStream);
        end;
        Email.AddRelation(
            EmailMessage,
            Database::"Sales Invoice Header",
            PostedInvoice.SystemId,
            Enum::"Email Relation Type"::"Primary Source",
            Enum::"Email Relation Origin"::"Compose Context");
        MessageId := EmailMessage.GetId();
    end;

    [TryFunction]
    local procedure TrySendInvoiceEmail(MessageId: Guid; var SenderAccount: Record "Email Account" temporary)
    var
        EmailMessage: Codeunit "Email Message";
        Email: Codeunit Email;
    begin
        if not EmailMessage.Get(MessageId) then
            Error('Business Central email message %1 could not be loaded.', MessageId);
        if not Email.Send(EmailMessage, SenderAccount) then
            Error('Business Central could not send the invoice through the configured email account.');
    end;

    local procedure BuildCommandEraEmailBody(PostedInvoice: Record "Sales Invoice Header"; Customer: Record Customer): Text
    begin
        exit(
            '<!doctype html><html><body style="margin:0;background:#F7F5EF;font-family:Noto Sans,Aptos,Arial,sans-serif;color:#050B2E;">' +
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px;">' +
            '<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;background:#FFFFFF;border:1px solid #D9D5CA;">' +
            '<tr><td style="background:#F7F5EF;padding:20px 32px;">' +
            '<img src="' + LogoUrlLbl + '" width="238" height="58" alt="MTM Logix" style="display:block;width:238px;max-width:100%;height:auto;border:0;outline:none;text-decoration:none;"></td></tr>' +
            '<tr><td style="background:#050B2E;padding:22px 32px;border-bottom:5px solid #C9A24A;">' +
            '<div style="color:#FFFFFF;font-size:24px;font-weight:700;">FACTURA ELECTRONICA</div></td></tr>' +
            '<tr><td style="padding:32px;font-size:15px;line-height:1.55;">' +
            '<p style="margin:0 0 16px;">Estimado/a ' + EscapeHtml(Customer.Name) + ',</p>' +
            '<p style="margin:0 0 16px;">Adjuntamos la factura electronica <strong>' + EscapeHtml(PostedInvoice."No.") +
            '</strong>. El PDF adjunto contiene el detalle y las referencias operativas del embarque.</p>' +
            GetMexicoInvoiceEmailDetails(PostedInvoice) +
            '<p style="margin:24px 0 0;">Atentamente,<br><strong>' + GetSenderDisplayName() + '</strong><br>MTM Logix<br>' + GetExpectedSender() + '</p>' +
            '</td></tr><tr><td style="padding:18px 32px;background:#F7F5EF;border-top:1px solid #D9D5CA;color:#5E6474;font-size:12px;">' +
            'Beyond Visibility. Into Command.</td></tr></table></td></tr></table></body></html>');
    end;

    local procedure GetMexicoInvoiceEmailDetails(PostedInvoice: Record "Sales Invoice Header"): Text
    begin
        if not IsMexicoCompany() then
            exit('');
        exit('<p style="margin:0 0 16px;">Referencia: <strong>' + EscapeHtml(PostedInvoice."External Document No.") +
            '</strong><br>Vencimiento: <strong>' + Format(PostedInvoice."Due Date", 0, '<Day,2>/<Month,2>/<Year4>') +
            '</strong>. Se incluye el XML CFDI certificado.</p>');
    end;

    local procedure EscapeHtml(Value: Text): Text
    begin
        Value := Value.Replace('&', '&amp;');
        Value := Value.Replace('<', '&lt;');
        Value := Value.Replace('>', '&gt;');
        Value := Value.Replace('"', '&quot;');
        exit(Value);
    end;

    local procedure ReadTextField(var SourceRef: RecordRef; FieldName: Text): Text
    var
        FieldRef: FieldRef;
        FieldIndex: Integer;
    begin
        for FieldIndex := 1 to SourceRef.FieldCount() do begin
            FieldRef := SourceRef.FieldIndex(FieldIndex);
            if FieldRef.Name() = FieldName then
                exit(Format(FieldRef.Value()));
        end;
        exit('');
    end;
}
