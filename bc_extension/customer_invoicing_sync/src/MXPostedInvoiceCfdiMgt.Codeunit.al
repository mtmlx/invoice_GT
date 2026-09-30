codeunit 71008 "MTM MX Posted Inv CFDI Mgt"
{
    Permissions = tabledata "Sales Invoice Header"=rm;

    procedure SetSubstitutionRelation(var ReplacementSalesInv: Record "Sales Invoice Header"; OldInvoiceNo: Code[20])
    var
        OldSalesInv: Record "Sales Invoice Header";
        OldFiscalInvoiceNumberPAC: Text;
    begin
        if OldInvoiceNo = '' then Error('Old invoice number is required.');
        OldSalesInv.Get(OldInvoiceNo);
        OldFiscalInvoiceNumberPAC:=UpperCase(GetDynamicFieldText(OldSalesInv, 'Fiscal Invoice Number PAC'));
        if OldFiscalInvoiceNumberPAC = '' then Error('Old invoice %1 does not have Fiscal Invoice Number PAC.', OldSalesInv."No.");
        if ReplacementSalesInv."Sell-to Customer No." <> OldSalesInv."Sell-to Customer No." then Error('Replacement invoice %1 customer %2 does not match old invoice %3 customer %4.', ReplacementSalesInv."No.", ReplacementSalesInv."Sell-to Customer No.", OldSalesInv."No.", OldSalesInv."Sell-to Customer No.");
        SetDynamicFieldText(ReplacementSalesInv, 'CFDI Relation', '04');
        UpsertCfdiRelationDocument(ReplacementSalesInv, OldSalesInv, OldFiscalInvoiceNumberPAC);
    end;
    procedure StampMxInvoice(var SalesInv: Record "Sales Invoice Header")
    var
        FunFactura: Codeunit "Fun. Factura";
        ElectronicDocumentStatus: Text;
        FiscalInvoiceNumberPAC: Text;
    begin
        if SalesInv.Cancelled then Error('Invoice %1 is cancelled and cannot be stamped.', SalesInv."No.");
        FiscalInvoiceNumberPAC:=GetDynamicFieldText(SalesInv, 'Fiscal Invoice Number PAC');
        if FiscalInvoiceNumberPAC <> '' then Error('Invoice %1 already has Fiscal Invoice Number PAC %2.', SalesInv."No.", FiscalInvoiceNumberPAC);
        ElectronicDocumentStatus:=GetDynamicFieldText(SalesInv, 'Electronic Document Status');
        if(DelChr(ElectronicDocumentStatus, '=', ' ') <> '') and (ElectronicDocumentStatus <> 'Stamp Request Error')then Error('Invoice %1 has electronic document status %2 and cannot be stamped by this API.', SalesInv."No.", ElectronicDocumentStatus);
        if SalesInv."Shipment Date" > SalesInv."Posting Date" then begin
            SalesInv."Shipment Date":=SalesInv."Posting Date";
            SalesInv.Modify(true);
        end;
        FunFactura.Factura(SalesInv);
    end;
    procedure CancelMxInvoiceWithSubstitution(var OldSalesInv: Record "Sales Invoice Header"; SubstitutionInvoiceNo: Code[20]; CancellationReasonId: Text)
    var
        ReplacementSalesInv: Record "Sales Invoice Header";
        CorrectPostedSalesInvoice: Codeunit "Correct Posted Sales Invoice";
        CancelaFactura: Codeunit CancelaFactura;
        OldDateTimeCanceled: Text;
        OldElectronicDocumentStatus: Text;
        OldFiscalInvoiceNumberPAC: Text;
        ReplacementFiscalInvoiceNumberPAC: Text;
    begin
        if CancellationReasonId = '' then CancellationReasonId:='01';
        if CancellationReasonId <> '01' then Error('Only cancellation reason 01 is valid for cancellation with substitution.');
        if SubstitutionInvoiceNo = '' then Error('Substitution invoice number is required.');
        OldFiscalInvoiceNumberPAC:=GetDynamicFieldText(OldSalesInv, 'Fiscal Invoice Number PAC');
        if OldFiscalInvoiceNumberPAC = '' then Error('Invoice %1 does not have Fiscal Invoice Number PAC.', OldSalesInv."No.");
        OldDateTimeCanceled:=GetDynamicFieldText(OldSalesInv, 'Date/Time Canceled');
        if OldDateTimeCanceled <> '' then Error('Invoice %1 already has Date/Time Canceled %2.', OldSalesInv."No.", OldDateTimeCanceled);
        OldElectronicDocumentStatus:=GetDynamicFieldText(OldSalesInv, 'Electronic Document Status');
        if(not OldSalesInv.Cancelled) and (OldElectronicDocumentStatus <> 'Stamp Received')then Error('Invoice %1 must be stamped before cancellation with substitution.', OldSalesInv."No.");
        ReplacementSalesInv.Get(SubstitutionInvoiceNo);
        ReplacementFiscalInvoiceNumberPAC:=GetDynamicFieldText(ReplacementSalesInv, 'Fiscal Invoice Number PAC');
        if ReplacementFiscalInvoiceNumberPAC = '' then Error('Substitution invoice %1 is not stamped yet.', ReplacementSalesInv."No.");
        if ReplacementSalesInv."Sell-to Customer No." <> OldSalesInv."Sell-to Customer No." then Error('Substitution invoice %1 customer %2 does not match old invoice %3 customer %4.', ReplacementSalesInv."No.", ReplacementSalesInv."Sell-to Customer No.", OldSalesInv."No.", OldSalesInv."Sell-to Customer No.");
        SetDynamicFieldText(OldSalesInv, 'Substitution Document No.', ReplacementSalesInv."No.");
        if not OldSalesInv.Cancelled then begin
            CorrectPostedSalesInvoice.CancelPostedInvoice(OldSalesInv);
            OldSalesInv.Get(OldSalesInv."No.");
        end;
        CancelaFactura.CacelaComplemento(OldSalesInv, CancellationReasonId);
    end;
    local procedure UpsertCfdiRelationDocument(ReplacementSalesInv: Record "Sales Invoice Header"; OldSalesInv: Record "Sales Invoice Header"; OldFiscalInvoiceNumberPAC: Text)
    var
        RelationDocRef: RecordRef;
    begin
        RelationDocRef.Open(27006); // Microsoft Mexico localization table: CFDI Relation Document.
        SetFieldFilter(RelationDocRef, 'Document No.', ReplacementSalesInv."No.");
        SetFieldFilter(RelationDocRef, 'Related Doc. No.', OldSalesInv."No.");
        if RelationDocRef.FindFirst()then begin
            SetFieldValue(RelationDocRef, 'Fiscal Invoice Number PAC', OldFiscalInvoiceNumberPAC);
            RelationDocRef.Modify(true);
            exit;
        end;
        RelationDocRef.Init();
        SetFieldValue(RelationDocRef, 'Document Table ID', Database::"Sales Invoice Header");
        SetFieldValue(RelationDocRef, 'Customer No.', ReplacementSalesInv."Bill-to Customer No.");
        SetFieldValue(RelationDocRef, 'Document No.', ReplacementSalesInv."No.");
        SetFieldValue(RelationDocRef, 'Related Doc. No.', OldSalesInv."No.");
        SetFieldValue(RelationDocRef, 'Fiscal Invoice Number PAC', OldFiscalInvoiceNumberPAC);
        RelationDocRef.Insert(true);
    end;
    local procedure GetDynamicFieldText(RecVariant: Variant; FieldName: Text): Text var
        RecRef: RecordRef;
        FldRef: FieldRef;
    begin
        RecRef.GetTable(RecVariant);
        GetFieldByName(RecRef, FieldName, FldRef);
        exit(Format(FldRef.Value()));
    end;
    local procedure SetDynamicFieldText(RecVariant: Variant; FieldName: Text; FieldValue: Text)
    var
        RecRef: RecordRef;
        FldRef: FieldRef;
    begin
        RecRef.GetTable(RecVariant);
        GetFieldByName(RecRef, FieldName, FldRef);
        FldRef.Value(FieldValue);
        RecRef.Modify();
    end;
    local procedure SetFieldFilter(var RecRef: RecordRef; FieldName: Text; FieldValue: Text)
    var
        FldRef: FieldRef;
    begin
        GetFieldByName(RecRef, FieldName, FldRef);
        FldRef.SetRange(FieldValue);
    end;
    local procedure SetFieldValue(var RecRef: RecordRef; FieldName: Text; FieldValue: Variant)
    var
        FldRef: FieldRef;
    begin
        GetFieldByName(RecRef, FieldName, FldRef);
        FldRef.Value(FieldValue);
    end;
    local procedure GetFieldByName(var RecRef: RecordRef; FieldName: Text; var FldRef: FieldRef)
    var
        FieldNo: Integer;
    begin
        for FieldNo:=1 to RecRef.FieldCount()do begin
            FldRef:=RecRef.FieldIndex(FieldNo);
            if FldRef.Name() = FieldName then exit;
        end;
        Error('Field %1 was not found in table %2.', FieldName, RecRef.Number());
    end;
}
