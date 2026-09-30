codeunit 71041 "MTM MX Cancellation Provider"
{
    // The legacy CancelaFactura codeunit displays signing material and retries
    // cancellation POSTs to check status. This adapter deliberately does neither.
    Permissions = tabledata "General Ledger Setup" = r;

    [NonDebuggable]
    procedure PrepareRequest(Operation: Record "MTM MX Cancellation"; var Request: HttpRequestMessage)
    var
        Setup: Record "General Ledger Setup";
        Body: JsonObject;
        Content: HttpContent;
        Headers: HttpHeaders;
        Stream: InStream;
        Certificate: Text;
        PrivateKey: Text;
        Payload: Text;
    begin
        Setup.Get();
        Setup.TestField(Token);
        Setup.TestField("CSD Password");
        if CopyStr(LowerCase(Setup."Url Pac"), 1, 8) <> 'https://' then
            Error('The configured PAC URL must use HTTPS.');
        Setup.CalcFields(Certificado, "Llave privada");
        if not Setup.Certificado.HasValue() or not Setup."Llave privada".HasValue() then
            Error('The Mexico signing certificate and private key must be configured.');
        Setup.Certificado.CreateInStream(Stream, TextEncoding::Windows);
        Stream.ReadText(Certificate);
        Setup."Llave privada".CreateInStream(Stream, TextEncoding::Windows);
        Stream.ReadText(PrivateKey);
        if (Certificate = '') or (PrivateKey = '') then
            Error('The configured Mexico signing material is empty.');
        Body.Add('RfcEmisor', Operation."Issuer RFC");
        Body.Add('RfcReceptor', Operation."Recipient RFC");
        Body.Add('Uuid', Operation."Original UUID");
        Body.Add('Motivo', '01');
        Body.Add('FolioFiscalSustitucion', Operation."Replacement UUID");
        Body.Add('Total', Operation.Amount);
        Body.Add('Certificado', Certificate);
        Body.Add('LlavePrivada', PrivateKey);
        Body.Add('Password', Setup."CSD Password");
        Body.WriteTo(Payload);
        Content.WriteFrom(Payload);
        Content.GetHeaders(Headers);
        Headers.Clear();
        Headers.Add('Content-Type', 'application/json');
        Request.Content := Content;
        Request.Method := 'POST';
        Request.SetRequestUri(DelChr(Setup."Url Pac", '>', '/') + '/servicios/cancelar/csd');
        Request.GetHeaders(Headers);
        Headers.Add('Authorization', 'Bearer ' + Setup.Token);
    end;

    [NonDebuggable]
    procedure Submit(var Operation: Record "MTM MX Cancellation"; var Request: HttpRequestMessage)
    var
        Client: HttpClient;
        Response: HttpResponseMessage;
        Body: Text;
    begin
        Client.Timeout(60000);
        // Unknown was committed before this call. Any transport exception leaves
        // that durable marker in place; the request is never blindly resubmitted.
        if not Client.Send(Request, Response) then begin
            Operation."Result Code" := 'TRANSPORT_UNKNOWN';
            exit;
        end;
        if not Response.IsSuccessStatusCode() then begin
            Operation."Result Code" := CopyStr('HTTP_' + Format(Response.HttpStatusCode()), 1, 100);
            exit;
        end;
        if not Response.Content.ReadAs(Body) then begin
            Operation."Result Code" := 'UNREADABLE_RESPONSE';
            exit;
        end;
        ApplyProviderResponse(Operation, Body);
    end;

    procedure ApplyProviderResponse(var Operation: Record "MTM MX Cancellation"; Body: Text)
    var
        Response: JsonObject;
        Receipt: Text;
        Stream: OutStream;
    begin
        Operation.State := Operation.State::Unknown;
        if not Response.ReadFrom(Body) then begin
            Operation."Result Code" := 'INVALID_JSON';
            exit;
        end;
        Operation."Result Code" := CopyStr(JsonText(Response, 'codigo'), 1, 100);
        if Operation."Result Code" = '' then begin
            Operation."Result Code" := 'MISSING_PROVIDER_CODE';
            exit;
        end;
        if Operation."Result Code" <> '000' then begin
            Operation.State := Operation.State::Rejected;
            exit;
        end;
        // Provider acceptance is not final fiscal proof. Always query SAT before
        // finalizing accounting, including an immediate provider "Cancelado".
        Operation.State := Operation.State::Pending;
        Receipt := JsonText(Response, 'acuse');
        if Receipt <> '' then begin
            Operation."Fiscal Receipt".CreateOutStream(Stream, TextEncoding::UTF8);
            Stream.WriteText(Receipt);
        end;
    end;

    procedure QuerySat(var Operation: Record "MTM MX Cancellation"; Replacement: Boolean): Boolean
    var
        Client: HttpClient;
        Request: HttpRequestMessage;
        Response: HttpResponseMessage;
        Content: HttpContent;
        Headers: HttpHeaders;
        Body: Text;
        Expression: Text;
        FiscalUUID: Text;
        Xml: XmlDocument;
        Envelope: XmlElement;
        SoapBody: XmlElement;
        Query: XmlElement;
        Status: Text;
        Code: Text;
        CancellationStatus: Text;
        Stream: OutStream;
    begin
        FiscalUUID := Operation."Original UUID";
        if Replacement then
            FiscalUUID := Operation."Replacement UUID";
        Expression := '?re=' + Operation."Issuer RFC" + '&rr=' + Operation."Recipient RFC" +
            '&tt=' + Format(Operation.Amount, 0, 9) + '&id=' + FiscalUUID;
        Xml := XmlDocument.Create();
        Envelope := XmlElement.Create('Envelope', 'http://schemas.xmlsoap.org/soap/envelope/');
        SoapBody := XmlElement.Create('Body', 'http://schemas.xmlsoap.org/soap/envelope/');
        Query := XmlElement.Create('Consulta', 'http://tempuri.org/');
        Query.Add(XmlElement.Create('expresionImpresa', 'http://tempuri.org/', Expression));
        SoapBody.Add(Query);
        Envelope.Add(SoapBody);
        Xml.Add(Envelope);
        Xml.WriteTo(Body);
        Content.WriteFrom(Body);
        Content.GetHeaders(Headers);
        Headers.Clear();
        Headers.Add('Content-Type', 'text/xml; charset=utf-8');
        Request.Content := Content;
        Request.Method := 'POST'; // SAT Consulta is read-only despite SOAP POST.
        Request.SetRequestUri('https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc');
        Request.GetHeaders(Headers);
        Headers.Add('SOAPAction', 'http://tempuri.org/IConsultaCFDIService/Consulta');
        Client.Timeout(60000);
        if not Client.Send(Request, Response) then
            exit(false);
        if not Response.IsSuccessStatusCode() then
            exit(false);
        if not Response.Content.ReadAs(Body) then
            exit(false);
        if not ReadSatResponse(Body, Code, Status, CancellationStatus) then
            exit(false);
        if Replacement then
            exit(Status = 'Vigente');
        Operation."SAT Status" := CopyStr(Status, 1, 100);
        Operation."SAT Cancellation Status" := CopyStr(CancellationStatus, 1, 100);
        Operation."Result Code" := CopyStr(Code, 1, 100);
        Operation."Checked At" := CurrentDateTime();
        Operation."SAT Evidence".CreateOutStream(Stream, TextEncoding::UTF8);
        Stream.WriteText(Body);
        case Status of
            'Cancelado': Operation.State := Operation.State::Confirmed;
            'Vigente':
                if CancellationStatus = 'Solicitud rechazada' then
                    Operation.State := Operation.State::Rejected
                else
                    Operation.State := Operation.State::Pending;
            else
                Operation.State := Operation.State::Unknown;
        end;
        exit(true);
    end;

    procedure ReadSatResponse(Body: Text; var Code: Text; var Status: Text; var CancellationStatus: Text): Boolean
    var
        Document: XmlDocument;
        Node: XmlNode;
        Ns: XmlNamespaceManager;
    begin
        if not XmlDocument.ReadFrom(Body, Document) then
            exit(false);
        Ns.AddNamespace('s', 'http://schemas.xmlsoap.org/soap/envelope/');
        Ns.AddNamespace('t', 'http://tempuri.org/');
        Ns.AddNamespace('a', 'http://schemas.datacontract.org/2004/07/Sat.Cfdi.Negocio.ConsultaCfdi.Servicio');
        if not Document.SelectSingleNode('/s:Envelope/s:Body/t:ConsultaResponse/t:ConsultaResult/a:CodigoEstatus', Ns, Node) then
            exit(false);
        Code := Node.AsXmlElement().InnerText();
        if CopyStr(Code, 1, 2) <> 'S ' then
            exit(false);
        if not Document.SelectSingleNode('/s:Envelope/s:Body/t:ConsultaResponse/t:ConsultaResult/a:Estado', Ns, Node) then
            exit(false);
        Status := Node.AsXmlElement().InnerText();
        if Document.SelectSingleNode('/s:Envelope/s:Body/t:ConsultaResponse/t:ConsultaResult/a:EstatusCancelacion', Ns, Node) then
            CancellationStatus := Node.AsXmlElement().InnerText();
        exit((Status = 'Vigente') or (Status = 'Cancelado'));
    end;

    local procedure JsonText(Object: JsonObject; Name: Text): Text
    var
        Token: JsonToken;
    begin
        if not Object.Get(Name, Token) then
            exit('');
        if not Token.IsValue() then
            exit('');
        if Token.AsValue().IsNull() then
            exit('');
        exit(Token.AsValue().AsText());
    end;
}
