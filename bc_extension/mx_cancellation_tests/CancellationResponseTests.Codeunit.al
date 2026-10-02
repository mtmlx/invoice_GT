codeunit 71940 "MTM MX Cancellation Tests"
{
    Subtype = Test;
    [Test]
    procedure CorrectedAmountsStayBoundToTheirOwnInvoice()
    var
        Operation: Record "MTM MX Cancellation" temporary;
    begin
        Operation."Original Amount" := 15307.20;
        Operation.Amount := 15347.20;
        if Operation.InvoiceAmount(false) <> 15307.20 then
            Error('Original cancellation and credit must use the original amount.');
        if Operation.InvoiceAmount(true) <> 15347.20 then
            Error('Replacement verification must use the corrected amount.');
    end;
    [Test]
    procedure LegacyOperationsKeepTheirApprovedAmount()
    var
        Operation: Record "MTM MX Cancellation" temporary;
    begin
        Operation.Amount := 15307.20;
        if (Operation.InvoiceAmount(false) <> 15307.20) or
           (Operation.InvoiceAmount(true) <> 15307.20) then
            Error('Existing operations must retain their original total semantics.');
    end;
    [Test]
    procedure AcceptedRequestIsPendingNotConfirmed()
    var
        Operation: Record "MTM MX Cancellation" temporary;
        Provider: Codeunit "MTM MX Cancellation Provider";
    begin
        Provider.ApplyProviderResponse(Operation, '{"codigo":"000","estado":"Cancelado","acuse":"<Acuse/>"}');
        Operation.TestField(State, Operation.State::Pending);
    end;
    [Test]
    procedure ProviderRejectDoesNotConfirmCancellation()
    var
        Operation: Record "MTM MX Cancellation" temporary;
        Provider: Codeunit "MTM MX Cancellation Provider";
    begin
        Provider.ApplyProviderResponse(Operation, '{"codigo":"500","estado":"Cancelado"}');
        Operation.TestField(State, Operation.State::Rejected);
    end;
    [Test]
    procedure MalformedResponseIsUnknown()
    var
        Operation: Record "MTM MX Cancellation" temporary;
        Provider: Codeunit "MTM MX Cancellation Provider";
    begin
        Provider.ApplyProviderResponse(Operation, 'invalid response');
        Operation.TestField(State, Operation.State::Unknown);
    end;
    [Test]
    procedure MissingSuccessCodeIsUnknown()
    var
        Operation: Record "MTM MX Cancellation" temporary;
        Provider: Codeunit "MTM MX Cancellation Provider";
    begin
        Provider.ApplyProviderResponse(Operation, '{"estado":"Cancelado"}');
        Operation.TestField(State, Operation.State::Unknown);
    end;
    [Test]
    procedure SatSuccessIsParsedByNamespace()
    var
        Provider: Codeunit "MTM MX Cancellation Provider";
        Code: Text;
        Status: Text;
        CancellationStatus: Text;
    begin
        if not Provider.ReadSatResponse(Response('S - Comprobante obtenido satisfactoriamente.', 'Cancelado'), Code, Status, CancellationStatus) then
            Error('Expected a valid SAT response.');
        if Status <> 'Cancelado' then
            Error('Expected Cancelado.');
    end;
    [Test]
    procedure SatNotFoundCannotConfirmCancellation()
    var
        Provider: Codeunit "MTM MX Cancellation Provider";
        Code: Text;
        Status: Text;
        CancellationStatus: Text;
    begin
        if Provider.ReadSatResponse(Response('N - 601: No encontrado.', 'Cancelado'), Code, Status, CancellationStatus) then
            Error('A failed lookup must never confirm cancellation.');
    end;
    [Test]
    procedure SatUnknownStatusFailsClosed()
    var
        Provider: Codeunit "MTM MX Cancellation Provider";
        Code: Text;
        Status: Text;
        CancellationStatus: Text;
    begin
        if Provider.ReadSatResponse(Response('S - Comprobante obtenido satisfactoriamente.', 'Unexpected'), Code, Status, CancellationStatus) then
            Error('An unknown status must not be accepted.');
    end;
    [Test]
    procedure UnrelatedXmlCannotConfirmCancellation()
    var
        Provider: Codeunit "MTM MX Cancellation Provider";
        Code: Text;
        Status: Text;
        CancellationStatus: Text;
    begin
        if Provider.ReadSatResponse('<response><Estado>Cancelado</Estado></response>', Code, Status, CancellationStatus) then
            Error('An unrelated response must not be accepted.');
    end;
    local procedure Response(Code: Text; Status: Text): Text
    begin
        exit('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>' +
            '<ConsultaResponse xmlns="http://tempuri.org/"><ConsultaResult xmlns:a="http://schemas.datacontract.org/2004/07/Sat.Cfdi.Negocio.ConsultaCfdi.Servicio">' +
            '<a:CodigoEstatus>' + Code + '</a:CodigoEstatus><a:Estado>' + Status + '</a:Estado>' +
            '<a:EstatusCancelacion>Cancelado con aceptacion</a:EstatusCancelacion>' +
            '</ConsultaResult></ConsultaResponse></s:Body></s:Envelope>');
    end;
}
