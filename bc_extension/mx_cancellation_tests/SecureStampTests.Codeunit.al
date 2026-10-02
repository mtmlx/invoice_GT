codeunit 71941 "MTM MX Secure Stamp Tests"
{
    Subtype = Test;

    [Test]
    procedure PacTaxCataloguesStayDistinctFromSatXmlCodes()
    var
        Stamper: Codeunit "MTM MX Secure Stamp Mgt";
    begin
        if Stamper.NormalizePacTaxId('2') <> 2 then
            Error('PAC JSON IVA must use integer catalogue ID 2.');
        if Stamper.NormalizePacTaxId('002') <> 2 then
            Error('Canonical XML code must normalize to the PAC integer ID.');
        if (Stamper.NormalizePacTaxFactor('1') <> 1) or (Stamper.NormalizePacTaxFactor('Tasa') <> 1) then
            Error('PAC factor 1 and native Tasa must have the same meaning.');
        if (Stamper.NormalizePacTaxType('1') <> 1) or (Stamper.NormalizePacTaxType('Trasladado') <> 1) then
            Error('Transferred IVA must use PAC tax type 1.');
    end;

    [Test]
    procedure LegacyNativeIvaCodeIsNormalizedWithoutChangingSetup()
    var
        Stamper: Codeunit "MTM MX Secure Stamp Mgt";
    begin
        if Stamper.NormalizeSatTaxCode('2') <> '002' then
            Error('Legacy IVA code 2 must be sent as the SAT code 002.');
        if Stamper.NormalizeSatTaxCode('002') <> '002' then
            Error('The already canonical SAT code must remain unchanged.');
        if Stamper.NormalizeSatTaxCode('1') <> '001' then
            Error('Legacy ISR code must also retain its catalogue meaning.');
    end;

    [Test]
    procedure MexicoTimestampUsesUTCMinusSixAcrossMidnight()
    var
        Stamper: Codeunit "MTM MX Secure Stamp Mgt";
        UTC: DateTime;
    begin
        if not Evaluate(UTC, '2026-10-02T02:03:40Z', 9) then
            Error('Test timestamp could not be parsed.');
        if Stamper.MexicoFiscalTimestamp(UTC) <> '2026-10-01T20:03:40' then
            Error('Mexico fiscal time must be six hours behind UTC, including the date boundary.');
    end;

    [Test]
    procedure NestedProviderRejectionRetainsTechnicalCause()
    var
        Stamper: Codeunit "MTM MX Secure Stamp Mgt";
        Result: JsonObject;
        UUID: Text;
        Body: Text;
    begin
        Body := '{"estatus":{"codigo":"017","descripcion":"Error al generar el timbrado","informacionTecnica":"Fecha fuera de rango"},"cfdiTimbrado":null}';
        if not Stamper.ReadProviderResult(Body, Result, UUID) then
            Error('The documented provider rejection must be recognized.');
        if (Stamper.ProviderCode(Body) <> '017') or (UUID <> '') then
            Error('A rejected request must not manufacture a stamp.');
        if StrPos(Stamper.ProviderDiagnostic(Body), 'Fecha fuera de rango') = 0 then
            Error('The technical cause must remain available for diagnosis.');
    end;

    [Test]
    procedure NestedSuccessfulStampIsParsedWithoutSubstringExtraction()
    var
        Stamper: Codeunit "MTM MX Secure Stamp Mgt";
        Result: JsonObject;
        UUID: Text;
    begin
        if not Stamper.ReadProviderResult('{ "estatus": { "codigo": "000" }, "cfdiTimbrado": { "respuesta": { "uuid": "BB14B70F-21D0-423B-8C6D-F2D46BDAD658", "cfdixml": "<example/>" } } }', Result, UUID) then
            Error('Whitespace in JSON must not affect response parsing.');
        if UUID <> 'BB14B70F-21D0-423B-8C6D-F2D46BDAD658' then
            Error('The returned fiscal UUID must be read from the documented object.');
    end;

    [Test]
    procedure SigningCredentialsAreNeverReturnedAsDiagnostics()
    var
        Stamper: Codeunit "MTM MX Secure Stamp Mgt";
        Value: Text;
    begin
        Value := Stamper.SafeDiagnostic('Failed: LlavePrivada=PRIVATE_SIGNING_MATERIAL');
        if StrPos(Value, 'PRIVATE_SIGNING_MATERIAL') <> 0 then
            Error('Credential-bearing response data must be suppressed.');
        Value := Stamper.SafeDiagnostic(PadStr('A', 100, 'A'));
        if StrPos(Value, PadStr('A', 80, 'A')) <> 0 then
            Error('Opaque signing material without a field name must be suppressed.');
    end;

    [Test]
    procedure UnrecognizedResponseCannotAuthorizeAStamp()
    var
        Stamper: Codeunit "MTM MX Secure Stamp Mgt";
        Result: JsonObject;
        UUID: Text;
    begin
        if Stamper.ReadProviderResult('{"uuid":"BB14B70F-21D0-423B-8C6D-F2D46BDAD658"}', Result, UUID) then
            Error('A response lacking a provider status cannot authorize a stamp.');
        if Stamper.ReadProviderResult('not-json', Result, UUID) then
            Error('Malformed responses must fail closed.');
    end;
}
