codeunit 71042 "MTM MX Secure Stamp Mgt"
{
    Permissions =
        tabledata "Sales Invoice Header" = rm,
        tabledata "General Ledger Setup" = r,
        tabledata "MTM MX Stamp Attempt" = rim;

    // Credentials remain in BC. No request body, token, certificate or private
    // key is persisted, displayed, returned by an API or written to telemetry.
    [NonDebuggable]
    procedure Stamp(var Invoice: Record "Sales Invoice Header")
    var
        Attempt: Record "MTM MX Stamp Attempt";
        Request: HttpRequestMessage;
        FiscalTime: Text;
    begin
        ValidateInvoiceScope(Invoice, false);
        Attempt.LockTable();
        if Attempt.Get(Invoice."No.") then
            Error('Invoice %1 already has a controlled stamp attempt (%2). Review the PAC result before any retry.', Invoice."No.", Attempt.Outcome);
        FiscalTime := MexicoFiscalTimestamp(CurrentDateTime());
        PrepareRequest(Invoice, FiscalTime, Request);
        Attempt.Init();
        Attempt."Invoice No." := Invoice."No.";
        Attempt."Invoice SystemId" := Invoice.SystemId;
        Attempt."Attempted At UTC" := CurrentDateTime();
        Attempt."Fiscal Timestamp" := CopyStr(FiscalTime, 1, 19);
        Attempt."Approved Total" := Invoice."Amount Including VAT";
        Attempt."Attempt Count" := 1;
        Attempt.Outcome := Attempt.Outcome::Unknown;
        Attempt."Error Code" := 'REQUEST_IN_FLIGHT';
        Attempt.Diagnostic := 'The PAC request may have executed. Do not retry without reconciliation.';
        Attempt.Insert(true);
        Commit();
        SubmitPreparedRequest(Invoice, Attempt, Request);
    end;

    [NonDebuggable]
    procedure RetryConfirmedFxRejection(var Invoice: Record "Sales Invoice Header"; ExpectedAttemptAt: DateTime; ExpectedTotal: Decimal; ExpectedDueDate: Date; ExpectedReference: Text)
    var
        Attempt: Record "MTM MX Stamp Attempt";
        Request: HttpRequestMessage;
        FiscalTime: Text;
    begin
        ValidateInvoiceScope(Invoice, true);
        Attempt.LockTable();
        if not Attempt.Get(Invoice."No.") then
            Error('A prior definitive FX rejection is required for this explicit retry.');
        if not Attempt.IsConfirmedFxRejection(ExpectedAttemptAt, ExpectedTotal) then
            Error('The prior PAC attempt is not the exact confirmed FX-format rejection approved for retry.');
        if Attempt."Invoice SystemId" <> Invoice.SystemId then
            Error('The rejected attempt belongs to a different posted invoice.');
        if (ExpectedTotal <> Invoice."Amount Including VAT") or
            (ExpectedDueDate = 0D) or (ExpectedDueDate <> Invoice."Due Date") or
            (ExpectedReference = '') or (ExpectedReference <> Invoice."External Document No.") then
            Error('The posted invoice reference, approved total or due date changed before the FX retry.');
        RequireFieldValue(Invoice, 'Error Code', '101');
        RequireFieldValue(Invoice, 'Electronic Document Status', 'Stamp Request Error');
        FiscalTime := MexicoFiscalTimestamp(CurrentDateTime());
        PrepareRequest(Invoice, FiscalTime, Request);
        // Retain the definitive rejection; never delete an attempt to enable a
        // retry. A second Unknown marker is committed before the only PAC POST.
        Attempt."Previous Attempted At UTC" := Attempt."Attempted At UTC";
        Attempt."Previous Error Code" := Attempt."Error Code";
        Attempt."Previous Diagnostic" := Attempt.Diagnostic;
        Attempt."Previous HTTP Status" := Attempt."HTTP Status";
        Attempt."Attempt Count" := 2;
        Attempt."Attempted At UTC" := CurrentDateTime();
        Attempt."Fiscal Timestamp" := CopyStr(FiscalTime, 1, 19);
        Attempt."HTTP Status" := 0;
        Attempt.Outcome := Attempt.Outcome::Unknown;
        Attempt."Error Code" := 'REQUEST_IN_FLIGHT';
        Attempt.Diagnostic := 'Explicit retry of the confirmed FX-format rejection is in flight; no further automatic retry is allowed.';
        Attempt.Modify(true);
        Commit();
        SubmitPreparedRequest(Invoice, Attempt, Request);
    end;

    [NonDebuggable]
    local procedure SubmitPreparedRequest(var Invoice: Record "Sales Invoice Header"; var Attempt: Record "MTM MX Stamp Attempt"; var Request: HttpRequestMessage)
    var
        Response: HttpResponseMessage;
        Client: HttpClient;
        ResponseBody: Text;
        Result: JsonObject;
        StampUUID: Text;
        InvoiceRef: RecordRef;
    begin
        Client.Timeout(60000);
        // The durable Unknown marker precedes the only mutating HTTP request.
        if not Client.Send(Request, Response) then begin
            SaveUnknown(Attempt, 'TRANSPORT_UNKNOWN', 'No definitive PAC response was received.');
            exit;
        end;
        Attempt."HTTP Status" := Response.HttpStatusCode();
        if not Response.Content.ReadAs(ResponseBody) then begin
            SaveUnknown(Attempt, 'UNREADABLE_RESPONSE', 'The PAC response could not be read.');
            exit;
        end;
        if not ReadProviderResult(ResponseBody, Result, StampUUID) then begin
            SaveUnknown(Attempt, 'INVALID_RESPONSE', 'The PAC response was not recognized; reconcile the invoice in the PAC before retrying.');
            exit;
        end;
        Attempt."Fiscal UUID" := CopyStr(StampUUID, 1, MaxStrLen(Attempt."Fiscal UUID"));
        Attempt."Error Code" := CopyStr(ProviderCode(ResponseBody), 1, MaxStrLen(Attempt."Error Code"));
        Attempt.Diagnostic := CopyStr(RedactConfiguredSecrets(RawProviderDiagnostic(ResponseBody)), 1, MaxStrLen(Attempt.Diagnostic));
        if (Attempt."Error Code" <> '000') and (StampUUID = '') then begin
            Attempt.Outcome := Attempt.Outcome::Rejected;
            Attempt.Modify(true);
            Invoice.Get(Invoice."No.");
            InvoiceRef.GetTable(Invoice);
            WriteOption(InvoiceRef, 'Electronic Document Status', 'Stamp Request Error');
            WriteText(InvoiceRef, 'Error Code', Attempt."Error Code");
            WriteText(InvoiceRef, 'Error Description', Attempt.Diagnostic);
            InvoiceRef.Modify(true);
            Invoice.Get(Invoice."No.");
            Commit();
            exit;
        end;
        if not Response.IsSuccessStatusCode() or (Attempt."Error Code" <> '000') or (StampUUID = '') then begin
            SaveUnknown(Attempt, 'AMBIGUOUS_RESPONSE', 'PAC response requires reconciliation; automatic retries are disabled.');
            exit;
        end;
        ClearLastError();
        if not TryValidateStampedResult(Invoice, Result, StampUUID) then begin
            SaveUnknown(Attempt, 'STAMP_VALIDATION_FAILED', SafeDiagnostic(GetLastErrorText()));
            exit;
        end;
        PersistStampedInvoice(Invoice, Result, StampUUID);
        Attempt.Outcome := Attempt.Outcome::Stamped;
        Attempt."Error Code" := '000';
        Attempt.Diagnostic := 'Stamped CFDI identity, amounts, replacement relation and PDF verified. Customer delivery remains a separate action.';
        Attempt.Modify(true);
        Commit();
    end;

    procedure GetDiagnostic(InvoiceNo: Code[20]): Text
    var
        Attempt: Record "MTM MX Stamp Attempt";
    begin
        if not Attempt.Get(InvoiceNo) then
            exit('');
        exit('Outcome=' + Format(Attempt.Outcome) + '|HTTP=' + Format(Attempt."HTTP Status") +
            '|Code=' + Attempt."Error Code" + '|AttemptUTC=' + Format(Attempt."Attempted At UTC", 0, 9) +
            '|FiscalTimestamp=' + Attempt."Fiscal Timestamp" + '|UUID=' + Attempt."Fiscal UUID" +
            '|AttemptCount=' + Format(Attempt."Attempt Count") + '|PriorAttemptUTC=' + Format(Attempt."Previous Attempted At UTC", 0, 9) +
            '|PriorCode=' + Attempt."Previous Error Code" + '|Detail=' + Attempt.Diagnostic);
    end;

    procedure MexicoFiscalTimestamp(UtcDateTime: DateTime): Text
    begin
        // XML format 9 always formats DateTime in UTC. Mexico City has used
        // UTC-6 year round since 2022; subtract once, independent of session TZ.
        exit(CopyStr(Format(UtcDateTime - 21600000, 0, 9), 1, 19));
    end;

    procedure FormatPacExchangeRate(Value: Decimal): Text
    var
        Invariant: Text;
        Point: Integer;
        DecimalCount: Integer;
    begin
        if Value <= 0 then
            Error('A positive approved exchange rate is required.');
        Invariant := Format(Round(Value, 0.0001, '='), 0, 9);
        Point := StrPos(Invariant, '.');
        if Point = 0 then
            exit(Invariant + '.0000');
        DecimalCount := StrLen(Invariant) - Point;
        if DecimalCount > 4 then
            Error('The approved exchange rate has invalid precision.');
        exit(Invariant + PadStr('', 4 - DecimalCount, '0'));
    end;

    local procedure ValidateInvoiceScope(var Invoice: Record "Sales Invoice Header"; AllowConfirmedFxRetry: Boolean)
    var
        Line: Record "Sales Invoice Line";
        VATGroup: Record "VAT Product Posting Group";
        VATRate: Decimal;
        HasOcean: Boolean;
        Status: Text;
    begin
        if CompanyName() <> 'MTM_MX_PROD' then
            Error('The controlled Mexico stamper is restricted to MTM_MX_PROD.');
        Invoice.Get(Invoice."No.");
        Invoice.CalcFields(Cancelled, Amount, "Amount Including VAT");
        Invoice.TestField(Cancelled, false);
        Invoice.TestField("Sell-to Customer No.", 'C00067');
        Invoice.TestField("Bill-to Customer No.", 'C00067');
        Invoice.TestField("Currency Code", 'USD');
        RequireFieldValue(Invoice, 'Fiscal Invoice Number PAC', '');
        if Invoice."Amount Including VAT" <= 0 then
            Error('A positive posted invoice total is required.');
        Status := ReadText(Invoice, 'Electronic Document Status');
        if (DelChr(Status, '=', ' ') <> '') and (Status <> 'Stamp Request Error') then
            Error('Invoice has an unsupported electronic document status.');
        if Status = 'Stamp Request Error' then
            if AllowConfirmedFxRetry then
                RequireFieldValue(Invoice, 'Error Code', '101')
            else
                RequireFieldValue(Invoice, 'Error Code', '017');
        Line.SetRange("Document No.", Invoice."No.");
        if Line.FindSet() then
            repeat
                if Line.Type = Line.Type::Item then begin
                    if not (Line."No." in ['INT000000026', 'INT000000011', 'INT000000017', 'INT000000022',
                        'INT000000007', 'NAT00000037', 'NAT00000009', 'NAT00000010', 'NAT00000030', 'NAT00000015']) then
                        Error('Invoice has a charge item outside the approved Mexico Ocean mapping.');
                    if Line."No." = 'INT000000026' then
                        HasOcean := true;
                    Line.TestField("Description XL");
                    Line.TestField(Quantity, 1);
                    if (Line."Line Discount Amount" <> 0) or (Line."Inv. Discount Amount" <> 0) then
                        Error('Discounted invoice lines are outside this controlled stamp path.');
                    if CopyStr(Line."No.", 1, 3) = 'NAT' then begin
                        Line.TestField("VAT %", 16);
                        VATGroup.Get(Line."VAT Prod. Posting Group");
                        VATGroup.TestField("Calcula impuesto - SAT", true);
                        if NormalizeSatTaxCode(Format(VATGroup."Impuesto - SAT")) <> '002' then
                            Error('National charges require the native SAT IVA tax code.');
                        if NormalizePacTaxFactor(Format(VATGroup."Factor - SAT")) <> 1 then
                            Error('National IVA charges require the native PAC Tasa factor.');
                        if NormalizePacTaxType(Format(VATGroup."Tipo de impuesto - SAT")) <> 1 then
                            Error('National IVA charges require transferred tax.');
                        if not Evaluate(VATRate, VATGroup."Tasa - SAT", 9) or (VATRate <> 0.16) then
                            Error('National charges require the native SAT IVA rate of 16 percent.');
                    end else
                        Line.TestField("VAT %", 0);
                end else
                    if Line.Amount <> 0 then
                        Error('All monetary invoice lines must be approved charge items.');
            until Line.Next() = 0;
        if not HasOcean then
            Error('The controlled stamp path requires an Ocean Freight line.');
    end;

    [NonDebuggable]
    local procedure PrepareRequest(Invoice: Record "Sales Invoice Header"; FiscalTime: Text; var Request: HttpRequestMessage)
    var
        Setup: Record "General Ledger Setup";
        Company: Record "Company Information";
        Customer: Record Customer;
        PaymentMethod: Record "Payment Method";
        PaymentTerms: Record "Payment Terms";
        Body: JsonObject;
        General: JsonObject;
        Header: JsonObject;
        Issuer: JsonObject;
        Recipient: JsonObject;
        Address: JsonObject;
        Addresses: JsonArray;
        Concepts: JsonArray;
        Content: HttpContent;
        Headers: HttpHeaders;
        Stream: InStream;
        Certificate: Text;
        PrivateKey: Text;
        Payload: Text;
        Logo: Text;
        Base64Convert: Codeunit "Base64 Convert";
        RelatedUUID: Text;
        RFC: Text;
    begin
        Setup.Get();
        Setup.TestField(Token);
        Setup.TestField("CSD Password");
        if CopyStr(LowerCase(Setup."Url Pac"), 1, 8) <> 'https://' then
            Error('The PAC URL must use HTTPS.');
        Setup.CalcFields(Certificado, "Llave privada");
        if not Setup.Certificado.HasValue() or not Setup."Llave privada".HasValue() then
            Error('Mexico signing material must be configured.');
        Setup.Certificado.CreateInStream(Stream, TextEncoding::Windows);
        Stream.Read(Certificate);
        Setup."Llave privada".CreateInStream(Stream, TextEncoding::Windows);
        Stream.Read(PrivateKey);
        if (Certificate = '') or (PrivateKey = '') then
            Error('Mexico signing material is empty.');
        Company.Get();
        // This adapter formats the civil time of the configured Mexico City
        // issuer; Mexico border regions use different offsets.
        RequireFieldValue(Company, 'SAT Postal Code', '11000');
        Customer.Get(Invoice."Sell-to Customer No.");
        PaymentMethod.Get(Invoice."Payment Method Code");
        PaymentTerms.Get(Invoice."Payment Terms Code");
        Company.CalcFields(Picture);
        if Company.Picture.HasValue() then begin
            Company.Picture.CreateInStream(Stream);
            Logo := Base64Convert.ToBase64(Stream);
        end;
        General.Add('Version', '4.0');
        General.Add('CSD', Certificate);
        General.Add('LlavePrivada', PrivateKey);
        General.Add('CSDPassword', Setup."CSD Password");
        General.Add('GeneraPDF', true);
        General.Add('Logotipo', Logo);
        General.Add('CFDI', 'Factura');
        General.Add('OpcionDecimales', '1');
        General.Add('NumeroDecimales', '2');
        General.Add('TipoCFDI', 'Ingreso');
        General.Add('EnviaEmail', false);
        General.Add('ReceptorEmail', '');
        General.Add('ReceptorEmailCC', '');
        General.Add('ReceptorEmailCCO', '');
        General.Add('EmailMensaje', '');
        Body.Add('DatosGenerales', General);
        Issuer.Add('RFC', ReadText(Company, 'RFC Number'));
        Issuer.Add('NombreRazonSocial', Company.Name);
        Issuer.Add('RegimenFiscal', ReadText(Company, 'SAT Tax Regime Classification'));
        Address.Add('Calle', Company.Address);
        Address.Add('NumeroExterior', Company."Numero Exterior");
        Address.Add('NumeroInterior', Company."Numero Interior");
        Address.Add('Colonia', Company."Address 2");
        Address.Add('Localidad', Company.Localidad);
        Address.Add('Municipio', Company.Municipio);
        Address.Add('Estado', Company.Estado);
        Address.Add('Pais', Company.Pais);
        Address.Add('CodigoPostal', ReadText(Company, 'SAT Postal Code'));
        Addresses.Add(Address);
        Issuer.Add('Direccion', Addresses);
        Header.Add('Emisor', Issuer);
        RFC := ReadText(Customer, 'RFC No.');
        if RFC = '' then
            RFC := Customer."VAT Registration No.";
        Recipient.Add('RFC', RFC);
        Recipient.Add('NombreRazonSocial', ReadText(Customer, 'CFDI Customer Name'));
        Recipient.Add('UsoCFDI', ReadText(Customer, 'CFDI Purpose'));
        Recipient.Add('RegimenFiscal', ReadText(Customer, 'SAT Tax Regime Classification'));
        Clear(Address);
        Address.Add('Calle', Customer.Address);
        Address.Add('NumeroExterior', Customer."Numero Exterior");
        Address.Add('NumeroInterior', Customer."Numero Interior");
        Address.Add('Colonia', Customer."Address 2");
        Address.Add('Localidad', Customer.Localidad);
        Address.Add('Municipio', Customer.Municipio);
        Address.Add('Estado', Customer.Estado);
        Address.Add('Pais', Customer.Pais);
        Address.Add('CodigoPostal', Customer."Post Code");
        if RFC = 'XEXX010101000' then begin
            Recipient.Add('TaxId', Customer."Registration Number");
            Address.Add('ClavePais', Customer.ClavePais);
        end;
        Recipient.Add('Direccion', Address);
        Header.Add('Receptor', Recipient);
        RelatedUUID := RelatedInvoiceUUID(Invoice);
        Header.Add('CFDIsRelacionados', RelatedUUID);
        Header.Add('TipoRelacion', ReadText(Invoice, 'CFDI Relation'));
        Header.Add('Fecha', FiscalTime);
        Header.Add('Serie', DelChr(Invoice."No.", '=', '0123456789'));
        Header.Add('Folio', DelChr(Invoice."No.", '=', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'));
        Header.Add('MetodoPago', ReadText(PaymentTerms, 'SAT Payment Term'));
        Header.Add('FormaPago', ReadText(PaymentMethod, 'SAT Method of Payment'));
        Header.Add('Moneda', Invoice."Currency Code");
        if Invoice."Currency Factor" = 0 then
            Error('A Mexico USD invoice requires its approved currency factor.');
        Header.Add('TipoCambio', FormatPacExchangeRate(1 / Invoice."Currency Factor"));
        Header.Add('LugarExpedicion', ReadText(Company, 'SAT Postal Code'));
        Header.Add('observaciones', Invoice.Observaciones + ' | Embarque: ' + Invoice."External Document No." +
            ' | Vencimiento: ' + Format(Invoice."Due Date", 0, 9));
        Header.Add('SubTotal', Format(Invoice.Amount, 0, 9));
        Header.Add('Total', Format(Invoice."Amount Including VAT", 0, 9));
        Body.Add('Encabezado', Header);
        BuildConcepts(Invoice, Concepts);
        Body.Add('Conceptos', Concepts);
        Body.WriteTo(Payload);
        Content.WriteFrom(Payload);
        Content.GetHeaders(Headers);
        Headers.Clear();
        Headers.Add('Content-Type', 'application/json');
        Request.Content := Content;
        Request.Method := 'POST';
        Request.SetRequestUri(DelChr(Setup."Url Pac", '>', '/') + '/servicios/timbrar/json');
        Request.GetHeaders(Headers);
        Headers.Add('Authorization', 'Bearer ' + Setup.Token);
    end;

    local procedure BuildConcepts(Invoice: Record "Sales Invoice Header"; var Concepts: JsonArray)
    var
        Line: Record "Sales Invoice Line";
        Item: Record Item;
        Unit: Record "Unit of Measure";
        VATGroup: Record "VAT Product Posting Group";
        Concept: JsonObject;
        Tax: JsonObject;
        Taxes: JsonArray;
    begin
        Line.SetRange("Document No.", Invoice."No.");
        Line.SetRange(Type, Line.Type::Item);
        if Line.FindSet() then
            repeat
                Item.Get(Line."No.");
                Unit.Get(Item."Base Unit of Measure");
                VATGroup.Get(Line."VAT Prod. Posting Group");
                Clear(Concept);
                Clear(Taxes);
                Clear(Tax);
                Concept.Add('Cantidad', Format(Line.Quantity, 0, 9));
                Concept.Add('CodigoUnidad', ReadText(Unit, 'SAT UofM Classification'));
                Concept.Add('Unidad', Unit.Description);
                Concept.Add('CodigoProducto', ReadText(Item, 'SAT Item Classification'));
                Concept.Add('Producto', Line."Description XL");
                Concept.Add('PrecioUnitario', Format(Line."Unit Price", 0, 9));
                Concept.Add('Importe', Format(Line.Amount, 0, 9));
                Concept.Add('ObjetoDeImpuesto', VATGroup."Objeto de impuesto");
                if VATGroup."Calcula impuesto - SAT" then begin
                    // PAC JSON uses integer catalogue IDs, whereas its final
                    // SAT XML uses 002/Tasa. Do not interchange those formats.
                    Tax.Add('TipoImpuesto', NormalizePacTaxType(Format(VATGroup."Tipo de impuesto - SAT")));
                    Tax.Add('Impuesto', NormalizePacTaxId(Format(VATGroup."Impuesto - SAT")));
                    Tax.Add('Factor', NormalizePacTaxFactor(Format(VATGroup."Factor - SAT")));
                    Tax.Add('Base', Format(Line.Amount, 0, 9));
                    Tax.Add('Tasa', VATGroup."Tasa - SAT");
                    Tax.Add('ImpuestoImporte', Format(Round(Line."Amount Including VAT", 0.01) - Round(Line."VAT Base Amount", 0.01), 0, 9));
                    Taxes.Add(Tax);
                    Concept.Add('Impuestos', Taxes);
                end;
                Concepts.Add(Concept);
            until Line.Next() = 0;
    end;

    local procedure RelatedInvoiceUUID(Invoice: Record "Sales Invoice Header"): Text
    var
        Relation: RecordRef;
        Field: FieldRef;
    begin
        Relation.Open(27006);
        Field := FindField(Relation, 'Document No.');
        Field.SetRange(Invoice."No.");
        if Relation.IsEmpty() then begin
            if ReadText(Invoice, 'CFDI Relation') = '04' then
                Error('A replacement invoice requires its original fiscal UUID relation.');
            exit('');
        end;
        if Relation.Count() <> 1 then
            Error('Exactly one original CFDI relation is required.');
        Relation.FindFirst();
        Field := FindField(Relation, 'Fiscal Invoice Number PAC');
        if Format(Field.Value()) = '' then
            Error('The original CFDI fiscal UUID is missing.');
        RequireFieldValue(Invoice, 'CFDI Relation', '04');
        exit(Format(Field.Value()));
    end;

    procedure NormalizeSatTaxCode(Value: Text): Text
    begin
        // Legacy MTM tax setup stores IVA as either 2 or 002. The SAT XML
        // catalogue requires the three-character code; leave setup untouched.
        case DelChr(Value, '<>', ' ') of
            '1', '01', '001': exit('001');
            '2', '02', '002': exit('002');
            '3', '03', '003': exit('003');
            else
                Error('The native SAT tax code is outside the supported catalogue.');
        end;
    end;

    procedure NormalizePacTaxId(Value: Text): Integer
    begin
        case NormalizeSatTaxCode(Value) of
            '001': exit(1);
            '002': exit(2);
            '003': exit(3);
        end;
    end;

    procedure NormalizePacTaxFactor(Value: Text): Integer
    begin
        case UpperCase(DelChr(Value, '<>', ' ')) of
            '1', 'TASA': exit(1);
            '2', 'CUOTA': exit(2);
            '3', 'EXENTO': exit(3);
            else
                Error('The native PAC tax factor is outside the documented catalogue.');
        end;
    end;

    procedure NormalizePacTaxType(Value: Text): Integer
    begin
        case UpperCase(DelChr(Value, '<>', ' ')) of
            '1', 'TRASLADADO', 'TRASLADADOS': exit(1);
            '2', 'RETENIDO', 'RETENIDOS': exit(2);
            else
                Error('The native PAC tax type is outside the documented catalogue.');
        end;
    end;

    procedure GetNativeTaxReadiness(InvoiceNo: Code[20]): Text
    var
        Invoice: Record "Sales Invoice Header";
        Line: Record "Sales Invoice Line";
        VATGroup: Record "VAT Product Posting Group";
        Seen: List of [Text];
        Summary: Text;
    begin
        if CompanyName() <> 'MTM_MX_PROD' then
            exit('');
        if not Invoice.Get(InvoiceNo) or (Invoice."Sell-to Customer No." <> 'C00067') then
            exit('');
        Line.SetRange("Document No.", InvoiceNo);
        Line.SetRange(Type, Line.Type::Item);
        if Line.FindSet() then
            repeat
                if not Seen.Contains(Line."VAT Prod. Posting Group") then begin
                    Seen.Add(Line."VAT Prod. Posting Group");
                    VATGroup.Get(Line."VAT Prod. Posting Group");
                    if Summary <> '' then
                        Summary += ';';
                    Summary += 'Group=' + VATGroup.Code + '|Calculate=' + Format(VATGroup."Calcula impuesto - SAT") +
                        '|Object=' + Format(VATGroup."Objeto de impuesto") + '|Type=' + Format(VATGroup."Tipo de impuesto - SAT") +
                        '|Tax=' + Format(VATGroup."Impuesto - SAT") + '|Factor=' + Format(VATGroup."Factor - SAT") +
                        '|Rate=' + Format(VATGroup."Tasa - SAT");
                end;
            until Line.Next() = 0;
        exit(CopyStr(Summary, 1, 2048));
    end;

    procedure ReadProviderResult(Body: Text; var Result: JsonObject; var FiscalUUID: Text): Boolean
    var
        Root: JsonObject;
        Token: JsonToken;
        Stamped: JsonObject;
    begin
        Clear(Result);
        FiscalUUID := '';
        if not Root.ReadFrom(Body) then
            exit(false);
        if ProviderCode(Body) = '' then
            exit(false);
        if Root.Get('cfdiTimbrado', Token) and Token.IsObject() then begin
            Stamped := Token.AsObject();
            if Stamped.Get('respuesta', Token) and Token.IsObject() then begin
                Result := Token.AsObject();
                FiscalUUID := JsonText(Result, 'uuid');
            end;
        end;
        exit(true);
    end;

    procedure ProviderCode(Body: Text): Text
    var
        Root: JsonObject;
        Token: JsonToken;
        Code: Text;
    begin
        if not Root.ReadFrom(Body) then
            exit('');
        if Root.Get('estatus', Token) and Token.IsObject() then
            Code := JsonText(Token.AsObject(), 'codigo')
        else
            Code := JsonText(Root, 'codigo');
        if (StrLen(Code) <> 3) or (DelChr(Code, '=', '0123456789') <> '') then
            exit('');
        exit(Code);
    end;

    procedure ProviderDiagnostic(Body: Text): Text
    begin
        exit(SafeDiagnostic(RawProviderDiagnostic(Body)));
    end;

    local procedure RawProviderDiagnostic(Body: Text): Text
    var
        Root: JsonObject;
        Token: JsonToken;
        Status: JsonObject;
    begin
        if not Root.ReadFrom(Body) then
            exit('Invalid PAC JSON response.');
        Status := Root;
        if Root.Get('estatus', Token) and Token.IsObject() then
            Status := Token.AsObject();
        exit(JsonText(Status, 'descripcion') + ' | ' + JsonText(Status, 'informacionTecnica'));
    end;

    procedure SafeDiagnostic(Value: Text): Text
    var
        Lower: Text;
        Index: Integer;
        OpaqueLength: Integer;
    begin
        Lower := LowerCase(Value);
        if (StrPos(Lower, 'llave') > 0) or (StrPos(Lower, 'password') > 0) or
            (StrPos(Lower, 'csd') > 0) or (StrPos(Lower, 'token') > 0) or
            (StrPos(Lower, 'authorization') > 0) or (StrPos(Lower, 'privatekey') > 0) or
            (StrPos(Lower, 'certificado') > 0) or (StrPos(Lower, '-----begin') > 0) then
            exit('Provider diagnostic was withheld because it references signing credentials.');
        // A provider may echo a bare key/token without its JSON property name.
        // Such opaque material is never useful as a human-readable rejection.
        for Index := 1 to StrLen(Value) do begin
            if StrPos('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-.', CopyStr(Value, Index, 1)) > 0 then
                OpaqueLength += 1
            else
                OpaqueLength := 0;
            if OpaqueLength >= 80 then
                exit('Provider diagnostic was withheld because it contains opaque credential-like data.');
        end;
        exit(CopyStr(Value, 1, 2048));
    end;

    [NonDebuggable]
    local procedure RedactConfiguredSecrets(Value: Text): Text
    var
        Setup: Record "General Ledger Setup";
        Stream: InStream;
        Secret: Text;
    begin
        Setup.Get();
        if Setup.Token <> '' then
            Value := Value.Replace(Setup.Token, '[redacted]');
        if Setup."CSD Password" <> '' then
            Value := Value.Replace(Setup."CSD Password", '[redacted]');
        Setup.CalcFields(Certificado, "Llave privada");
        if Setup.Certificado.HasValue() then begin
            Setup.Certificado.CreateInStream(Stream, TextEncoding::Windows);
            Stream.Read(Secret);
            if Secret <> '' then
                Value := Value.Replace(Secret, '[redacted]');
        end;
        if Setup."Llave privada".HasValue() then begin
            Setup."Llave privada".CreateInStream(Stream, TextEncoding::Windows);
            Stream.Read(Secret);
            if Secret <> '' then
                Value := Value.Replace(Secret, '[redacted]');
        end;
        exit(SafeDiagnostic(Value));
    end;

    [TryFunction]
    local procedure TryValidateStampedResult(Invoice: Record "Sales Invoice Header"; Result: JsonObject; FiscalUUID: Text)
    var
        Document: XmlDocument;
        Ns: XmlNamespaceManager;
        Node: XmlNode;
        Nodes: XmlNodeList;
        Company: Record "Company Information";
        Customer: Record Customer;
        PDF: Codeunit "Temp Blob";
        Base64Convert: Codeunit "Base64 Convert";
        Stream: InStream;
        Out: OutStream;
        PDFHeader: Text[5];
        RFC: Text;
        OriginalUUID: Text;
    begin
        if (StrLen(FiscalUUID) <> 36) or (JsonText(Result, 'idVersionTimbrado') = '') then
            Error('The PAC did not return a complete fiscal stamp.');
        if not XmlDocument.ReadFrom(JsonText(Result, 'cfdixml'), Document) then
            Error('The PAC returned invalid stamped XML.');
        Company.Get();
        Customer.Get(Invoice."Sell-to Customer No.");
        RFC := ReadText(Customer, 'RFC No.');
        if RFC = '' then
            RFC := Customer."VAT Registration No.";
        Ns.AddNamespace('c', 'http://www.sat.gob.mx/cfd/4');
        Ns.AddNamespace('t', 'http://www.sat.gob.mx/TimbreFiscalDigital');
        RequireAttribute(Document, Ns, '/c:Comprobante/@Version', '4.0');
        RequireAttribute(Document, Ns, '/c:Comprobante/@TipoDeComprobante', 'I');
        RequireAttribute(Document, Ns, '/c:Comprobante/@Serie', DelChr(Invoice."No.", '=', '0123456789'));
        RequireAttribute(Document, Ns, '/c:Comprobante/@Folio', DelChr(Invoice."No.", '=', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'));
        RequireAttribute(Document, Ns, '/c:Comprobante/@Moneda', Invoice."Currency Code");
        RequireAttribute(Document, Ns, '/c:Comprobante/c:Emisor/@Rfc', ReadText(Company, 'RFC Number'));
        RequireAttribute(Document, Ns, '/c:Comprobante/c:Receptor/@Rfc', RFC);
        RequireAttribute(Document, Ns, '/c:Comprobante/c:Complemento/t:TimbreFiscalDigital/@UUID', FiscalUUID);
        RequireAmount(Document, Ns, '/c:Comprobante/@SubTotal', Invoice.Amount);
        RequireAmount(Document, Ns, '/c:Comprobante/@Total', Invoice."Amount Including VAT");
        RequireAmount(Document, Ns, '/c:Comprobante/c:Impuestos/@TotalImpuestosTrasladados', Invoice."Amount Including VAT" - Invoice.Amount);
        OriginalUUID := RelatedInvoiceUUID(Invoice);
        if OriginalUUID <> '' then begin
            RequireAttribute(Document, Ns, '/c:Comprobante/c:CfdiRelacionados/@TipoRelacion', '04');
            RequireAttribute(Document, Ns, '/c:Comprobante/c:CfdiRelacionados/c:CfdiRelacionado/@UUID', OriginalUUID);
            Document.SelectNodes('/c:Comprobante/c:CfdiRelacionados/c:CfdiRelacionado', Ns, Nodes);
            if Nodes.Count() <> 1 then
                Error('Stamped replacement must refer to exactly one original UUID.');
        end;
        if JsonText(Result, 'pdf') = '' then
            Error('PAC PDF is missing.');
        PDF.CreateOutStream(Out);
        Base64Convert.FromBase64(JsonText(Result, 'pdf'), Out);
        PDF.CreateInStream(Stream);
        if (Stream.ReadText(PDFHeader, 5) <> 5) or (PDFHeader <> '%PDF-') then
            Error('PAC PDF has an invalid header.');
    end;

    local procedure PersistStampedInvoice(var Invoice: Record "Sales Invoice Header"; Result: JsonObject; FiscalUUID: Text)
    var
        InvoiceRef: RecordRef;
        Field: FieldRef;
    begin
        Invoice.Get(Invoice."No.");
        RequireFieldValue(Invoice, 'Fiscal Invoice Number PAC', '');
        InvoiceRef.GetTable(Invoice);
        WriteText(InvoiceRef, 'Date/Time Stamped', JsonText(Result, 'fecha'));
        WriteText(InvoiceRef, 'Fiscal Invoice Number PAC', FiscalUUID);
        WriteText(InvoiceRef, 'Certificate Serial No.', JsonText(Result, 'noCertificado'));
        Field := FindField(InvoiceRef, 'Electronic Document Sent');
        Field.Value := true;
        WriteOption(InvoiceRef, 'Electronic Document Status', 'Stamp Received');
        WriteBlob(InvoiceRef, 'Digital Stamp PAC', JsonText(Result, 'selloCFD'));
        WriteBlob(InvoiceRef, 'Digital Stamp SAT', JsonText(Result, 'selloSAT'));
        WriteBlob(InvoiceRef, 'Original String', JsonText(Result, 'cadenaOriginal'));
        WriteBlob(InvoiceRef, 'Original Document XML', JsonText(Result, 'cfdixml'));
        WriteBlob(InvoiceRef, 'PDF_64', JsonText(Result, 'pdf'));
        WriteText(InvoiceRef, 'Error Code', '');
        WriteText(InvoiceRef, 'Error Description', '');
        InvoiceRef.Modify(true);
        Invoice.Get(Invoice."No.");
    end;

    local procedure SaveUnknown(var Attempt: Record "MTM MX Stamp Attempt"; Code: Text; Detail: Text)
    begin
        Attempt.Outcome := Attempt.Outcome::Unknown;
        Attempt."Error Code" := CopyStr(Code, 1, MaxStrLen(Attempt."Error Code"));
        Attempt.Diagnostic := CopyStr(SafeDiagnostic(Detail), 1, MaxStrLen(Attempt.Diagnostic));
        Attempt.Modify(true);
        Commit();
    end;

    local procedure RequireAttribute(Document: XmlDocument; Ns: XmlNamespaceManager; Path: Text; Expected: Text)
    var
        Node: XmlNode;
    begin
        if not Document.SelectSingleNode(Path, Ns, Node) then
            Error('Required stamped XML identity is missing: %1.', Path);
        if UpperCase(Node.AsXmlAttribute().Value()) <> UpperCase(Expected) then
            Error('Stamped XML identity differs from the approved invoice: %1.', Path);
    end;

    local procedure RequireAmount(Document: XmlDocument; Ns: XmlNamespaceManager; Path: Text; Expected: Decimal)
    var
        Node: XmlNode;
        Amount: Decimal;
    begin
        if not Document.SelectSingleNode(Path, Ns, Node) then
            Error('Required stamped XML amount is missing: %1.', Path);
        if not Evaluate(Amount, Node.AsXmlAttribute().Value(), 9) or (Amount <> Expected) then
            Error('Stamped XML amount differs from the approved posted invoice: %1.', Path);
    end;

    local procedure JsonText(Object: JsonObject; Name: Text): Text
    var
        Token: JsonToken;
    begin
        if not Object.Get(Name, Token) or not Token.IsValue() then
            exit('');
        if Token.AsValue().IsNull() then
            exit('');
        exit(Token.AsValue().AsText());
    end;

    local procedure ReadText(Value: Variant; Name: Text): Text
    var
        RecRef: RecordRef;
        Field: FieldRef;
    begin
        RecRef.GetTable(Value);
        Field := FindField(RecRef, Name);
        exit(Format(Field.Value()));
    end;

    local procedure RequireFieldValue(Value: Variant; Name: Text; Expected: Text)
    begin
        if ReadText(Value, Name) <> Expected then
            Error('Required Mexico field does not match: %1.', Name);
    end;

    local procedure WriteText(var RecRef: RecordRef; Name: Text; Value: Text)
    var
        Field: FieldRef;
    begin
        Field := FindField(RecRef, Name);
        Field.Value := CopyStr(Value, 1, Field.Length());
    end;

    local procedure WriteOption(var RecRef: RecordRef; Name: Text; Value: Text)
    var
        Field: FieldRef;
    begin
        Field := FindField(RecRef, Name);
        Evaluate(Field, Value);
    end;

    local procedure WriteBlob(var RecRef: RecordRef; Name: Text; Value: Text)
    var
        Field: FieldRef;
        Blob: Codeunit "Temp Blob";
        Out: OutStream;
    begin
        Field := FindField(RecRef, Name);
        Blob.CreateOutStream(Out, TextEncoding::Windows);
        Out.WriteText(Value);
        Blob.ToFieldRef(Field);
    end;

    local procedure FindField(RecRef: RecordRef; Name: Text): FieldRef
    var
        Field: FieldRef;
        Index: Integer;
    begin
        for Index := 1 to RecRef.FieldCount() do begin
            Field := RecRef.FieldIndex(Index);
            if Field.Name() = Name then
                exit(Field);
        end;
        Error('Required Mexico invoice field is not available: %1.', Name);
    end;
}
