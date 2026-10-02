page 71007 "MTM Posted Inv FEL Desc API"
{
    PageType = API;
    APIPublisher = 'mtmlogix';
    APIGroup = 'invoiceSync';
    APIVersion = 'v1.0';
    EntityName = 'postedInvoiceFelDescription';
    EntitySetName = 'postedInvoiceFelDescriptions';
    SourceTable = "Sales Invoice Header";
    ODataKeyFields = SystemId;
    DelayedInsert = false;
    Extensible = false;
    InsertAllowed = false;
    ModifyAllowed = false;
    DeleteAllowed = false;
    Permissions = tabledata "Sales Invoice Header"=rm,
        tabledata "Sales Invoice Line"=rm;

    layout
    {
        area(Content)
        {
            repeater(General)
            {
                field(id; Rec.SystemId)
                {
                    Caption = 'Id';
                    Editable = false;
                }
                field(number; Rec."No.")
                {
                    Caption = 'Number';
                    Editable = false;
                }
                field(customerNumber; Rec."Sell-to Customer No.")
                {
                    Caption = 'Customer Number';
                    Editable = false;
                }
                field(externalDocumentNumber; Rec."External Document No.")
                {
                    Caption = 'External Document Number';
                    Editable = false;
                }
                field(paymentTermsCode; Rec."Payment Terms Code")
                {
                    Caption = 'Payment Terms Code';
                    Editable = false;
                }
                field(paymentMethodCode; Rec."Payment Method Code")
                {
                    Caption = 'Payment Method Code';
                    Editable = false;
                }
                field(cfdiRelation; GetDynamicFieldText('CFDI Relation'))
                {
                    Caption = 'CFDI Relation';
                    Editable = false;
                }
                field(systemModifiedAt; Rec.SystemModifiedAt)
                {
                    Caption = 'System Modified At';
                    Editable = false;
                }
                field(electronicDocumentStatus; GetDynamicFieldText('Electronic Document Status'))
                {
                    Caption = 'Electronic Document Status';
                    Editable = false;
                }
                field(fiscalInvoiceNumberPac; GetDynamicFieldText('Fiscal Invoice Number PAC'))
                {
                    Caption = 'Fiscal Invoice Number PAC';
                    Editable = false;
                }
                field(fiscalSeries; Rec.serie)
                {
                    Caption = 'Fiscal Series';
                    Editable = false;
                }
                field(fiscalNumber; Rec.numero)
                {
                    Caption = 'Fiscal Number';
                    Editable = false;
                }
                field(dateTimeStamped; GetDynamicFieldText('Date/Time Stamped'))
                {
                    Caption = 'Date/Time Stamped';
                    Editable = false;
                }
                field(dateTimeCanceled; GetDynamicFieldText('Date/Time Canceled'))
                {
                    Caption = 'Date/Time Canceled';
                    Editable = false;
                }
                field(errorDescription; GetDynamicFieldText('Error Description'))
                {
                    Caption = 'Error Description';
                    Editable = false;
                }
                field(errorCode; GetDynamicFieldText('Error Code'))
                {
                    Caption = 'Error Code';
                    Editable = false;
                }
                field(mxStampReadiness; BuildMxStampReadinessSummary())
                {
                    Caption = 'MX Stamp Readiness';
                    Editable = false;
                }
                field(cancelled; Rec.Cancelled)
                {
                    Caption = 'Cancelled';
                    Editable = false;
                }
                field(cfdiCancellationId; GetDynamicFieldText('CFDI Cancellation ID'))
                {
                    Caption = 'CFDI Cancellation ID';
                    Editable = false;
                }
                field(cfdiCancellationReasonCode; GetDynamicFieldText('CFDI Cancellation Reason Code'))
                {
                    Caption = 'CFDI Cancellation Reason Code';
                    Editable = false;
                }
                field(invoiceEmailReadiness; GetInvoiceEmailReadiness())
                {
                    Caption = 'Invoice Email Readiness';
                    Editable = false;
                }
                field(cancelGtUuid; Rec.CancelaGTUUID)
                {
                    Caption = 'Cancel GT UUID';
                    Editable = false;
                }
            }
        }
    }
    [ServiceEnabled]
    procedure SyncFelLineDescriptions(var ActionContext: WebServiceActionContext)
    var
        CustomerInvoicingMgt: Codeunit "MTM Customer Invoicing Mgt";
    begin
        CustomerInvoicingMgt.SyncPostedInvoiceLineDescriptions(Rec."No.");
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure StampFelInvoice(var ActionContext: WebServiceActionContext)
    var
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        GTFelMgt.StampPostedInvoiceNoEmail(Rec);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure SendFelInvoice(var ActionContext: WebServiceActionContext)
    begin
        Error('LEGACY FEL CUSTOMER SEND IS DISABLED. USE STAMPFELINVOICE FOR SAT/FEL STAMPING, THEN DELIVER THE BUSINESS CENTRAL SALESINVOICES PDFDOCUMENT ATTACHMENT.');
    end;
    [ServiceEnabled]
    procedure PrepareInvoiceEmailDelivery(ccRecipients: Text; expectedFiscalUuid: Text; expectedExternalDocumentNumber: Text; expectedAmountIncludingVat: Decimal; expectedDueDate: Date; expectedPdfSha256: Text; var ActionContext: WebServiceActionContext)
    var
        InvoiceCustomerEmailMgt: Codeunit "MTM Invoice Customer Email Mgt";
    begin
        InvoiceCustomerEmailMgt.PrepareInvoiceEmailDelivery(Rec, ccRecipients, expectedFiscalUuid,
            expectedExternalDocumentNumber, expectedAmountIncludingVat, expectedDueDate, expectedPdfSha256);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure SendApprovedInvoiceEmail(var ActionContext: WebServiceActionContext)
    var
        InvoiceCustomerEmailMgt: Codeunit "MTM Invoice Customer Email Mgt";
    begin
        InvoiceCustomerEmailMgt.SendApprovedInvoiceEmail(Rec);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure SendApprovedInvoiceTestEmailToMario(var ActionContext: WebServiceActionContext)
    var
        InvoiceCustomerEmailMgt: Codeunit "MTM Invoice Customer Email Mgt";
    begin
        InvoiceCustomerEmailMgt.SendApprovedInvoiceTestEmailToMario(Rec);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure SendApprovedMxInvoiceTestEmailToMario(expectedPdfSha256: Text; var ActionContext: WebServiceActionContext)
    var
        InvoiceCustomerEmailMgt: Codeunit "MTM Invoice Customer Email Mgt";
    begin
        InvoiceCustomerEmailMgt.SendApprovedMxInvoiceTestEmailToMario(Rec, expectedPdfSha256);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure ProcessFelInvoiceResponse(var ActionContext: WebServiceActionContext)
    var
        GTMLeerDocumentos: Codeunit GTMLeerDocumentos;
    begin
        GTMLeerDocumentos.ExtractXMLData('Factura');
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CancelFelInvoice(var ActionContext: WebServiceActionContext)
    var
        FunFacturaGT: Codeunit "Fun. Factura GT";
    begin
        FunFacturaGT.CancelaFacturaGT(Rec);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CancelFelInvoiceWithReason(cancellationReasonId: Text; var ActionContext: WebServiceActionContext)
    var
        FunFacturaGT: Codeunit "Fun. Factura GT";
    begin
        SetDynamicFieldText('CFDI Cancellation ID', cancellationReasonId);
        FunFacturaGT.CancelaFacturaGT(Rec);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CancelFelInvoiceWithMotive(motiveText: Text; var ActionContext: WebServiceActionContext)
    var
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        GTFelMgt.CancelPostedInvoiceWithMotive(Rec, motiveText);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CancelFelInvoiceWithMotiveAndIssueDateTime(motiveText: Text; issueDateTimeText: Text; var ActionContext: WebServiceActionContext)
    var
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        GTFelMgt.CancelPostedInvoiceWithMotiveAndIssueDateTime(Rec, motiveText, issueDateTimeText);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CancelPostedInvoiceAndFelWithMotive(motiveText: Text; var ActionContext: WebServiceActionContext)
    var
        CorrectPostedSalesInvoice: Codeunit "Correct Posted Sales Invoice";
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        if not Rec.Cancelled then begin
            CorrectPostedSalesInvoice.CancelPostedInvoice(Rec);
            Rec.Get(Rec."No.");
        end;
        GTFelMgt.CancelPostedInvoiceWithMotive(Rec, motiveText);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CancelPostedInvoiceAndFelWithMotiveAndIssueDateTime(motiveText: Text; issueDateTimeText: Text; var ActionContext: WebServiceActionContext)
    var
        CorrectPostedSalesInvoice: Codeunit "Correct Posted Sales Invoice";
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        if not Rec.Cancelled then begin
            CorrectPostedSalesInvoice.CancelPostedInvoice(Rec);
            Rec.Get(Rec."No.");
        end;
        GTFelMgt.CancelPostedInvoiceWithMotiveAndIssueDateTime(Rec, motiveText, issueDateTimeText);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CancelPostedInvoiceInBcAfterFelCancellation(expectedInvoiceNumber: Text; expectedExternalDocumentNumber: Text; expectedCustomerNumber: Text; expectedCurrencyCode: Text; expectedAmountIncludingVat: Decimal; expectedFiscalUuid: Text; expectedCancellationUuid: Text; var ActionContext: WebServiceActionContext)
    var
        CorrectPostedSalesInvoice: Codeunit "Correct Posted Sales Invoice";
        CurrentFiscalUuid: Text;
        ElectronicStatus: Text;
    begin
        CurrentFiscalUuid:=GetDynamicFieldText('Fiscal Invoice Number PAC');
        ElectronicStatus:=GetDynamicFieldText('Electronic Document Status');
        Rec.CalcFields("Amount Including VAT");
        if(expectedInvoiceNumber = '') or (Rec."No." <> expectedInvoiceNumber)then Error('Invoice number does not match the expected invoice.');
        if(expectedExternalDocumentNumber = '') or (Rec."External Document No." <> expectedExternalDocumentNumber)then Error('Invoice %1 external document number does not match the expected value.', Rec."No.");
        if(expectedCustomerNumber = '') or (Rec."Sell-to Customer No." <> expectedCustomerNumber)then Error('Invoice %1 customer number does not match the expected value.', Rec."No.");
        if Rec."Currency Code" <> expectedCurrencyCode then Error('Invoice %1 currency code does not match the expected value.', Rec."No.");
        if Rec."Amount Including VAT" <> expectedAmountIncludingVat then Error('Invoice %1 total does not match the expected value.', Rec."No.");
        if UpperCase(ElectronicStatus) <> 'CANCELED' then Error('Invoice %1 FEL status is %2, not Canceled.', Rec."No.", ElectronicStatus);
        if(expectedFiscalUuid = '') or (UpperCase(CurrentFiscalUuid) <> UpperCase(expectedFiscalUuid))then Error('Invoice %1 fiscal UUID does not match the expected UUID.', Rec."No.");
        if(expectedCancellationUuid = '') or (UpperCase(Rec.CancelaGTUUID) <> UpperCase(expectedCancellationUuid))then Error('Invoice %1 cancellation UUID does not match the expected UUID.', Rec."No.");
        if not Rec.Cancelled then begin
            CorrectPostedSalesInvoice.CancelPostedInvoice(Rec);
            Rec.Get(Rec."No.");
        end;
        if not Rec.Cancelled then Error('Invoice %1 was not canceled in Business Central.', Rec."No.");
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure RepairGtCanceledInvoiceFiscalIdentity(expectedInvoiceNumber: Text; expectedFiscalUuid: Text; expectedCancelUuid: Text; expectedCurrentSeries: Text; expectedFiscalNumber: Text; var ActionContext: WebServiceActionContext)
    var
        CurrentFiscalUuid: Text;
        ElectronicStatus: Text;
        FiscalSeries: Text;
    begin
        CurrentFiscalUuid:=GetDynamicFieldText('Fiscal Invoice Number PAC');
        ElectronicStatus:=GetDynamicFieldText('Electronic Document Status');
        if(expectedInvoiceNumber = '') or (Rec."No." <> expectedInvoiceNumber)then Error('Invoice number does not match the expected invoice.');
        if not Rec.Cancelled then Error('Invoice %1 is not canceled in Business Central.', Rec."No.");
        if UpperCase(ElectronicStatus) <> 'CANCELED' then Error('Invoice %1 FEL status is %2, not Canceled.', Rec."No.", ElectronicStatus);
        if(expectedFiscalUuid = '') or (UpperCase(CurrentFiscalUuid) <> UpperCase(expectedFiscalUuid))then Error('Invoice %1 fiscal UUID does not match the expected UUID.', Rec."No.");
        if(expectedCancelUuid = '') or (UpperCase(Rec.CancelaGTUUID) <> UpperCase(expectedCancelUuid))then Error('Invoice %1 cancellation UUID does not match the expected UUID.', Rec."No.");
        if(expectedFiscalNumber = '') or (Rec.numero <> expectedFiscalNumber)then Error('Invoice %1 fiscal number does not match the expected number.', Rec."No.");
        if StrPos(expectedFiscalUuid, '-') <= 1 then Error('Expected fiscal UUID does not contain a valid fiscal series prefix.');
        FiscalSeries:=CopyStr(expectedFiscalUuid, 1, StrPos(expectedFiscalUuid, '-') - 1);
        if Rec.serie = FiscalSeries then begin
            ActionContext.SetObjectType(ObjectType::Page);
            ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
            ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
            ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
            exit;
        end;
        if(expectedCurrentSeries = '') or (Rec.serie <> expectedCurrentSeries)then Error('Invoice %1 current fiscal series does not match the expected value.', Rec."No.");
        Rec.serie:=CopyStr(FiscalSeries, 1, MaxStrLen(Rec.serie));
        Rec.Modify(false);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure SetMxSubstitutionRelation(oldInvoiceNumber: Text; var ActionContext: WebServiceActionContext)
    var
        MXCfdiMgt: Codeunit "MTM MX Posted Inv CFDI Mgt";
    begin
        MXCfdiMgt.SetSubstitutionRelation(Rec, CopyStr(oldInvoiceNumber, 1, MaxStrLen(Rec."No.")));
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure StampMxInvoice(var ActionContext: WebServiceActionContext)
    var
        MXCfdiMgt: Codeunit "MTM MX Posted Inv CFDI Mgt";
    begin
        MXCfdiMgt.StampMxInvoice(Rec);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CheckMxReplacementReadiness(expectedOriginalUuid: Text; expectedExternalDocumentNumber: Text; expectedAmountIncludingVat: Decimal; var ActionContext: WebServiceActionContext)
    var
        Management: Codeunit "MTM MX Cancellation Mgt";
    begin
        if StrLen(expectedExternalDocumentNumber) > 35 then
            Error('Shipment identity exceeds Business Central field limits.');
        Management.CheckOriginalReadiness(Rec."No.", expectedOriginalUuid,
            CopyStr(expectedExternalDocumentNumber, 1, 35), expectedAmountIncludingVat);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;

    [ServiceEnabled]
    procedure RequestMxCancellation(substitutionInvoiceNumber: Text; expectedOriginalUuid: Text; expectedReplacementUuid: Text; expectedExternalDocumentNumber: Text; expectedAmountIncludingVat: Decimal; expectedReplacementDueDate: Date; var ActionContext: WebServiceActionContext)
    var
        Management: Codeunit "MTM MX Cancellation Mgt";
    begin
        if (StrLen(substitutionInvoiceNumber) > 20) or (StrLen(expectedExternalDocumentNumber) > 35) then
            Error('Invoice identity exceeds Business Central field limits.');
        Management.RequestCancellation(Rec."No.", CopyStr(substitutionInvoiceNumber, 1, 20), expectedOriginalUuid,
            expectedReplacementUuid, CopyStr(expectedExternalDocumentNumber, 1, 35), expectedAmountIncludingVat, expectedReplacementDueDate);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;

    [ServiceEnabled]
    procedure RequestMxCorrectedCancellation(substitutionInvoiceNumber: Text; expectedOriginalUuid: Text; expectedReplacementUuid: Text; expectedExternalDocumentNumber: Text; expectedOriginalAmountIncludingVat: Decimal; expectedAmountIncludingVat: Decimal; expectedReplacementDueDate: Date; var ActionContext: WebServiceActionContext)
    var
        Management: Codeunit "MTM MX Cancellation Mgt";
    begin
        if (StrLen(substitutionInvoiceNumber) > 20) or (StrLen(expectedExternalDocumentNumber) > 35) then
            Error('Invoice identity exceeds Business Central field limits.');
        Management.RequestCorrectedCancellation(Rec."No.", CopyStr(substitutionInvoiceNumber, 1, 20), expectedOriginalUuid,
            expectedReplacementUuid, CopyStr(expectedExternalDocumentNumber, 1, 35), expectedOriginalAmountIncludingVat, expectedAmountIncludingVat, expectedReplacementDueDate);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;

    [ServiceEnabled]
    procedure CancelMxInvoiceWithSubstitution(substitutionInvoiceNumber: Text; cancellationReasonId: Text; var ActionContext: WebServiceActionContext)
    var
        MXCfdiMgt: Codeunit "MTM MX Posted Inv CFDI Mgt";
    begin
        MXCfdiMgt.CancelMxInvoiceWithSubstitution(Rec, CopyStr(substitutionInvoiceNumber, 1, MaxStrLen(Rec."No.")), cancellationReasonId);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Inv FEL Desc API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    local procedure GetInvoiceEmailReadiness(): Text
    var
        InvoiceCustomerEmailMgt: Codeunit "MTM Invoice Customer Email Mgt";
    begin
        exit(InvoiceCustomerEmailMgt.GetInvoiceEmailReadiness(Rec));
    end;

    local procedure GetDynamicFieldText(FieldName: Text): Text var
        RecRef: RecordRef;
        FieldRef: FieldRef;
    begin
        RecRef.GetTable(Rec);
        if not TryGetFieldByName(RecRef, FieldName, FieldRef)then exit('');
        exit(Format(FieldRef.Value()));
    end;
    local procedure SetDynamicFieldText(FieldName: Text; FieldValue: Text)
    var
        RecRef: RecordRef;
        FieldRef: FieldRef;
    begin
        RecRef.GetTable(Rec);
        TryGetFieldByName(RecRef, FieldName, FieldRef);
        FieldRef.Value(FieldValue);
        RecRef.Modify();
        Rec.Get(Rec."No.");
    end;
    local procedure BuildMxStampReadinessSummary(): Text var
        PaymentMethod: Record "Payment Method";
        PaymentTerms: Record "Payment Terms";
        SalesInvoiceLine: Record "Sales Invoice Line";
        RelationDocRef: RecordRef;
        MissingDescriptionXLCount: Integer;
        RelationCount: Integer;
        LineCount: Integer;
        SatMethodOfPayment: Text;
        SatPaymentTerm: Text;
    begin
        if PaymentTerms.Get(Rec."Payment Terms Code")then SatPaymentTerm:=Format(GetRecordFieldValue(PaymentTerms, 'SAT Payment Term'));
        if PaymentMethod.Get(Rec."Payment Method Code")then SatMethodOfPayment:=Format(GetRecordFieldValue(PaymentMethod, 'SAT Method of Payment'));
        SalesInvoiceLine.SetRange("Document No.", Rec."No.");
        SalesInvoiceLine.SetRange(Type, SalesInvoiceLine.Type::Item);
        if SalesInvoiceLine.FindSet()then repeat LineCount+=1;
                if DelChr(Format(GetRecordFieldValue(SalesInvoiceLine, 'Description XL')), '=', ' ') = '' then MissingDescriptionXLCount+=1;
            until SalesInvoiceLine.Next() = 0;
        RelationDocRef.Open(27006);
        SetFieldFilter(RelationDocRef, 'Document No.', Rec."No.");
        if RelationDocRef.FindSet()then repeat RelationCount+=1;
            until RelationDocRef.Next() = 0;
        exit(StrSubstNo('PaymentTerms=%1;SATPaymentTerm=%2;PaymentMethod=%3;SATMethodOfPayment=%4;CFDIRelation=%5;RelationRows=%6;ItemLines=%7;MissingDescriptionXL=%8', Rec."Payment Terms Code", SatPaymentTerm, Rec."Payment Method Code", SatMethodOfPayment, GetDynamicFieldText('CFDI Relation'), RelationCount, LineCount, MissingDescriptionXLCount));
    end;
    local procedure GetRecordFieldValue(RecVariant: Variant; FieldName: Text): Text var
        RecRef: RecordRef;
        FieldRef: FieldRef;
    begin
        RecRef.GetTable(RecVariant);
        if not TryGetFieldByName(RecRef, FieldName, FieldRef)then exit('');
        exit(Format(FieldRef.Value()));
    end;
    local procedure SetFieldFilter(var RecRef: RecordRef; FieldName: Text; FieldValue: Text)
    var
        FieldRef: FieldRef;
    begin
        TryGetFieldByName(RecRef, FieldName, FieldRef);
        FieldRef.SetRange(FieldValue);
    end;
    [TryFunction]
    local procedure TryGetFieldByName(var RecRef: RecordRef; FieldName: Text; var FieldRef: FieldRef)
    var
        FieldNo: Integer;
    begin
        for FieldNo:=1 to RecRef.FieldCount()do begin
            FieldRef:=RecRef.FieldIndex(FieldNo);
            if FieldRef.Name() = FieldName then exit;
        end;
        Error('Field %1 was not found.', FieldName);
    end;
}
