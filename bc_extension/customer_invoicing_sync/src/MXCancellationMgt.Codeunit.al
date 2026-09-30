codeunit 71040 "MTM MX Cancellation Mgt"
{
    Permissions = tabledata "MTM MX Cancellation" = rim,
        tabledata "Sales Invoice Header" = rm;

    procedure CheckOriginalReadiness(InvoiceNo: Code[20]; ExpectedUUID: Text; ExpectedReference: Code[35]; ExpectedAmount: Decimal)
    var
        Operation: Record "MTM MX Cancellation" temporary;
        Original: Record "Sales Invoice Header";
        Company: Record "Company Information";
        Customer: Record Customer;
        Provider: Codeunit "MTM MX Cancellation Provider";
        CorrectInvoice: Codeunit "Correct Posted Sales Invoice";
        Request: HttpRequestMessage;
    begin
        if CompanyName() <> 'MTM_MX_PROD' then
            Error('This readiness check is scoped to MTM_MX_PROD.');
        if (ExpectedUUID = '') or (ExpectedReference = '') or (ExpectedAmount <= 0) then
            Error('Complete original invoice identity is required.');
        Operation."Invoice No." := InvoiceNo;
        Operation."Original UUID" := UpperCase(ExpectedUUID);
        Operation."External Document No." := ExpectedReference;
        Operation."Customer No." := 'C00067';
        Operation."Currency Code" := 'USD';
        Operation.Amount := ExpectedAmount;
        Company.Get();
        Customer.Get(Operation."Customer No.");
        Operation."Issuer RFC" := ReadField(Company, 'RFC Number');
        Operation."Recipient RFC" := ReadField(Customer, 'RFC No.');
        Operation.TestField("Issuer RFC");
        Operation.TestField("Recipient RFC");
        Original.Get(InvoiceNo);
        ValidateInvoice(Original, Operation, false);
        if ReadField(Original, 'Electronic Document Status') <> 'Stamp Received' then
            Error('The original must be stamped and active.');
        if ReadField(Original, 'Substitution Document No.') <> '' then
            Error('The original already identifies a replacement.');
        ValidateStampedXml(Original, Operation, false);
        EnsureUnapplied(Original);
        CorrectInvoice.TestCorrectInvoiceIsAllowed(Original, true);
        // Build locally to validate configured signing material. Never send it.
        Provider.PrepareRequest(Operation, Request);
        if not Provider.QuerySat(Operation, false) or (Operation."SAT Status" <> 'Vigente') then
            Error('SAT has not confirmed that the original CFDI is active. Diagnostic: %1. Query: %2', Operation."Result Code", Provider.GetSatRequestDiagnostic());
        // No Insert, Modify or Commit: this preflight never changes BC documents.
    end;

    procedure RequestCancellation(InvoiceNo: Code[20]; ReplacementNo: Code[20]; ExpectedOriginalUUID: Text; ExpectedReplacementUUID: Text; ExpectedReference: Code[35]; ExpectedAmount: Decimal; ExpectedDueDate: Date)
    var
        Operation: Record "MTM MX Cancellation";
        Original: Record "Sales Invoice Header";
        Replacement: Record "Sales Invoice Header";
        Company: Record "Company Information";
        Customer: Record Customer;
        Provider: Codeunit "MTM MX Cancellation Provider";
        CorrectInvoice: Codeunit "Correct Posted Sales Invoice";
        Request: HttpRequestMessage;
    begin
        if (ExpectedOriginalUUID = '') or (ExpectedReplacementUUID = '') or
           (ExpectedReference = '') or (ExpectedAmount <= 0) or (ExpectedDueDate = 0D)
        then
            Error('A complete approved replacement identity is required.');
        Operation."Invoice No." := InvoiceNo;
        Operation."Replacement No." := ReplacementNo;
        Operation."Original UUID" := UpperCase(ExpectedOriginalUUID);
        Operation."Replacement UUID" := UpperCase(ExpectedReplacementUUID);
        Operation."External Document No." := ExpectedReference;
        Operation."Customer No." := 'C00067';
        Operation."Currency Code" := 'USD';
        Operation.Amount := ExpectedAmount;
        Operation."Replacement Due Date" := ExpectedDueDate;
        Company.Get();
        Customer.Get(Operation."Customer No.");
        Operation."Issuer RFC" := ReadField(Company, 'RFC Number');
        Operation."Recipient RFC" := ReadField(Customer, 'RFC No.');
        Operation.TestField("Issuer RFC");
        Operation.TestField("Recipient RFC");
        ValidatePair(Operation, Original, Replacement);
        // A repeat of the identical operation is a status read, never another POST.
        if ExistingOperationMatches(Operation) then
            exit;
        EnsureUnapplied(Original);
        CorrectInvoice.TestCorrectInvoiceIsAllowed(Original, true);
        if not Provider.QuerySat(Operation, true) then
            Error('SAT has not confirmed that the replacement CFDI is active.');
        if not Provider.QuerySat(Operation, false) or (Operation."SAT Status" <> 'Vigente') then
            Error('SAT has not confirmed that the original CFDI is active.');
        Provider.PrepareRequest(Operation, Request);

        Operation.LockTable();
        if ExistingOperationMatches(Operation) then
            exit;
        // Re-read financial and document state after the network preflight.
        ValidatePair(Operation, Original, Replacement);
        EnsureUnapplied(Original);
        Operation.State := Operation.State::Unknown;
        Operation."Requested At" := CurrentDateTime();
        Operation."Requested By" := UserSecurityId();
        Operation."Result Code" := 'REQUEST_OUTCOME_UNKNOWN';
        Operation.Insert(true);
        WriteField(Original, 'Substitution Document No.', Replacement."No.");
        Commit(); // Durable identity/at-most-once marker before external side effect.

        Provider.Submit(Operation, Request);
        Operation.Modify(true);
        // No accounting reversal, even if the provider reports immediate success.
    end;

    procedure Refresh(var Operation: Record "MTM MX Cancellation")
    var
        Provider: Codeunit "MTM MX Cancellation Provider";
        Original: Record "Sales Invoice Header";
        Replacement: Record "Sales Invoice Header";
    begin
        Operation.Get(Operation."Invoice No.");
        if Operation.State = Operation.State::Completed then
            exit;
        ValidatePair(Operation, Original, Replacement);
        if not Provider.QuerySat(Operation, false) then begin
            Operation.State := Operation.State::Unknown;
            Operation."Result Code" := 'SAT_QUERY_UNCONFIRMED';
        end;
        Operation.Modify(true);
    end;

    procedure Finalize(var Operation: Record "MTM MX Cancellation")
    var
        Original: Record "Sales Invoice Header";
        Replacement: Record "Sales Invoice Header";
        CorrectInvoice: Codeunit "Correct Posted Sales Invoice";
        Provider: Codeunit "MTM MX Cancellation Provider";
        CancelledDocument: Record "Cancelled Document";
    begin
        Operation.Get(Operation."Invoice No.");
        ValidatePair(Operation, Original, Replacement);
        if Operation.State = Operation.State::Completed then begin
            VerifyAccountingReversal(Operation, Original);
            exit;
        end;
        // Fresh authoritative checks; a stale database flag cannot authorize reversal.
        if not Provider.QuerySat(Operation, true) then
            Error('Replacement CFDI is not confirmed active by SAT.');
        if not Provider.QuerySat(Operation, false) or (Operation.State <> Operation.State::Confirmed) then
            Error('Original CFDI cancellation is not confirmed by SAT. Accounting was not reversed.');
        Operation.Modify(true);
        Commit(); // Retain fiscal evidence even if the later BC reversal fails.

        Operation.LockTable();
        Operation.Get(Operation."Invoice No.");
        Original.Get(Operation."Invoice No.");
        Original.CalcFields(Cancelled);
        if not Original.Cancelled then begin
            if Operation."Accounting Attempted At" <> 0DT then
                Error('A previous accounting attempt needs reconciliation before retrying.');
            EnsureUnapplied(Original);
            CorrectInvoice.TestCorrectInvoiceIsAllowed(Original, true);
            Operation."Result Code" := 'ACCOUNTING_OUTCOME_UNKNOWN';
            Operation."Accounting Attempted At" := CurrentDateTime();
            Operation.Modify(true);
            Commit();
            if not CorrectInvoice.CancelPostedInvoice(Original) then
                Error('Business Central did not confirm the accounting reversal. Reconcile before retrying.');
        end;
        Original.Get(Operation."Invoice No.");
        Original.CalcFields(Cancelled);
        VerifyAccountingReversal(Operation, Original);
        CancelledDocument.FindSalesCancelledInvoice(Original."No.");
        Operation."Credit Memo No." := CancelledDocument."Cancelled By Doc. No.";
        SetFiscalCanceled(Original);
        Operation.State := Operation.State::Completed;
        Operation."Completed At" := CurrentDateTime();
        Operation."Result Code" := 'VERIFIED_COMPLETE';
        Operation.Modify(true);
    end;

    local procedure ExistingOperationMatches(Expected: Record "MTM MX Cancellation"): Boolean
    var
        Existing: Record "MTM MX Cancellation";
    begin
        if not Existing.Get(Expected."Invoice No.") then
            exit(false);
        if (Existing."Replacement No." <> Expected."Replacement No.") or
           (Existing."Original UUID" <> Expected."Original UUID") or
           (Existing."Replacement UUID" <> Expected."Replacement UUID") or
           (Existing."External Document No." <> Expected."External Document No.") or
           (Existing.Amount <> Expected.Amount) or
           (Existing."Issuer RFC" <> Expected."Issuer RFC") or
           (Existing."Recipient RFC" <> Expected."Recipient RFC") or
           (Existing."Replacement Due Date" <> Expected."Replacement Due Date")
        then
            Error('An existing cancellation is bound to a different replacement identity.');
        exit(true);
    end;

    local procedure ValidatePair(Operation: Record "MTM MX Cancellation"; var Original: Record "Sales Invoice Header"; var Replacement: Record "Sales Invoice Header")
    begin
        if CompanyName() <> 'MTM_MX_PROD' then
            Error('This controlled Ocean cancellation path is scoped to MTM_MX_PROD.');
        if (Operation."Invoice No." = Operation."Replacement No.") or
           (Operation."Original UUID" = Operation."Replacement UUID")
        then
            Error('An invoice cannot substitute itself.');
        Original.Get(Operation."Invoice No.");
        Replacement.Get(Operation."Replacement No.");
        Original.CalcFields(Cancelled);
        Replacement.CalcFields(Cancelled);
        ValidateInvoice(Original, Operation, false);
        ValidateInvoice(Replacement, Operation, true);
        if (ReadField(Original, 'Substitution Document No.') <> '') and
           (ReadField(Original, 'Substitution Document No.') <> Replacement."No.")
        then
            Error('The original invoice already refers to a different replacement.');
        Replacement.TestField("Due Date", Operation."Replacement Due Date");
        Replacement.TestField(Cancelled, false);
        if ReadField(Replacement, 'Electronic Document Status') <> 'Stamp Received' then
            Error('Replacement must be stamped and active in BC.');
        ValidateStampedXml(Original, Operation, false);
        ValidateStampedXml(Replacement, Operation, true);
    end;

    local procedure ValidateInvoice(Invoice: Record "Sales Invoice Header"; Operation: Record "MTM MX Cancellation"; IsReplacement: Boolean)
    var
        Line: Record "Sales Invoice Line";
        HasOceanFreight: Boolean;
    begin
        Invoice.TestField("Sell-to Customer No.", Operation."Customer No.");
        Invoice.TestField("Bill-to Customer No.", Operation."Customer No.");
        Invoice.TestField("Currency Code", Operation."Currency Code");
        Invoice.TestField("External Document No.", Operation."External Document No.");
        Invoice.CalcFields("Amount Including VAT");
        if Invoice."Amount Including VAT" <> Operation.Amount then
            Error('Invoice %1 total differs from the approved amount.', Invoice."No.");
        if IsReplacement then begin
            if UpperCase(ReadField(Invoice, 'Fiscal Invoice Number PAC')) <> Operation."Replacement UUID" then
                Error('Replacement fiscal UUID differs from the approved identity.');
        end else
            if UpperCase(ReadField(Invoice, 'Fiscal Invoice Number PAC')) <> Operation."Original UUID" then
                Error('Original fiscal UUID differs from the approved identity.');
        Line.SetRange("Document No.", Invoice."No.");
        if Line.FindSet() then
            repeat
                if Line.Type <> Line.Type::" " then begin
                    Line.TestField(Type, Line.Type::Item);
                    if not (Line."No." in ['INT000000026', 'INT000000011', 'INT000000017',
                        'INT000000028', 'INT000000022', 'INT000000031', 'INT000000007', 'INT000000016',
                        'NAT00000037', 'NAT00000009', 'NAT00000010', 'NAT00000015'])
                    then
                        Error('Item %1 is outside the approved USD Ocean path.', Line."No.");
                    if Line."No." = 'INT000000026' then
                        HasOceanFreight := true;
                    if CopyStr(Line."No.", 1, 3) = 'INT' then
                        Line.TestField("VAT %", 0)
                    else
                        Line.TestField("VAT %", 16);
                end;
            until Line.Next() = 0;
        if not HasOceanFreight then
            Error('The controlled cancellation path requires an Ocean Freight line.');
    end;

    local procedure ValidateStampedXml(Invoice: Record "Sales Invoice Header"; Operation: Record "MTM MX Cancellation"; IsReplacement: Boolean)
    var
        Document: XmlDocument;
        Ns: XmlNamespaceManager;
        Node: XmlNode;
        Nodes: XmlNodeList;
        Stream: InStream;
        Raw: Text;
        Token: JsonToken;
        Total: Decimal;
        Subtotal: Decimal;
        Vat: Decimal;
        ExpectedUUID: Text;
        RecordRef: RecordRef;
        BlobField: FieldRef;
        TempBlob: Codeunit "Temp Blob";
    begin
        RecordRef.GetTable(Invoice);
        BlobField := FindField(RecordRef, 'Original Document XML');
        BlobField.CalcField();
        TempBlob.FromFieldRef(BlobField);
        TempBlob.CreateInStream(Stream, TextEncoding::Windows);
        Stream.Read(Raw);
        if not XmlDocument.ReadFrom(Raw, Document) then begin
            // Legacy MTM stores the JSON-escaped cfdixml text in this BLOB.
            if not Token.ReadFrom('"' + Raw + '"') then
                Error('Invoice %1 has unreadable stamped XML.', Invoice."No.");
            if not XmlDocument.ReadFrom(Token.AsValue().AsText(), Document) then
                Error('Invoice %1 has invalid stamped XML.', Invoice."No.");
        end;
        Ns.AddNamespace('c', 'http://www.sat.gob.mx/cfd/4');
        Ns.AddNamespace('t', 'http://www.sat.gob.mx/TimbreFiscalDigital');
        ExpectedUUID := Operation."Original UUID";
        if IsReplacement then
            ExpectedUUID := Operation."Replacement UUID";
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/c:Complemento/t:TimbreFiscalDigital/@UUID', ExpectedUUID);
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/c:Emisor/@Rfc', Operation."Issuer RFC");
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/c:Receptor/@Rfc', Operation."Recipient RFC");
        RequireXmlAttribute(Document, Ns, '/c:Comprobante/@Moneda', Operation."Currency Code");
        if not Document.SelectSingleNode('/c:Comprobante/@Total', Ns, Node) then
            Error('Stamped XML total is missing.');
        if not Evaluate(Total, Node.AsXmlAttribute().Value(), 9) or (Total <> Operation.Amount) then
            Error('Stamped XML total differs from the approved total.');
        Invoice.CalcFields(Amount, "Amount Including VAT");
        if not Document.SelectSingleNode('/c:Comprobante/@SubTotal', Ns, Node) then
            Error('Stamped XML subtotal is missing.');
        if not Evaluate(Subtotal, Node.AsXmlAttribute().Value(), 9) or (Subtotal <> Invoice.Amount) then
            Error('Stamped XML subtotal differs from the posted charges.');
        if not Document.SelectSingleNode('/c:Comprobante/c:Impuestos/@TotalImpuestosTrasladados', Ns, Node) then
            Error('Stamped XML VAT total is missing.');
        if not Evaluate(Vat, Node.AsXmlAttribute().Value(), 9) or (Vat <> Invoice."Amount Including VAT" - Invoice.Amount) then
            Error('Stamped XML VAT differs from the posted INT/NAT split.');
        if IsReplacement then begin
            RequireXmlAttribute(Document, Ns, '/c:Comprobante/c:CfdiRelacionados/@TipoRelacion', '04');
            RequireXmlAttribute(Document, Ns, '/c:Comprobante/c:CfdiRelacionados/c:CfdiRelacionado/@UUID', Operation."Original UUID");
            Document.SelectNodes('/c:Comprobante/c:CfdiRelacionados/c:CfdiRelacionado', Ns, Nodes);
            if Nodes.Count() <> 1 then
                Error('Replacement XML must reference exactly one original CFDI.');
        end;
    end;

    local procedure RequireXmlAttribute(Document: XmlDocument; Ns: XmlNamespaceManager; Path: Text; Expected: Text)
    var
        Node: XmlNode;
    begin
        if not Document.SelectSingleNode(Path, Ns, Node) then
            Error('Required stamped XML identity is missing: %1.', Path);
        if UpperCase(Node.AsXmlAttribute().Value()) <> UpperCase(Expected) then
            Error('Stamped XML does not match the approved identity: %1.', Path);
    end;

    local procedure EnsureUnapplied(Invoice: Record "Sales Invoice Header")
    var
        Entry: Record "Cust. Ledger Entry";
    begin
        Invoice.CalcFields(Cancelled);
        Invoice.TestField(Cancelled, false);
        Entry.SetRange("Document Type", Entry."Document Type"::Invoice);
        Entry.SetRange("Document No.", Invoice."No.");
        Entry.SetRange("Customer No.", Invoice."Bill-to Customer No.");
        if Entry.Count() <> 1 then
            Error('Exactly one original invoice ledger entry is required.');
        Entry.FindFirst();
        Entry.CalcFields(Amount, "Remaining Amount");
        Entry.TestField(Open, true);
        if Entry."Remaining Amount" <> Entry.Amount then
            Error('Invoice %1 has an application; cancellation is held.', Invoice."No.");
    end;

    local procedure VerifyAccountingReversal(Operation: Record "MTM MX Cancellation"; Invoice: Record "Sales Invoice Header")
    var
        Entry: Record "Cust. Ledger Entry";
        CreditEntry: Record "Cust. Ledger Entry";
        CancelledDocument: Record "Cancelled Document";
    begin
        Invoice.CalcFields(Cancelled);
        Invoice.TestField(Cancelled, true);
        if not CancelledDocument.FindSalesCancelledInvoice(Invoice."No.") then
            Error('BC corrective credit memo could not be verified.');
        Entry.SetRange("Document Type", Entry."Document Type"::Invoice);
        Entry.SetRange("Document No.", Invoice."No.");
        Entry.SetRange("Customer No.", Operation."Customer No.");
        if Entry.Count() <> 1 then
            Error('Original invoice ledger identity is ambiguous.');
        Entry.FindFirst();
        Entry.CalcFields("Remaining Amount");
        Entry.TestField("Remaining Amount", 0);
        Entry.TestField(Open, false);
        CreditEntry.SetRange("Document Type", CreditEntry."Document Type"::"Credit Memo");
        CreditEntry.SetRange("Document No.", CancelledDocument."Cancelled By Doc. No.");
        CreditEntry.SetRange("Customer No.", Operation."Customer No.");
        if CreditEntry.Count() <> 1 then
            Error('Corrective credit memo ledger identity is ambiguous.');
        CreditEntry.FindFirst();
        CreditEntry.CalcFields(Amount, "Remaining Amount");
        CreditEntry.TestField("Currency Code", Operation."Currency Code");
        CreditEntry.TestField(Amount, -Operation.Amount);
        CreditEntry.TestField("Remaining Amount", 0);
        CreditEntry.TestField(Open, false);
    end;

    local procedure ReadField(Value: Variant; Name: Text): Text
    var
        Ref: RecordRef;
        Field: FieldRef;
    begin
        Ref.GetTable(Value);
        Field := FindField(Ref, Name);
        exit(Format(Field.Value()));
    end;

    local procedure WriteField(Value: Variant; Name: Text; Content: Text)
    var
        Ref: RecordRef;
        Field: FieldRef;
    begin
        Ref.GetTable(Value);
        Field := FindField(Ref, Name);
        Field.Value(Content);
        Ref.Modify();
    end;

    local procedure SetFiscalCanceled(Invoice: Record "Sales Invoice Header")
    var
        Ref: RecordRef;
        Field: FieldRef;
    begin
        Ref.GetTable(Invoice);
        Field := FindField(Ref, 'Electronic Document Status');
        if not Evaluate(Field, 'Canceled') then
            Error('The Mexico fiscal Canceled status is unavailable.');
        Ref.Modify();
    end;

    local procedure FindField(Ref: RecordRef; Name: Text): FieldRef
    var
        Index: Integer;
        Field: FieldRef;
    begin
        for Index := 1 to Ref.FieldCount() do begin
            Field := Ref.FieldIndex(Index);
            if Field.Name() = Name then
                exit(Field);
        end;
        Error('Required Mexico field %1 is unavailable on table %2.', Name, Ref.Number());
    end;
}
