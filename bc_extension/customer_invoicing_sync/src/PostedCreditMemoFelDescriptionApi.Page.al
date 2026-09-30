page 71010 "MTM Posted Cr Memo FEL API"
{
    PageType = API;
    APIPublisher = 'mtmlogix';
    APIGroup = 'invoiceSync';
    APIVersion = 'v1.0';
    EntityName = 'postedCreditMemoFelDescription';
    EntitySetName = 'postedCreditMemoFelDescriptions';
    SourceTable = "Sales Cr.Memo Header";
    ODataKeyFields = SystemId;
    DelayedInsert = false;
    Extensible = false;
    InsertAllowed = false;
    ModifyAllowed = false;
    DeleteAllowed = false;
    Permissions = tabledata "Sales Cr.Memo Header"=rm,
        tabledata "Sales Cr.Memo Line"=rm,
        tabledata "Sales Invoice Header"=r;

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
                field(corrective; Rec.Corrective)
                {
                    Caption = 'Corrective';
                    Editable = false;
                }
                field(cancelGtUuid; Rec.CancelaGTUUID)
                {
                    Caption = 'Cancel GT UUID';
                    Editable = false;
                }
                field(motivo; Rec.Motivo)
                {
                    Caption = 'Motive Code';
                    Editable = false;
                }
                field(motivoCancela; Rec."Motivo Cancela")
                {
                    Caption = 'Cancellation Motive';
                    Editable = false;
                }
            }
        }
    }
    [ServiceEnabled]
    procedure StampFelCreditMemo(var ActionContext: WebServiceActionContext)
    var
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        GTFelMgt.StampPostedCreditMemoNoEmail(Rec);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Cr Memo FEL API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure StampFelFullCreditMemoFromRelatedInvoice(var ActionContext: WebServiceActionContext)
    var
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        GTFelMgt.StampPostedFullCreditMemoUsingRelatedInvoiceLinesNoEmail(Rec);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Cr Memo FEL API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure ApplyToInvoice(invoiceNumber: Text; expectedAppliedAmount: Decimal; var ActionContext: WebServiceActionContext)
    var
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        GTFelMgt.ApplyPostedCreditMemoToInvoice(Rec, CopyStr(invoiceNumber, 1, MaxStrLen(Rec."No.")), expectedAppliedAmount);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Cr Memo FEL API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure SetCreditMemoMotive(motiveText: Text; var ActionContext: WebServiceActionContext)
    begin
        if DelChr(motiveText, '=', ' ') = '' then Error('Credit memo motive is required.');
        Rec."Motivo Cancela":=CopyStr(motiveText, 1, MaxStrLen(Rec."Motivo Cancela"));
        Rec.Modify(true);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Cr Memo FEL API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure SetCreditMemoRelatedInvoice(invoiceNumber: Text; var ActionContext: WebServiceActionContext)
    var
        RelatedSalesInv: Record "Sales Invoice Header";
        RelatedSalesInvRef: RecordRef;
        RelationDocRef: RecordRef;
        FiscalInvoiceNumberPacField: FieldRef;
        RelatedFiscalInvoiceNumberPac: Text;
        RelatedInvoiceNo: Code[20];
    begin
        RelatedInvoiceNo:=CopyStr(invoiceNumber, 1, MaxStrLen(RelatedInvoiceNo));
        if RelatedInvoiceNo = '' then Error('Related invoice number is required.');
        RelatedSalesInv.Get(RelatedInvoiceNo);
        if Rec."Sell-to Customer No." <> RelatedSalesInv."Sell-to Customer No." then Error('Credit memo %1 customer %2 does not match invoice %3 customer %4.', Rec."No.", Rec."Sell-to Customer No.", RelatedSalesInv."No.", RelatedSalesInv."Sell-to Customer No.");
        RelatedSalesInvRef.GetTable(RelatedSalesInv);
        if not TryGetFieldByName(RelatedSalesInvRef, 'Fiscal Invoice Number PAC', FiscalInvoiceNumberPacField)then Error('Business Central field Fiscal Invoice Number PAC is not available on %1.', RelatedSalesInvRef.Name());
        RelatedFiscalInvoiceNumberPac:=Format(FiscalInvoiceNumberPacField.Value());
        if RelatedFiscalInvoiceNumberPac = '' then Error('Related invoice %1 does not have Fiscal Invoice Number PAC.', RelatedSalesInv."No.");
        RelationDocRef.Open(27006); // CFDI Relation Document.
        SetFieldFilter(RelationDocRef, 'Document Table ID', Format(Database::"Sales Cr.Memo Header"));
        SetFieldFilter(RelationDocRef, 'Customer No.', Rec."Bill-to Customer No.");
        SetFieldFilter(RelationDocRef, 'Document No.', Rec."No.");
        SetFieldFilter(RelationDocRef, 'Related Doc. No.', RelatedSalesInv."No.");
        if RelationDocRef.FindFirst()then begin
            SetFieldValue(RelationDocRef, 'Fiscal Invoice Number PAC', RelatedFiscalInvoiceNumberPac);
            RelationDocRef.Modify(true);
        end
        else
        begin
            RelationDocRef.Init();
            SetFieldValue(RelationDocRef, 'Document Table ID', Database::"Sales Cr.Memo Header");
            SetFieldValue(RelationDocRef, 'Customer No.', Rec."Bill-to Customer No.");
            SetFieldValue(RelationDocRef, 'Document No.', Rec."No.");
            SetFieldValue(RelationDocRef, 'Related Doc. No.', RelatedSalesInv."No.");
            SetFieldValue(RelationDocRef, 'Fiscal Invoice Number PAC', RelatedFiscalInvoiceNumberPac);
            RelationDocRef.Insert(true);
        end;
        RelationDocRef.Close();
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Cr Memo FEL API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CancelFelCreditMemoWithMotive(motiveText: Text; var ActionContext: WebServiceActionContext)
    var
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        GTFelMgt.CancelPostedCreditMemoWithMotive(Rec, motiveText);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Cr Memo FEL API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    [ServiceEnabled]
    procedure CancelFelCreditMemoWithMotiveAndIssueDateTime(motiveText: Text; issueDateTimeText: Text; var ActionContext: WebServiceActionContext)
    var
        GTFelMgt: Codeunit "MTM GT Posted Inv FEL Mgt";
    begin
        GTFelMgt.CancelPostedCreditMemoWithMotiveAndIssueDateTime(Rec, motiveText, issueDateTimeText);
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM Posted Cr Memo FEL API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
    local procedure GetDynamicFieldText(FieldName: Text): Text var
        RecRef: RecordRef;
        FieldRef: FieldRef;
    begin
        RecRef.GetTable(Rec);
        if not TryGetFieldByName(RecRef, FieldName, FieldRef)then exit('');
        exit(Format(FieldRef.Value()));
    end;
    local procedure SetFieldFilter(var RecRef: RecordRef; FieldName: Text; Value: Text)
    var
        FieldRef: FieldRef;
    begin
        if not TryGetFieldByName(RecRef, FieldName, FieldRef)then Error('Field %1 was not found.', FieldName);
        FieldRef.SetFilter('%1', Value);
    end;
    local procedure SetFieldValue(var RecRef: RecordRef; FieldName: Text; Value: Variant)
    var
        FieldRef: FieldRef;
    begin
        if not TryGetFieldByName(RecRef, FieldName, FieldRef)then Error('Field %1 was not found.', FieldName);
        FieldRef.Value(Value);
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
