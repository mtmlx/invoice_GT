codeunit 71008 "MTM MX Posted Inv CFDI Mgt"
{
    Permissions =
        tabledata "Sales Invoice Header" = rm;

    procedure SetSubstitutionRelation(var ReplacementSalesInv: Record "Sales Invoice Header"; OldInvoiceNo: Code[20])
    var
        OldSalesInv: Record "Sales Invoice Header";
        OldFiscalInvoiceNumberPAC: Text;
    begin
        if OldInvoiceNo = '' then
            Error('Old invoice number is required.');

        if ReplacementSalesInv."No." = OldInvoiceNo then
            Error('An invoice cannot substitute itself.');
        if GetDynamicFieldText(ReplacementSalesInv, 'Fiscal Invoice Number PAC') <> '' then
            Error('A stamped replacement cannot have its CFDI relation changed.');
        ReplacementSalesInv.CalcFields(Cancelled);
        ReplacementSalesInv.TestField(Cancelled, false);
        OldSalesInv.Get(OldInvoiceNo);
        OldSalesInv.CalcFields(Cancelled);
        OldSalesInv.TestField(Cancelled, false);
        ReplacementSalesInv.TestField("Bill-to Customer No.", OldSalesInv."Bill-to Customer No.");
        ReplacementSalesInv.TestField("Currency Code", OldSalesInv."Currency Code");
        ReplacementSalesInv.TestField("External Document No.", OldSalesInv."External Document No.");
        if GetDynamicFieldText(OldSalesInv, 'Electronic Document Status') <> 'Stamp Received' then
            Error('The original invoice must still be fiscally active before preparing its replacement.');
        if (GetDynamicFieldText(OldSalesInv, 'Substitution Document No.') <> '') and
           (GetDynamicFieldText(OldSalesInv, 'Substitution Document No.') <> ReplacementSalesInv."No.")
        then
            Error('The original invoice already refers to a different replacement.');
        OldFiscalInvoiceNumberPAC := UpperCase(GetDynamicFieldText(OldSalesInv, 'Fiscal Invoice Number PAC'));
        if OldFiscalInvoiceNumberPAC = '' then
            Error('Old invoice %1 does not have Fiscal Invoice Number PAC.', OldSalesInv."No.");
        if ReplacementSalesInv."Sell-to Customer No." <> OldSalesInv."Sell-to Customer No." then
            Error(
                'Replacement invoice %1 customer %2 does not match old invoice %3 customer %4.',
                ReplacementSalesInv."No.",
                ReplacementSalesInv."Sell-to Customer No.",
                OldSalesInv."No.",
                OldSalesInv."Sell-to Customer No.");

        SetDynamicFieldText(ReplacementSalesInv, 'CFDI Relation', '04');

        UpsertCfdiRelationDocument(ReplacementSalesInv, OldSalesInv, OldFiscalInvoiceNumberPAC);
    end;

    procedure StampMxInvoice(var SalesInv: Record "Sales Invoice Header")
    var
        FunFactura: Codeunit "Fun. Factura";
        ElectronicDocumentStatus: Text;
        FiscalInvoiceNumberPAC: Text;
    begin
        if SalesInv.Cancelled then
            Error('Invoice %1 is cancelled and cannot be stamped.', SalesInv."No.");

        FiscalInvoiceNumberPAC := GetDynamicFieldText(SalesInv, 'Fiscal Invoice Number PAC');
        if FiscalInvoiceNumberPAC <> '' then
            Error('Invoice %1 already has Fiscal Invoice Number PAC %2.', SalesInv."No.", FiscalInvoiceNumberPAC);

        ElectronicDocumentStatus := GetDynamicFieldText(SalesInv, 'Electronic Document Status');
        if (DelChr(ElectronicDocumentStatus, '=', ' ') <> '') and (ElectronicDocumentStatus <> 'Stamp Request Error') then
            Error(
                'Invoice %1 has electronic document status %2 and cannot be stamped by this API.',
                SalesInv."No.",
                ElectronicDocumentStatus);

        FunFactura.Factura(SalesInv);
    end;

    procedure CancelMxInvoiceWithSubstitution(var OldSalesInv: Record "Sales Invoice Header"; SubstitutionInvoiceNo: Code[20]; CancellationReasonId: Text)
    begin
        Error('Combined Mexico cancellation is retired. Use RequestMxCancellation, then RefreshStatus and FinalizeAccounting on mxCancellations.');
    end;

    local procedure UpsertCfdiRelationDocument(ReplacementSalesInv: Record "Sales Invoice Header"; OldSalesInv: Record "Sales Invoice Header"; OldFiscalInvoiceNumberPAC: Text)
    var
        RelationDocRef: RecordRef;
        RelatedDocumentField: FieldRef;
    begin
        RelationDocRef.Open(27006); // Microsoft Mexico localization table: CFDI Relation Document.
        SetFieldFilter(RelationDocRef, 'Document No.', ReplacementSalesInv."No.");
        if RelationDocRef.Count() > 1 then
            Error('Replacement already has multiple CFDI relations.');
        if RelationDocRef.FindFirst() then begin
            GetFieldByName(RelationDocRef, 'Related Doc. No.', RelatedDocumentField);
            if Format(RelatedDocumentField.Value()) <> OldSalesInv."No." then
                Error('Replacement is already linked to a different original invoice.');
        end;
        SetFieldFilter(RelationDocRef, 'Related Doc. No.', OldSalesInv."No.");
        if RelationDocRef.FindFirst() then begin
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

    local procedure GetDynamicFieldText(RecVariant: Variant; FieldName: Text): Text
    var
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
        for FieldNo := 1 to RecRef.FieldCount() do begin
            FldRef := RecRef.FieldIndex(FieldNo);
            if FldRef.Name() = FieldName then
                exit;
        end;

        Error('Field %1 was not found in table %2.', FieldName, RecRef.Number());
    end;
}
