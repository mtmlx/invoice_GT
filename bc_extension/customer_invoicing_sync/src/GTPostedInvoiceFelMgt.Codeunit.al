codeunit 71002 "MTM GT Posted Inv FEL Mgt"
{
    Permissions = tabledata "Company Information"=r,
        tabledata Customer=r,
        tabledata "General Ledger Setup"=r,
        tabledata Item=r,
        tabledata "Payment Method"=r,
        tabledata "Payment Terms"=r,
        tabledata "Cust. Ledger Entry"=rm,
        tabledata "Sales Invoice Header"=rm,
        tabledata "Sales Invoice Line"=r,
        tabledata "Sales Cr.Memo Header"=rm,
        tabledata "Sales Cr.Memo Line"=r,
        tabledata "Unit of Measure"=r,
        tabledata "VAT Product Posting Group"=r;

    procedure StampPostedInvoiceNoEmail(SalesInv: Record "Sales Invoice Header")
    var
        FacturaGuata: Record "General Ledger Setup";
        Client: HttpClient;
        Request: HttpRequestMessage;
        Response: HttpResponseMessage;
        ContentHeaders: HttpHeaders;
        Content: HttpContent;
        Body: Text;
        RespondeAPI: Text;
        Folio: Text;
        Resultado: Boolean;
        MensajeError: Text;
        UUID: Text;
        XmlCertificado: Text;
        FechaTimbre: Text;
        Serie: Text;
        Numero: Text;
    begin
        FacturaGuata.FindFirst();
        ObtenerFolioVenta(SalesInv."No.", Folio);
        Body:=BuildFacturaGTXml(SalesInv);
        Content.WriteFrom(Body);
        Content.GetHeaders(ContentHeaders);
        ContentHeaders.Clear();
        ContentHeaders.Add('Content-Type', 'application/xml');
        ContentHeaders.Add('UsuarioApi', FacturaGuata.UsuarioApi);
        ContentHeaders.Add('LlaveApi', FacturaGuata.LlaveApi);
        ContentHeaders.Add('UsuarioFirma', FacturaGuata.UsuarioFirma);
        ContentHeaders.Add('Identificador', Folio);
        ContentHeaders.Add('LlaveFirma', FacturaGuata.LlaveFirma);
        Request.Content:=Content;
        Request.SetRequestUri(FacturaGuata.URLGuatemala + '/fel/procesounificado/transaccion/v2/xml');
        Request.Method:='POST';
        Client.Send(Request, Response);
        Response.Content().ReadAs(RespondeAPI);
        if Response.HttpStatusCode() = 200 then begin
            ExtractJsonData(false, RespondeAPI, Resultado, MensajeError, UUID, XmlCertificado, FechaTimbre, Serie, Numero);
            ApplyStampResponse(SalesInv, Resultado, MensajeError, UUID, XmlCertificado, FechaTimbre, Serie, Numero);
            exit;
        end;
        ApplyStampResponse(SalesInv, false, CopyStr(RespondeAPI, 1, 2048), '', '', '', '', '');
        Error('No pudo timbrar %1. HTTP %2: %3', SalesInv."No.", Response.HttpStatusCode(), CopyStr(RespondeAPI, 1, 2048));
    end;
    procedure CancelPostedInvoiceWithMotive(SalesInv: Record "Sales Invoice Header"; MotiveText: Text)
    begin
        CancelPostedInvoiceWithMotiveAndIssueDateTime(SalesInv, MotiveText, '');
    end;
    procedure StampPostedCreditMemoNoEmail(SalesCrMemo: Record "Sales Cr.Memo Header")
    var
        FacturaGuata: Record "General Ledger Setup";
        Client: HttpClient;
        Request: HttpRequestMessage;
        Response: HttpResponseMessage;
        ContentHeaders: HttpHeaders;
        Content: HttpContent;
        Body: Text;
        RespondeAPI: Text;
        Folio: Text;
        Resultado: Boolean;
        MensajeError: Text;
        UUID: Text;
        XmlCertificado: Text;
        FechaTimbre: Text;
        Serie: Text;
        Numero: Text;
    begin
        FacturaGuata.FindFirst();
        ObtenerFolioVenta(SalesCrMemo."No.", Folio);
        Body:=BuildNotaCreditoGTXml(SalesCrMemo, false);
        Content.WriteFrom(Body);
        Content.GetHeaders(ContentHeaders);
        ContentHeaders.Clear();
        ContentHeaders.Add('Content-Type', 'application/xml');
        ContentHeaders.Add('UsuarioApi', FacturaGuata.UsuarioApi);
        ContentHeaders.Add('LlaveApi', FacturaGuata.LlaveApi);
        ContentHeaders.Add('UsuarioFirma', FacturaGuata.UsuarioFirma);
        ContentHeaders.Add('Identificador', Folio);
        ContentHeaders.Add('LlaveFirma', FacturaGuata.LlaveFirma);
        Request.Content:=Content;
        Request.SetRequestUri(FacturaGuata.URLGuatemala + '/fel/procesounificado/transaccion/v2/xml');
        Request.Method:='POST';
        Client.Send(Request, Response);
        Response.Content().ReadAs(RespondeAPI);
        if Response.HttpStatusCode() = 200 then begin
            ExtractJsonData(true, RespondeAPI, Resultado, MensajeError, UUID, XmlCertificado, FechaTimbre, Serie, Numero);
            ApplyCreditMemoStampResponse(SalesCrMemo, Resultado, MensajeError, UUID, XmlCertificado, FechaTimbre, Serie, Numero, Body);
            if not Resultado then Error('No pudo timbrar nota de credito %1: %2', SalesCrMemo."No.", MensajeError);
            exit;
        end;
        ApplyCreditMemoStampResponse(SalesCrMemo, false, CopyStr(RespondeAPI, 1, 2048), '', '', '', '', '', Body);
        Error('No pudo timbrar nota de credito %1. HTTP %2: %3', SalesCrMemo."No.", Response.HttpStatusCode(), CopyStr(RespondeAPI, 1, 2048));
    end;
    procedure StampPostedFullCreditMemoUsingRelatedInvoiceLinesNoEmail(SalesCrMemo: Record "Sales Cr.Memo Header")
    var
        FacturaGuata: Record "General Ledger Setup";
        CreditMemoCustLedgEntry: Record "Cust. Ledger Entry";
        SalesCrMemoRef: RecordRef;
        Client: HttpClient;
        Request: HttpRequestMessage;
        Response: HttpResponseMessage;
        ContentHeaders: HttpHeaders;
        Content: HttpContent;
        Body: Text;
        RespondeAPI: Text;
        Folio: Text;
        Resultado: Boolean;
        MensajeError: Text;
        UUID: Text;
        XmlCertificado: Text;
        FechaTimbre: Text;
        Serie: Text;
        Numero: Text;
    begin
        SalesCrMemoRef.GetTable(SalesCrMemo);
        if ReadTextField(SalesCrMemoRef, 'Fiscal Invoice Number PAC') <> '' then
            Error('Credit memo %1 already has a FEL fiscal UUID.', SalesCrMemo."No.");
        FindOpenCustomerLedgerEntry(CreditMemoCustLedgEntry, CreditMemoCustLedgEntry."Document Type"::"Credit Memo", SalesCrMemo."No.");
        FacturaGuata.FindFirst();
        ObtenerFolioVenta(SalesCrMemo."No.", Folio);
        Body:=BuildNotaCreditoGTXml(SalesCrMemo, true);
        Content.WriteFrom(Body);
        Content.GetHeaders(ContentHeaders);
        ContentHeaders.Clear();
        ContentHeaders.Add('Content-Type', 'application/xml');
        ContentHeaders.Add('UsuarioApi', FacturaGuata.UsuarioApi);
        ContentHeaders.Add('LlaveApi', FacturaGuata.LlaveApi);
        ContentHeaders.Add('UsuarioFirma', FacturaGuata.UsuarioFirma);
        ContentHeaders.Add('Identificador', Folio);
        ContentHeaders.Add('LlaveFirma', FacturaGuata.LlaveFirma);
        Request.Content:=Content;
        Request.SetRequestUri(FacturaGuata.URLGuatemala + '/fel/procesounificado/transaccion/v2/xml');
        Request.Method:='POST';
        Client.Send(Request, Response);
        Response.Content().ReadAs(RespondeAPI);
        if Response.HttpStatusCode() = 200 then begin
            ExtractJsonData(true, RespondeAPI, Resultado, MensajeError, UUID, XmlCertificado, FechaTimbre, Serie, Numero);
            ApplyCreditMemoStampResponse(SalesCrMemo, Resultado, MensajeError, UUID, XmlCertificado, FechaTimbre, Serie, Numero, Body);
            if not Resultado then Error('No pudo timbrar nota de credito %1: %2', SalesCrMemo."No.", MensajeError);
            exit;
        end;
        ApplyCreditMemoStampResponse(SalesCrMemo, false, CopyStr(RespondeAPI, 1, 2048), '', '', '', '', '', Body);
        Error('No pudo timbrar nota de credito %1. HTTP %2: %3', SalesCrMemo."No.", Response.HttpStatusCode(), CopyStr(RespondeAPI, 1, 2048));
    end;
    procedure ApplyPostedCreditMemoToInvoice(SalesCrMemo: Record "Sales Cr.Memo Header"; InvoiceNo: Code[20]; ExpectedAppliedAmount: Decimal)
    var
        InvoiceCustLedgEntry: Record "Cust. Ledger Entry";
        CreditMemoCustLedgEntry: Record "Cust. Ledger Entry";
        ApplyUnapplyParameters: Record "Apply Unapply Parameters" temporary;
        CustEntryApplyPostedEntries: Codeunit "CustEntry-Apply Posted Entries";
        AppliesToID: Code[50];
        CreditRemainingAmount: Decimal;
        InvoiceEntryNo: Integer;
        CreditMemoEntryNo: Integer;
    begin
        if InvoiceNo = '' then Error('Invoice number is required.');
        if ExpectedAppliedAmount <= 0 then Error('Expected applied amount must be greater than zero.');
        FindOpenCustomerLedgerEntry(InvoiceCustLedgEntry, InvoiceCustLedgEntry."Document Type"::Invoice, InvoiceNo);
        FindOpenCustomerLedgerEntry(CreditMemoCustLedgEntry, CreditMemoCustLedgEntry."Document Type"::"Credit Memo", SalesCrMemo."No.");
        if InvoiceCustLedgEntry."Customer No." <> CreditMemoCustLedgEntry."Customer No." then Error('Invoice %1 and credit memo %2 belong to different customers.', InvoiceNo, SalesCrMemo."No.");
        if InvoiceCustLedgEntry."Currency Code" <> CreditMemoCustLedgEntry."Currency Code" then Error('Invoice %1 and credit memo %2 use different currencies.', InvoiceNo, SalesCrMemo."No.");
        InvoiceCustLedgEntry.CalcFields("Remaining Amount");
        CreditMemoCustLedgEntry.CalcFields("Remaining Amount");
        CreditRemainingAmount:=Abs(CreditMemoCustLedgEntry."Remaining Amount");
        if Round(CreditRemainingAmount, 0.01, '=') <> Round(ExpectedAppliedAmount, 0.01, '=')then Error('Credit memo %1 remaining amount is %2, not expected amount %3.', SalesCrMemo."No.", CreditRemainingAmount, ExpectedAppliedAmount);
        if InvoiceCustLedgEntry."Remaining Amount" < ExpectedAppliedAmount then Error('Invoice %1 remaining amount %2 is less than expected applied amount %3.', InvoiceNo, InvoiceCustLedgEntry."Remaining Amount", ExpectedAppliedAmount);
        AppliesToID:=CopyStr(SalesCrMemo."No.", 1, MaxStrLen(AppliesToID));
        InvoiceEntryNo:=InvoiceCustLedgEntry."Entry No.";
        CreditMemoEntryNo:=CreditMemoCustLedgEntry."Entry No.";
        InvoiceCustLedgEntry.Get(InvoiceEntryNo);
        InvoiceCustLedgEntry."Applies-to ID":=AppliesToID;
        InvoiceCustLedgEntry."Applying Entry":=false;
        InvoiceCustLedgEntry.Validate("Amount to Apply", ExpectedAppliedAmount);
        InvoiceCustLedgEntry.Modify(true);
        CreditMemoCustLedgEntry.Get(CreditMemoEntryNo);
        CreditMemoCustLedgEntry.CalcFields("Remaining Amount");
        CreditMemoCustLedgEntry."Applies-to ID":=AppliesToID;
        CreditMemoCustLedgEntry."Applying Entry":=true;
        CreditMemoCustLedgEntry.Validate("Amount to Apply", CreditMemoCustLedgEntry."Remaining Amount");
        CreditMemoCustLedgEntry.Modify(true);
        ApplyUnapplyParameters.Init();
        ApplyUnapplyParameters."Account Type":=ApplyUnapplyParameters."Account Type"::Customer;
        ApplyUnapplyParameters."Account No.":=CreditMemoCustLedgEntry."Customer No.";
        if InvoiceCustLedgEntry."Posting Date" > CreditMemoCustLedgEntry."Posting Date" then ApplyUnapplyParameters."Posting Date":=InvoiceCustLedgEntry."Posting Date"
        else
            ApplyUnapplyParameters."Posting Date":=CreditMemoCustLedgEntry."Posting Date";
        ApplyUnapplyParameters."Document No.":=CreditMemoCustLedgEntry."Document No.";
        ApplyUnapplyParameters."Entry No.":=CreditMemoCustLedgEntry."Entry No.";
        ApplyUnapplyParameters.Insert();
        CreditMemoCustLedgEntry.Get(CreditMemoEntryNo);
        if not CustEntryApplyPostedEntries.Apply(CreditMemoCustLedgEntry, ApplyUnapplyParameters)then Error('Business Central did not post the application for credit memo %1.', SalesCrMemo."No.");
    end;
    procedure CancelPostedInvoiceWithMotiveAndIssueDateTime(SalesInv: Record "Sales Invoice Header"; MotiveText: Text; IssueDateTimeText: Text)
    var
        FacturaGuata: Record "General Ledger Setup";
        Company: Record "Company Information";
        Cliente: Record Customer;
        Client: HttpClient;
        Request: HttpRequestMessage;
        Response: HttpResponseMessage;
        ContentHeaders: HttpHeaders;
        Content: HttpContent;
        Body: Text;
        RespondeAPI: Text;
        Folio: Text;
        Resultado: Boolean;
        MensajeError: Text;
        UUID: Text;
        XmlCertificado: Text;
        FechaTimbre: Text;
        Serie: Text;
        Numero: Text;
        SalesInvRef: RecordRef;
        FiscalInvoiceNumberPAC: Text;
        DateTimeStamped: Text;
        CancelDateTimeText: Text;
        ResolvedIssueDateTimeText: Text;
    begin
        if MotiveText = '' then Error('Cancellation motive text is required.');
        FacturaGuata.FindFirst();
        Company.FindFirst();
        Cliente.Get(SalesInv."Sell-to Customer No.");
        ObtenerFolioVenta(SalesInv."No.", Folio);
        SalesInvRef.GetTable(SalesInv);
        DateTimeStamped:=ReadTextField(SalesInvRef, 'Date/Time Stamped');
        FiscalInvoiceNumberPAC:=ReadTextField(SalesInvRef, 'Fiscal Invoice Number PAC');
        if DateTimeStamped = '' then Error('Invoice %1 does not have Date/Time Stamped.', SalesInv."No.");
        if FiscalInvoiceNumberPAC = '' then Error('Invoice %1 does not have Fiscal Invoice Number PAC.', SalesInv."No.");
        CancelDateTimeText:=Format(Today(), 0, '<Year4>-<Month,2>-<Day,2>') + 'T' + Format(Time(), 0, '<Hours24,2><Filler Character,0>:<Minutes,2>:<Seconds,2>');
        ResolvedIssueDateTimeText:=IssueDateTimeText;
        if ResolvedIssueDateTimeText = '' then ResolvedIssueDateTimeText:=ResolveCancellationIssueDateTime(DateTimeStamped, SalesInv."Posting Date");
        Body:='<dte:GTAnulacionDocumento xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:dte="http://www.sat.gob.gt/dte/fel/0.1.0" xmlns:n1="http://www.altova.com/samplexml/other-namespace" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" Version="0.1" xsi:schemaLocation="http://www.sat.gob.gt/dte/fel/0.1.0 C:\Users\User\Desktop\FEL\Esquemas\GT_AnulacionDocumento-0.1.0.xsd">' + '<dte:SAT><dte:AnulacionDTE ID="DatosCertificados">' + '<dte:DatosGenerales FechaEmisionDocumentoAnular="' + EscapeXml(ResolvedIssueDateTimeText) + '" FechaHoraAnulacion="' + EscapeXml(CancelDateTimeText) + '" ID="DatosAnulacion" IDReceptor="' + EscapeXml(Cliente."VAT Registration No.") + '" MotivoAnulacion="' + EscapeXml(MotiveText) + '" NITEmisor="' + EscapeXml(Company."VAT Registration No.") + '" NumeroDocumentoAAnular="' + EscapeXml(FiscalInvoiceNumberPAC) + '"></dte:DatosGenerales>' + '</dte:AnulacionDTE></dte:SAT></dte:GTAnulacionDocumento>';
        Content.WriteFrom(Body);
        Content.GetHeaders(ContentHeaders);
        ContentHeaders.Clear();
        ContentHeaders.Add('Content-Type', 'application/xml');
        ContentHeaders.Add('UsuarioApi', FacturaGuata.UsuarioApi);
        ContentHeaders.Add('LlaveApi', FacturaGuata.LlaveApi);
        ContentHeaders.Add('UsuarioFirma', FacturaGuata.UsuarioFirma);
        ContentHeaders.Add('Identificador', Folio);
        ContentHeaders.Add('LlaveFirma', FacturaGuata.LlaveFirma);
        Request.Content:=Content;
        Request.SetRequestUri(FacturaGuata.URLGuatemala + '/fel/procesounificado/transaccion/v2/xml');
        Request.Method:='POST';
        Client.Send(Request, Response);
        Response.Content().ReadAs(RespondeAPI);
        if Response.HttpStatusCode() = 200 then begin
            ExtractJsonData(false, RespondeAPI, Resultado, MensajeError, UUID, XmlCertificado, FechaTimbre, Serie, Numero);
            ApplyCancelResponse(SalesInv, Resultado, MensajeError, UUID, XmlCertificado, Serie);
            if not Resultado then Error('No pudo cancelar %1 en FEL: %2', SalesInv."No.", MensajeError);
            exit;
        end;
        ApplyCancelResponse(SalesInv, false, CopyStr(RespondeAPI, 1, 2048), '', '', '');
        Error('No pudo cancelar %1 en FEL. HTTP %2: %3', SalesInv."No.", Response.HttpStatusCode(), CopyStr(RespondeAPI, 1, 2048));
    end;
    procedure CancelPostedCreditMemoWithMotive(SalesCrMemo: Record "Sales Cr.Memo Header"; MotiveText: Text)
    begin
        CancelPostedCreditMemoWithMotiveAndIssueDateTime(SalesCrMemo, MotiveText, '');
    end;
    procedure CancelPostedCreditMemoWithMotiveAndIssueDateTime(SalesCrMemo: Record "Sales Cr.Memo Header"; MotiveText: Text; IssueDateTimeText: Text)
    var
        FacturaGuata: Record "General Ledger Setup";
        Company: Record "Company Information";
        Cliente: Record Customer;
        Client: HttpClient;
        Request: HttpRequestMessage;
        Response: HttpResponseMessage;
        ContentHeaders: HttpHeaders;
        Content: HttpContent;
        Body: Text;
        RespondeAPI: Text;
        Folio: Text;
        Resultado: Boolean;
        MensajeError: Text;
        UUID: Text;
        XmlCertificado: Text;
        FechaTimbre: Text;
        Serie: Text;
        Numero: Text;
        SalesCrMemoRef: RecordRef;
        FiscalInvoiceNumberPAC: Text;
        DateTimeStamped: Text;
        CancelDateTimeText: Text;
        ResolvedIssueDateTimeText: Text;
    begin
        if MotiveText = '' then Error('Cancellation motive text is required.');
        FacturaGuata.FindFirst();
        Company.FindFirst();
        Cliente.Get(SalesCrMemo."Sell-to Customer No.");
        ObtenerFolioVenta(SalesCrMemo."No.", Folio);
        SalesCrMemoRef.GetTable(SalesCrMemo);
        DateTimeStamped:=ReadTextField(SalesCrMemoRef, 'Date/Time Stamped');
        FiscalInvoiceNumberPAC:=ReadTextField(SalesCrMemoRef, 'Fiscal Invoice Number PAC');
        if DateTimeStamped = '' then Error('Credit memo %1 does not have Date/Time Stamped.', SalesCrMemo."No.");
        if FiscalInvoiceNumberPAC = '' then Error('Credit memo %1 does not have Fiscal Invoice Number PAC.', SalesCrMemo."No.");
        CancelDateTimeText:=Format(Today(), 0, '<Year4>-<Month,2>-<Day,2>') + 'T' + Format(Time(), 0, '<Hours24,2><Filler Character,0>:<Minutes,2>:<Seconds,2>');
        ResolvedIssueDateTimeText:=IssueDateTimeText;
        if ResolvedIssueDateTimeText = '' then ResolvedIssueDateTimeText:=ResolveCancellationIssueDateTime(DateTimeStamped, SalesCrMemo."Posting Date");
        Body:='<dte:GTAnulacionDocumento xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:dte="http://www.sat.gob.gt/dte/fel/0.1.0" xmlns:n1="http://www.altova.com/samplexml/other-namespace" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" Version="0.1" xsi:schemaLocation="http://www.sat.gob.gt/dte/fel/0.1.0 C:\Users\User\Desktop\FEL\Esquemas\GT_AnulacionDocumento-0.1.0.xsd">' + '<dte:SAT><dte:AnulacionDTE ID="DatosCertificados">' + '<dte:DatosGenerales FechaEmisionDocumentoAnular="' + EscapeXml(ResolvedIssueDateTimeText) + '" FechaHoraAnulacion="' + EscapeXml(CancelDateTimeText) + '" ID="DatosAnulacion" IDReceptor="' + EscapeXml(Cliente."VAT Registration No.") + '" MotivoAnulacion="' + EscapeXml(MotiveText) + '" NITEmisor="' + EscapeXml(Company."VAT Registration No.") + '" NumeroDocumentoAAnular="' + EscapeXml(FiscalInvoiceNumberPAC) + '"></dte:DatosGenerales>' + '</dte:AnulacionDTE></dte:SAT></dte:GTAnulacionDocumento>';
        Content.WriteFrom(Body);
        Content.GetHeaders(ContentHeaders);
        ContentHeaders.Clear();
        ContentHeaders.Add('Content-Type', 'application/xml');
        ContentHeaders.Add('UsuarioApi', FacturaGuata.UsuarioApi);
        ContentHeaders.Add('LlaveApi', FacturaGuata.LlaveApi);
        ContentHeaders.Add('UsuarioFirma', FacturaGuata.UsuarioFirma);
        ContentHeaders.Add('Identificador', Folio);
        ContentHeaders.Add('LlaveFirma', FacturaGuata.LlaveFirma);
        Request.Content:=Content;
        Request.SetRequestUri(FacturaGuata.URLGuatemala + '/fel/procesounificado/transaccion/v2/xml');
        Request.Method:='POST';
        Client.Send(Request, Response);
        Response.Content().ReadAs(RespondeAPI);
        if Response.HttpStatusCode() = 200 then begin
            ExtractJsonData(false, RespondeAPI, Resultado, MensajeError, UUID, XmlCertificado, FechaTimbre, Serie, Numero);
            ApplyCreditMemoCancelResponse(SalesCrMemo, Resultado, MensajeError, UUID, XmlCertificado, Serie);
            if not Resultado then Error('No pudo cancelar %1 en FEL: %2', SalesCrMemo."No.", MensajeError);
            exit;
        end;
        ApplyCreditMemoCancelResponse(SalesCrMemo, false, CopyStr(RespondeAPI, 1, 2048), '', '', '');
        Error('No pudo cancelar %1 en FEL. HTTP %2: %3', SalesCrMemo."No.", Response.HttpStatusCode(), CopyStr(RespondeAPI, 1, 2048));
    end;
    local procedure BuildFacturaGTXml(SalesInv: Record "Sales Invoice Header"): Text var
        Company: Record "Company Information";
        Cliente: Record Customer;
        LineasVenta: Record "Sales Invoice Line";
        ImpuestosinfoSAT: Record "VAT Product Posting Group";
        Body: Text;
        SubBody: Text;
        Otros: Text;
        Subfrase: Text;
        Adendas: Text;
        CurrencyCode: Text;
        TipoEspecial: Text;
        TiempoText: Text;
        FechaText: Text;
        Letras: Text;
        Numeros: Text;
        GranTotal: Decimal;
        GranTotalIVA: Decimal;
        GranTotalT: Text;
        GranTotalIVAT: Text;
        ValidaFrase: Boolean;
        CompanyPais: Text;
        ClientePais: Text;
    begin
        SeparacionAddenda(SalesInv."No.", Letras, Numeros);
        Company.FindFirst();
        Cliente.Get(SalesInv."Sell-to Customer No.");
        CompanyPais:=ResolveFelCountryCode(Company.Pais, Company."Country/Region Code", 'company information');
        ClientePais:=ResolveFelCountryCode(Cliente.Pais, Cliente."Country/Region Code", StrSubstNo('customer %1', Cliente."No."));
        FechaText:=Format(Today(), 0, '<Year4>-<Month,2>-<Day,2>');
        TiempoText:=FechaText + 'T' + Format(Time(), 0, '<Hours24,2><Filler Character,0>:<Minutes,2>:<Seconds,2>');
        if SalesInv."Currency Code" = '' then CurrencyCode:='GTQ'
        else
            CurrencyCode:=SalesInv."Currency Code";
        LineasVenta.SetRange("Document No.", SalesInv."No.");
        LineasVenta.SetRange(Type, LineasVenta.Type::" ");
        if LineasVenta.FindSet()then repeat Adendas:=LineasVenta.Description;
            until LineasVenta.Next() = 0;
        LineasVenta.Reset();
        LineasVenta.SetRange("Document No.", SalesInv."No.");
        LineasVenta.SetRange(Type, LineasVenta.Type::Item);
        if LineasVenta.FindSet()then repeat ImpuestosinfoSAT.Get(LineasVenta."VAT Prod. Posting Group");
                if ImpuestosinfoSAT."Codigo Unidad Gravable" = Format(2)then ValidaFrase:=true;
            until LineasVenta.Next() = 0;
        if ValidaFrase then Subfrase:='<dte:Frase CodigoEscenario="24" TipoFrase="4"/>';
        if Cliente.TipoEspecial then TipoEspecial:='TipoEspecial="CUI"';
        if Cliente.TipoEspecialEXT then TipoEspecial:='TipoEspecial="EXT"';
        Body:='<dte:GTDocumento xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:dte="http://www.sat.gob.gt/dte/fel/0.2.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" Version="0.1" xsi:schemaLocation="http://www.sat.gob.gt/dte/fel/0.2.0">' + '<dte:SAT ClaseDocumento="dte">' + '<dte:DTE ID="DatosCertificados">' + '<dte:DatosEmision ID="DatosEmision">' + '<dte:DatosGenerales CodigoMoneda="' + EscapeXml(CurrencyCode) + '" FechaHoraEmision="' + EscapeXml(TiempoText) + '" Tipo="FACT"/>' + '<dte:Emisor AfiliacionIVA="GEN" CodigoEstablecimiento="1" NITEmisor="' + EscapeXml(Company."VAT Registration No.") + '" NombreComercial="' + EscapeXml(Company."Nombre comercial") + '" NombreEmisor="' + EscapeXml(Company.Name) + '">' + '<dte:DireccionEmisor><dte:Direccion>' + EscapeXml(Company.Address) + '</dte:Direccion><dte:CodigoPostal>' + EscapeXml(Company."Post Code") + '</dte:CodigoPostal><dte:Municipio>' + EscapeXml(Company.City) + '</dte:Municipio><dte:Departamento>' + EscapeXml(Company.County) + '</dte:Departamento><dte:Pais>' + EscapeXml(CompanyPais) + '</dte:Pais></dte:DireccionEmisor>' + '</dte:Emisor>' + '<dte:Receptor CorreoReceptor="' + EscapeXml(Cliente."E-Mail") + '" IDReceptor="' + EscapeXml(Cliente."VAT Registration No.") + '" NombreReceptor="' + EscapeXml(Cliente.Name) + '" ' + TipoEspecial + '>' + '<dte:DireccionReceptor><dte:Direccion>' + EscapeXml(Cliente.Address) + '</dte:Direccion><dte:CodigoPostal>' + EscapeXml(Cliente."Post Code") + '</dte:CodigoPostal><dte:Municipio>' + EscapeXml(Cliente.City) + '</dte:Municipio><dte:Departamento>' + EscapeXml(Cliente.County) + '</dte:Departamento><dte:Pais>' + EscapeXml(ClientePais) + '</dte:Pais></dte:DireccionReceptor>' + '</dte:Receptor>' + '<dte:Frases><dte:Frase CodigoEscenario="1" TipoFrase="1"/>' + Subfrase + '</dte:Frases><dte:Items>';
        SubBody:=BuildFacturaGTLineXml(SalesInv, GranTotal, GranTotalIVA, GranTotalT, GranTotalIVAT, ImpuestosinfoSAT);
        Otros:='</dte:Items>' + '<dte:Totales><dte:TotalImpuestos><dte:TotalImpuesto NombreCorto="' + EscapeXml(ImpuestosinfoSAT."Nombre Corto -GT") + '" TotalMontoImpuesto="' + GranTotalIVAT + '"/></dte:TotalImpuestos><dte:GranTotal>' + GranTotalT + '</dte:GranTotal></dte:Totales>' + '</dte:DatosEmision></dte:DTE>' + '<dte:Adenda><Codigo_cliente>' + EscapeXml(Cliente."Codigo Cliente") + '</Codigo_cliente><Observaciones>' + EscapeXml(Adendas) + '</Observaciones><SERIEINTERNA>' + EscapeXml(Letras) + '</SERIEINTERNA><NUMERO-INTERNO>' + EscapeXml(Numeros) + '</NUMERO-INTERNO></dte:Adenda>' + '</dte:SAT></dte:GTDocumento>';
        exit(Body + SubBody + Otros);
    end;
    local procedure BuildNotaCreditoGTXml(SalesCrMemo: Record "Sales Cr.Memo Header"; UseRelatedInvoiceLines: Boolean): Text var
        Company: Record "Company Information";
        Cliente: Record Customer;
        LineasVenta: Record "Sales Cr.Memo Line";
        ImpuestosinfoSAT: Record "VAT Product Posting Group";
        Body: Text;
        SubBody: Text;
        Otros: Text;
        Adendas: Text;
        CurrencyCode: Text;
        TipoEspecial: Text;
        TiempoText: Text;
        FechaText: Text;
        Letras: Text;
        Numeros: Text;
        GranTotal: Decimal;
        GranTotalIVA: Decimal;
        GranTotalT: Text;
        GranTotalIVAT: Text;
        CompanyPais: Text;
        ClientePais: Text;
        RelatedInvoiceNo: Code[20];
        RelatedUuid: Text;
        RelatedSerie: Text;
        RelatedNumero: Text;
        RelatedIssueDate: Text;
    begin
        SeparacionAddenda(SalesCrMemo."No.", Letras, Numeros);
        Company.FindFirst();
        Cliente.Get(SalesCrMemo."Sell-to Customer No.");
        CompanyPais:=ResolveFelCountryCode(Company.Pais, Company."Country/Region Code", 'company information');
        ClientePais:=ResolveFelCountryCode(Cliente.Pais, Cliente."Country/Region Code", StrSubstNo('customer %1', Cliente."No."));
        ResolveCreditMemoRelatedInvoice(SalesCrMemo, RelatedInvoiceNo, RelatedUuid, RelatedSerie, RelatedNumero, RelatedIssueDate);
        FechaText:=Format(Today(), 0, '<Year4>-<Month,2>-<Day,2>');
        TiempoText:=FechaText + 'T' + Format(Time(), 0, '<Hours24,2><Filler Character,0>:<Minutes,2>:<Seconds,2>');
        if SalesCrMemo."Currency Code" = '' then CurrencyCode:='GTQ'
        else
            CurrencyCode:=SalesCrMemo."Currency Code";
        LineasVenta.SetRange("Document No.", SalesCrMemo."No.");
        LineasVenta.SetRange(Type, LineasVenta.Type::" ");
        if LineasVenta.FindSet()then repeat Adendas:=LineasVenta.Description;
            until LineasVenta.Next() = 0;
        if Cliente.TipoEspecial then TipoEspecial:='TipoEspecial="CUI"';
        if Cliente.TipoEspecialEXT then TipoEspecial:='TipoEspecial="EXT"';
        Body:='<dte:GTDocumento xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:dte="http://www.sat.gob.gt/dte/fel/0.2.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" Version="0.1" xsi:schemaLocation="http://www.sat.gob.gt/dte/fel/0.2.0">' + '<dte:SAT ClaseDocumento="dte">' + '<dte:DTE ID="DatosCertificados">' + '<dte:DatosEmision ID="DatosEmision">' + '<dte:DatosGenerales CodigoMoneda="' + EscapeXml(CurrencyCode) + '" FechaHoraEmision="' + EscapeXml(TiempoText) + '" Tipo="NCRE"/>' + '<dte:Emisor AfiliacionIVA="GEN" CodigoEstablecimiento="1" NITEmisor="' + EscapeXml(Company."VAT Registration No.") + '" NombreComercial="' + EscapeXml(Company."Nombre comercial") + '" NombreEmisor="' + EscapeXml(Company.Name) + '">' + '<dte:DireccionEmisor><dte:Direccion>' + EscapeXml(Company.Address) + '</dte:Direccion><dte:CodigoPostal>' + EscapeXml(Company."Post Code") + '</dte:CodigoPostal><dte:Municipio>' + EscapeXml(Company.City) + '</dte:Municipio><dte:Departamento>' + EscapeXml(Company.County) + '</dte:Departamento><dte:Pais>' + EscapeXml(CompanyPais) + '</dte:Pais></dte:DireccionEmisor>' + '</dte:Emisor>' + '<dte:Receptor CorreoReceptor="' + EscapeXml(Cliente."E-Mail") + '" IDReceptor="' + EscapeXml(Cliente."VAT Registration No.") + '" NombreReceptor="' + EscapeXml(Cliente.Name) + '" ' + TipoEspecial + '>' + '<dte:DireccionReceptor><dte:Direccion>' + EscapeXml(Cliente.Address) + '</dte:Direccion><dte:CodigoPostal>' + EscapeXml(Cliente."Post Code") + '</dte:CodigoPostal><dte:Municipio>' + EscapeXml(Cliente.City) + '</dte:Municipio><dte:Departamento>' + EscapeXml(Cliente.County) + '</dte:Departamento><dte:Pais>' + EscapeXml(ClientePais) + '</dte:Pais></dte:DireccionReceptor>' + '</dte:Receptor>' + '<dte:Frases><dte:Frase CodigoEscenario="1" TipoFrase="1"/>';
        Body+='</dte:Frases><dte:Items>';
        if UseRelatedInvoiceLines then
            SubBody:=BuildFullCreditMemoFromRelatedInvoiceLines(SalesCrMemo, RelatedInvoiceNo, GranTotal, GranTotalIVA, GranTotalT, GranTotalIVAT, ImpuestosinfoSAT)
        else
            SubBody:=BuildNotaCreditoGTLineXml(SalesCrMemo, GranTotal, GranTotalIVA, GranTotalT, GranTotalIVAT, ImpuestosinfoSAT);
        Otros:='</dte:Items>' + '<dte:Totales><dte:TotalImpuestos><dte:TotalImpuesto NombreCorto="' + EscapeXml(ImpuestosinfoSAT."Nombre Corto -GT") + '" TotalMontoImpuesto="' + GranTotalIVAT + '"/></dte:TotalImpuestos><dte:GranTotal>' + GranTotalT + '</dte:GranTotal></dte:Totales>' + '<dte:Complementos><dte:Complemento IDComplemento="TEXT" NombreComplemento="TEXT" URIComplemento="TEXT">' + '<cno:ReferenciasNota xmlns:cno="http://www.sat.gob.gt/face2/ComplementoReferenciaNota/0.1.0" FechaEmisionDocumentoOrigen="' + EscapeXml(RelatedIssueDate) + '" MotivoAjuste="' + EscapeXml(SalesCrMemo."Motivo Cancela") + '" NumeroAutorizacionDocumentoOrigen="' + EscapeXml(RelatedUuid) + '" NumeroDocumentoOrigen="' + EscapeXml(RelatedNumero) + '" SerieDocumentoOrigen="' + EscapeXml(RelatedSerie) + '" Version="0.0" xsi:schemaLocation="http://www.sat.gob.gt/face2/ComplementoReferenciaNota/0.1.0 C:\Users\User\Desktop\FEL\Esquemas\GT_Complemento_Referencia_Nota-0.1.0.xsd"></cno:ReferenciasNota>' + '</dte:Complemento></dte:Complementos>' + '</dte:DatosEmision></dte:DTE>' + '<dte:Adenda><Codigo_cliente>' + EscapeXml(Cliente."Codigo Cliente") + '</Codigo_cliente><Observaciones>' + EscapeXml(Adendas) + '</Observaciones><SERIEINTERNA>' + EscapeXml(Letras) + '</SERIEINTERNA><NUMERO-INTERNO>' + EscapeXml(Numeros) + '</NUMERO-INTERNO></dte:Adenda>' + '</dte:SAT></dte:GTDocumento>';
        exit(Body + SubBody + Otros);
    end;
    local procedure BuildFullCreditMemoFromRelatedInvoiceLines(SalesCrMemo: Record "Sales Cr.Memo Header"; RelatedInvoiceNo: Code[20]; var GranTotal: Decimal; var GranTotalIVA: Decimal; var GranTotalT: Text; var GranTotalIVAT: Text; var LastImpuestosinfoSAT: Record "VAT Product Posting Group"): Text
    var
        RelatedSalesInv: Record "Sales Invoice Header";
        SalesCrMemoLine: Record "Sales Cr.Memo Line";
        SalesInvLine: Record "Sales Invoice Line";
        CreditMemoGrossTotal: Decimal;
        RelatedInvoiceGrossTotal: Decimal;
        CreditMemoItemCount: Integer;
        RelatedInvoiceItemCount: Integer;
    begin
        RelatedSalesInv.Get(RelatedInvoiceNo);
        if SalesCrMemo."Sell-to Customer No." <> RelatedSalesInv."Sell-to Customer No." then
            Error('Credit memo %1 customer %2 does not match related invoice %3 customer %4.', SalesCrMemo."No.", SalesCrMemo."Sell-to Customer No.", RelatedSalesInv."No.", RelatedSalesInv."Sell-to Customer No.");
        if SalesCrMemo."Currency Code" <> RelatedSalesInv."Currency Code" then
            Error('Credit memo %1 currency %2 does not match related invoice %3 currency %4.', SalesCrMemo."No.", SalesCrMemo."Currency Code", RelatedSalesInv."No.", RelatedSalesInv."Currency Code");
        if SalesCrMemo."External Document No." <> RelatedSalesInv."External Document No." then
            Error('Credit memo %1 shipment reference %2 does not match related invoice %3 shipment reference %4.', SalesCrMemo."No.", SalesCrMemo."External Document No.", RelatedSalesInv."No.", RelatedSalesInv."External Document No.");

        SalesCrMemoLine.SetRange("Document No.", SalesCrMemo."No.");
        SalesCrMemoLine.SetRange(Type, SalesCrMemoLine.Type::Item);
        if SalesCrMemoLine.FindSet() then
            repeat
                CreditMemoItemCount+=1;
                CreditMemoGrossTotal+=SalesCrMemoLine."Amount Including VAT";
            until SalesCrMemoLine.Next() = 0;
        if CreditMemoItemCount = 0 then
            Error('Credit memo %1 does not contain item lines.', SalesCrMemo."No.");

        SalesInvLine.SetRange("Document No.", RelatedSalesInv."No.");
        SalesInvLine.SetRange(Type, SalesInvLine.Type::Item);
        if SalesInvLine.FindSet() then
            repeat
                RelatedInvoiceItemCount+=1;
                RelatedInvoiceGrossTotal+=SalesInvLine."Amount Including VAT";
            until SalesInvLine.Next() = 0;
        if RelatedInvoiceItemCount = 0 then
            Error('Related invoice %1 does not contain item lines.', RelatedSalesInv."No.");
        if Round(CreditMemoGrossTotal, 0.01, '=') <> Round(RelatedInvoiceGrossTotal, 0.01, '=') then
            Error('Credit memo %1 gross total %2 is not a full match for related invoice %3 gross total %4.', SalesCrMemo."No.", CreditMemoGrossTotal, RelatedSalesInv."No.", RelatedInvoiceGrossTotal);

        exit(BuildFacturaGTLineXml(RelatedSalesInv, GranTotal, GranTotalIVA, GranTotalT, GranTotalIVAT, LastImpuestosinfoSAT));
    end;
    local procedure ResolveFelCountryCode(FelCountryCode: Text; CountryRegionCode: Code[10]; SourceDescription: Text): Text var
        ResolvedCountryCode: Text;
    begin
        ResolvedCountryCode:=DelChr(FelCountryCode, '=', ' ');
        if ResolvedCountryCode = '' then ResolvedCountryCode:=DelChr(CountryRegionCode, '=', ' ');
        ResolvedCountryCode:=UpperCase(ResolvedCountryCode);
        if ResolvedCountryCode = '' then Error('FEL country code is required for %1.', SourceDescription);
        exit(ResolvedCountryCode);
    end;
    local procedure ResolveCancellationIssueDateTime(DateTimeStamped: Text; InvoiceIssueDate: Date): Text var
        OffsetDateTime: Text;
    begin
        OffsetDateTime:=ResolveOffsetDateTimeAsUtc(DateTimeStamped);
        if OffsetDateTime <> '' then exit(OffsetDateTime);
        if StrLen(DateTimeStamped) < 19 then exit(DateTimeStamped);
        if CopyStr(DateTimeStamped, 11, 1) <> 'T' then exit(DateTimeStamped);
        if InvoiceIssueDate <> 0D then exit(Format(InvoiceIssueDate, 0, '<Year4>-<Month,2>-<Day,2>') + CopyStr(DateTimeStamped, 11, 9));
        exit(CopyStr(DateTimeStamped, 1, 19));
    end;
    local procedure ResolveOffsetDateTimeAsUtc(DateTimeStamped: Text): Text var
        StampedDate: Date;
        OffsetSign: Text;
        YearNo: Integer;
        MonthNo: Integer;
        DayNo: Integer;
        HourNo: Integer;
        MinuteNo: Integer;
        SecondNo: Integer;
        OffsetHourNo: Integer;
        OffsetMinuteNo: Integer;
        UtcMinutes: Integer;
    begin
        if StrLen(DateTimeStamped) < 25 then exit('');
        if CopyStr(DateTimeStamped, 11, 1) <> 'T' then exit('');
        OffsetSign:=CopyStr(DateTimeStamped, 20, 1);
        if(OffsetSign <> '+') and (OffsetSign <> '-')then exit('');
        if CopyStr(DateTimeStamped, 23, 1) <> ':' then exit('');
        if not Evaluate(YearNo, CopyStr(DateTimeStamped, 1, 4))then exit('');
        if not Evaluate(MonthNo, CopyStr(DateTimeStamped, 6, 2))then exit('');
        if not Evaluate(DayNo, CopyStr(DateTimeStamped, 9, 2))then exit('');
        if not Evaluate(HourNo, CopyStr(DateTimeStamped, 12, 2))then exit('');
        if not Evaluate(MinuteNo, CopyStr(DateTimeStamped, 15, 2))then exit('');
        if not Evaluate(SecondNo, CopyStr(DateTimeStamped, 18, 2))then exit('');
        if not Evaluate(OffsetHourNo, CopyStr(DateTimeStamped, 21, 2))then exit('');
        if not Evaluate(OffsetMinuteNo, CopyStr(DateTimeStamped, 24, 2))then exit('');
        StampedDate:=DMY2Date(DayNo, MonthNo, YearNo);
        UtcMinutes:=(HourNo * 60) + MinuteNo;
        if OffsetSign = '-' then UtcMinutes+=(OffsetHourNo * 60) + OffsetMinuteNo
        else
            UtcMinutes-=(OffsetHourNo * 60) + OffsetMinuteNo;
        while UtcMinutes >= 1440 do begin
            StampedDate+=1;
            UtcMinutes-=1440;
        end;
        while UtcMinutes < 0 do begin
            StampedDate-=1;
            UtcMinutes+=1440;
        end;
        exit(Format(StampedDate, 0, '<Year4>-<Month,2>-<Day,2>') + 'T' + PadTwoDigits(UtcMinutes div 60) + ':' + PadTwoDigits(UtcMinutes mod 60) + ':' + PadTwoDigits(SecondNo));
    end;
    local procedure PadTwoDigits(Value: Integer): Text begin
        if Value < 10 then exit('0' + Format(Value));
        exit(Format(Value));
    end;
    local procedure ResolveCreditMemoRelatedInvoice(SalesCrMemo: Record "Sales Cr.Memo Header"; var RelatedInvoiceNo: Code[20]; var RelatedUuid: Text; var RelatedSerie: Text; var RelatedNumero: Text; var RelatedIssueDate: Text)
    var
        RelatedSalesInv: Record "Sales Invoice Header";
        RelatedSalesInvRef: RecordRef;
        RelationDocRef: RecordRef;
        RelationFieldRef: FieldRef;
        RelatedDateTimeStamped: Text;
        RelatedUtcDateTime: Text;
        RelatedIssueDateValue: Date;
    begin
        RelationDocRef.Open(27006); // CFDI Relation Document.
        SetFieldFilter(RelationDocRef, 'Document Table ID', Format(Database::"Sales Cr.Memo Header"));
        SetFieldFilter(RelationDocRef, 'Customer No.', SalesCrMemo."Bill-to Customer No.");
        SetFieldFilter(RelationDocRef, 'Document No.', SalesCrMemo."No.");
        if not RelationDocRef.FindFirst()then Error('No se encontro ningun CFDI asociado a la nota de credito %1.', SalesCrMemo."No.");
        if not TryFindFieldRef(RelationDocRef, 'Related Doc. No.', RelationFieldRef)then Error('Business Central field Related Doc. No. is not available on %1.', RelationDocRef.Name());
        RelatedInvoiceNo:=CopyStr(Format(RelationFieldRef.Value()), 1, MaxStrLen(RelatedInvoiceNo));
        if not TryFindFieldRef(RelationDocRef, 'Fiscal Invoice Number PAC', RelationFieldRef)then Error('Business Central field Fiscal Invoice Number PAC is not available on %1.', RelationDocRef.Name());
        RelatedUuid:=Format(RelationFieldRef.Value());
        if RelatedInvoiceNo = '' then Error('Credit memo %1 does not have a related invoice number.', SalesCrMemo."No.");
        if RelatedUuid = '' then Error('Credit memo %1 does not have the related invoice UUID.', SalesCrMemo."No.");
        if not RelatedSalesInv.Get(RelatedInvoiceNo)then Error('No se ha localizado ninguna factura asociada al documento con identificador %1.', RelatedInvoiceNo);
        RelatedSerie:=RelatedSalesInv.serie;
        RelatedNumero:=RelatedSalesInv.numero;
        RelatedSalesInvRef.GetTable(RelatedSalesInv);
        RelatedDateTimeStamped:=ReadTextField(RelatedSalesInvRef, 'Date/Time Stamped');
        if StrLen(RelatedDateTimeStamped) >= 10 then begin
            RelatedIssueDate:=CopyStr(RelatedDateTimeStamped, 1, 10);
            exit;
        end;
        RelatedUtcDateTime:=ResolveOffsetDateTimeAsUtc(RelatedDateTimeStamped);
        if RelatedUtcDateTime <> '' then begin
            RelatedIssueDate:=CopyStr(RelatedUtcDateTime, 1, 10);
            exit;
        end;
        RelatedIssueDateValue:=RelatedSalesInv."Document Date";
        if RelatedIssueDateValue = 0D then RelatedIssueDateValue:=RelatedSalesInv."Posting Date";
        if RelatedIssueDateValue = 0D then Error('Related invoice %1 does not have a document or posting date.', RelatedInvoiceNo);
        RelatedIssueDate:=Format(RelatedIssueDateValue, 0, '<Year4>-<Month,2>-<Day,2>');
    end;
    local procedure FindOpenCustomerLedgerEntry(var CustLedgEntry: Record "Cust. Ledger Entry"; DocumentType: Enum "Gen. Journal Document Type"; DocumentNo: Code[20])
    begin
        CustLedgEntry.Reset();
        CustLedgEntry.SetRange("Document Type", DocumentType);
        CustLedgEntry.SetRange("Document No.", DocumentNo);
        CustLedgEntry.SetRange(Open, true);
        if not CustLedgEntry.FindFirst()then Error('Open customer ledger entry was not found for %1 %2.', Format(DocumentType), DocumentNo);
    end;
    local procedure BuildFacturaGTLineXml(SalesInv: Record "Sales Invoice Header"; var GranTotal: Decimal; var GranTotalIVA: Decimal; var GranTotalT: Text; var GranTotalIVAT: Text; var LastImpuestosinfoSAT: Record "VAT Product Posting Group"): Text var
        LineasVenta: Record "Sales Invoice Line";
        Producto: Record Item;
        ImpuestosinfoSAT: Record "VAT Product Posting Group";
        SubBody: Text;
        Subimpuestos: Text;
        Subtotal: Decimal;
        TotalIVA: Decimal;
        Total: Decimal;
        SubtotalFactura: Text;
        TotalFactura: Text;
        ImporteTotalIVA: Text;
        PrecioUnitario: Text;
        PrecioTotal: Text;
        NumLine: Integer;
    begin
        LineasVenta.SetRange("Document No.", SalesInv."No.");
        LineasVenta.SetRange(Type, LineasVenta.Type::Item);
        if not LineasVenta.FindSet()then exit('');
        repeat NumLine+=1;
            Producto.Get(LineasVenta."No.");
            ImpuestosinfoSAT.Get(LineasVenta."VAT Prod. Posting Group");
            LastImpuestosinfoSAT:=ImpuestosinfoSAT;
            if LineasVenta."VAT %" > 0 then Subtotal:=Round(LineasVenta."Amount Including VAT" / 1.12, 0.01, '=')
            else
                Subtotal:=LineasVenta."Amount Including VAT";
            Total:=LineasVenta."Amount Including VAT";
            TotalIVA:=LineasVenta."Amount Including VAT" - Subtotal;
            GranTotal+=LineasVenta."Amount Including VAT";
            GranTotalIVA+=TotalIVA;
            SubtotalFactura:=FormatDecimalNoComma(Subtotal);
            TotalFactura:=FormatDecimalNoComma(Total);
            ImporteTotalIVA:=FormatDecimalNoComma(TotalIVA);
            GranTotalT:=FormatDecimalNoComma(GranTotal);
            GranTotalIVAT:=FormatDecimalNoComma(GranTotalIVA);
            PrecioUnitario:=FormatDecimalNoComma(LineasVenta."Amount Including VAT" / LineasVenta.Quantity);
            PrecioTotal:=FormatDecimalNoComma(LineasVenta."Amount Including VAT");
            Subimpuestos:='<dte:Impuestos><dte:Impuesto><dte:NombreCorto>' + EscapeXml(ImpuestosinfoSAT."Nombre Corto -GT") + '</dte:NombreCorto><dte:CodigoUnidadGravable>' + EscapeXml(ImpuestosinfoSAT."Codigo Unidad Gravable") + '</dte:CodigoUnidadGravable><dte:MontoGravable>' + SubtotalFactura + '</dte:MontoGravable><dte:MontoImpuesto>' + ImporteTotalIVA + '</dte:MontoImpuesto></dte:Impuesto></dte:Impuestos><dte:Total>' + TotalFactura + '</dte:Total></dte:Item>';
            SubBody+='<dte:Item BienOServicio="' + EscapeXml(Producto."Item Category Code") + '" NumeroLinea="' + Format(NumLine) + '">' + '<dte:Cantidad>' + FormatDecimalNoComma(LineasVenta.Quantity) + '</dte:Cantidad>' + '<dte:UnidadMedida>' + EscapeXml(LineasVenta."Unit of Measure Code") + '</dte:UnidadMedida>' + '<dte:Descripcion>' + EscapeXml(GetLineDescription(LineasVenta)) + ' </dte:Descripcion>' + '<dte:PrecioUnitario>' + PrecioUnitario + '</dte:PrecioUnitario>' + '<dte:Precio>' + PrecioTotal + '</dte:Precio>' + '<dte:Descuento>0.00</dte:Descuento>' + Subimpuestos;
        until LineasVenta.Next() = 0;
        exit(SubBody);
    end;
    local procedure BuildNotaCreditoGTLineXml(SalesCrMemo: Record "Sales Cr.Memo Header"; var GranTotal: Decimal; var GranTotalIVA: Decimal; var GranTotalT: Text; var GranTotalIVAT: Text; var LastImpuestosinfoSAT: Record "VAT Product Posting Group"): Text var
        LineasVenta: Record "Sales Cr.Memo Line";
        Producto: Record Item;
        ImpuestosinfoSAT: Record "VAT Product Posting Group";
        SubBody: Text;
        Subimpuestos: Text;
        Subtotal: Decimal;
        TotalIVA: Decimal;
        Total: Decimal;
        SubtotalFactura: Text;
        TotalFactura: Text;
        ImporteTotalIVA: Text;
        PrecioUnitario: Text;
        PrecioTotal: Text;
        NumLine: Integer;
    begin
        LineasVenta.SetRange("Document No.", SalesCrMemo."No.");
        LineasVenta.SetRange(Type, LineasVenta.Type::Item);
        if not LineasVenta.FindSet()then exit('');
        repeat NumLine+=1;
            Producto.Get(LineasVenta."No.");
            ImpuestosinfoSAT.Get(LineasVenta."VAT Prod. Posting Group");
            LastImpuestosinfoSAT:=ImpuestosinfoSAT;
            if LineasVenta."VAT %" > 0 then Subtotal:=Round(LineasVenta."Amount Including VAT" / 1.12, 0.01, '=')
            else
                Subtotal:=LineasVenta."Amount Including VAT";
            Total:=LineasVenta."Amount Including VAT";
            TotalIVA:=LineasVenta."Amount Including VAT" - Subtotal;
            GranTotal+=LineasVenta."Amount Including VAT";
            GranTotalIVA+=TotalIVA;
            SubtotalFactura:=FormatDecimalNoComma(Subtotal);
            TotalFactura:=FormatDecimalNoComma(Total);
            ImporteTotalIVA:=FormatDecimalNoComma(TotalIVA);
            GranTotalT:=FormatDecimalNoComma(GranTotal);
            GranTotalIVAT:=FormatDecimalNoComma(GranTotalIVA);
            PrecioUnitario:=FormatDecimalNoComma(LineasVenta."Amount Including VAT" / LineasVenta.Quantity);
            PrecioTotal:=FormatDecimalNoComma(LineasVenta."Amount Including VAT");
            Subimpuestos:='<dte:Impuestos><dte:Impuesto><dte:NombreCorto>' + EscapeXml(ImpuestosinfoSAT."Nombre Corto -GT") + '</dte:NombreCorto><dte:CodigoUnidadGravable>' + EscapeXml(ImpuestosinfoSAT."Codigo Unidad Gravable") + '</dte:CodigoUnidadGravable><dte:MontoGravable>' + SubtotalFactura + '</dte:MontoGravable><dte:MontoImpuesto>' + ImporteTotalIVA + '</dte:MontoImpuesto></dte:Impuesto></dte:Impuestos><dte:Total>' + TotalFactura + '</dte:Total></dte:Item>';
            SubBody+='<dte:Item BienOServicio="' + EscapeXml(Producto."Item Category Code") + '" NumeroLinea="' + Format(NumLine) + '">' + '<dte:Cantidad>' + FormatDecimalNoComma(LineasVenta.Quantity) + '</dte:Cantidad>' + '<dte:UnidadMedida>' + EscapeXml(LineasVenta."Unit of Measure Code") + '</dte:UnidadMedida>' + '<dte:Descripcion>' + EscapeXml(GetLineDescription(LineasVenta)) + ' </dte:Descripcion>' + '<dte:PrecioUnitario>' + PrecioUnitario + '</dte:PrecioUnitario>' + '<dte:Precio>' + PrecioTotal + '</dte:Precio>' + '<dte:Descuento>0.00</dte:Descuento>' + Subimpuestos;
        until LineasVenta.Next() = 0;
        exit(SubBody);
    end;
    local procedure ApplyStampResponse(SalesInv: Record "Sales Invoice Header"; Resultado: Boolean; MensajeError: Text; UUID: Text; XmlCertificado: Text; FechaTimbre: Text; Serie: Text; Numero: Text)
    var
        Modifica: Record "Sales Invoice Header";
        ModificaRef: RecordRef;
    begin
        Modifica.Get(SalesInv."No.");
        ModificaRef.GetTable(Modifica);
        if Resultado then begin
            WriteTextField(ModificaRef, 'Date/Time Stamped', FechaTimbre);
            WriteTextField(ModificaRef, 'Fiscal Invoice Number PAC', UUID);
            WriteTextField(ModificaRef, 'serie', Serie);
            WriteTextField(ModificaRef, 'numero', Numero);
            WriteBooleanField(ModificaRef, 'Electronic Document Sent', true);
            WriteOptionField(ModificaRef, 'Electronic Document Status', 'Stamp Received');
            WriteTextField(ModificaRef, 'Error Description', '');
            ModificaRef.Modify();
            exit;
        end;
        WriteOptionField(ModificaRef, 'Electronic Document Status', 'Stamp Request Error');
        WriteTextField(ModificaRef, 'Error Description', MensajeError);
        ModificaRef.Modify();
    end;
    local procedure ApplyCreditMemoStampResponse(SalesCrMemo: Record "Sales Cr.Memo Header"; Resultado: Boolean; MensajeError: Text; UUID: Text; XmlCertificado: Text; FechaTimbre: Text; Serie: Text; Numero: Text; OriginalXml: Text)
    var
        Modifica: Record "Sales Cr.Memo Header";
        ModificaRef: RecordRef;
    begin
        Modifica.Get(SalesCrMemo."No.");
        ModificaRef.GetTable(Modifica);
        if Resultado then begin
            WriteTextField(ModificaRef, 'Date/Time Stamped', FechaTimbre);
            WriteTextField(ModificaRef, 'Fiscal Invoice Number PAC', UUID);
            WriteTextField(ModificaRef, 'serie', Serie);
            WriteTextField(ModificaRef, 'numero', Numero);
            WriteBooleanField(ModificaRef, 'Electronic Document Sent', true);
            WriteOptionField(ModificaRef, 'Electronic Document Status', 'Stamp Received');
            WriteTextField(ModificaRef, 'Error Description', '');
            ModificaRef.Modify();
            exit;
        end;
        WriteOptionField(ModificaRef, 'Electronic Document Status', 'Stamp Request Error');
        WriteTextField(ModificaRef, 'Error Description', MensajeError);
        ModificaRef.Modify();
    end;
    local procedure ApplyCancelResponse(SalesInv: Record "Sales Invoice Header"; Resultado: Boolean; MensajeError: Text; UUID: Text; XmlCertificado: Text; Serie: Text)
    var
        Modifica: Record "Sales Invoice Header";
        ModificaRef: RecordRef;
    begin
        Modifica.Get(SalesInv."No.");
        ModificaRef.GetTable(Modifica);
        ApplyCancelResponseToRecord(ModificaRef, Resultado, MensajeError, UUID, XmlCertificado, Serie);
    end;
    local procedure ApplyCreditMemoCancelResponse(SalesCrMemo: Record "Sales Cr.Memo Header"; Resultado: Boolean; MensajeError: Text; UUID: Text; XmlCertificado: Text; Serie: Text)
    var
        Modifica: Record "Sales Cr.Memo Header";
        ModificaRef: RecordRef;
    begin
        Modifica.Get(SalesCrMemo."No.");
        ModificaRef.GetTable(Modifica);
        ApplyCancelResponseToRecord(ModificaRef, Resultado, MensajeError, UUID, XmlCertificado, Serie);
    end;
    local procedure ApplyCancelResponseToRecord(var ModificaRef: RecordRef; Resultado: Boolean; MensajeError: Text; UUID: Text; XmlCertificado: Text; Serie: Text)
    begin
        if Resultado then begin
            WriteTextField(ModificaRef, 'CancelaGTUUID', UUID);
            // The cancellation response identifies the cancellation transaction, not
            // the original DTE. Preserve the original fiscal series because a later
            // corrective credit memo must reference that exact series at FEL.
            WriteBooleanField(ModificaRef, 'Electronic Document Sent', true);
            WriteOptionField(ModificaRef, 'Electronic Document Status', 'Canceled');
            WriteTextField(ModificaRef, 'Error Description', '');
            ModificaRef.Modify();
            exit;
        end;
        WriteOptionField(ModificaRef, 'Electronic Document Status', 'Cancel Error');
        WriteTextField(ModificaRef, 'Error Description', MensajeError);
        ModificaRef.Modify();
    end;
    local procedure ReadTextField(var SourceRef: RecordRef; FieldName: Text): Text var
        SourceFieldRef: FieldRef;
    begin
        if not TryFindFieldRef(SourceRef, FieldName, SourceFieldRef)then Error('Business Central field %1 is not available on %2.', FieldName, SourceRef.Name());
        exit(Format(SourceFieldRef.Value()));
    end;
    local procedure WriteTextField(var SourceRef: RecordRef; FieldName: Text; Value: Text)
    var
        TargetFieldRef: FieldRef;
    begin
        if not TryFindFieldRef(SourceRef, FieldName, TargetFieldRef)then Error('Business Central field %1 is not available on %2.', FieldName, SourceRef.Name());
        TargetFieldRef.Value:=CopyStr(Value, 1, TargetFieldRef.Length());
    end;
    local procedure WriteBooleanField(var SourceRef: RecordRef; FieldName: Text; Value: Boolean)
    var
        TargetFieldRef: FieldRef;
    begin
        if not TryFindFieldRef(SourceRef, FieldName, TargetFieldRef)then Error('Business Central field %1 is not available on %2.', FieldName, SourceRef.Name());
        TargetFieldRef.Value:=Value;
    end;
    local procedure WriteOptionField(var SourceRef: RecordRef; FieldName: Text; Value: Text)
    var
        TargetFieldRef: FieldRef;
    begin
        if not TryFindFieldRef(SourceRef, FieldName, TargetFieldRef)then Error('Business Central field %1 is not available on %2.', FieldName, SourceRef.Name());
        Evaluate(TargetFieldRef, Value);
    end;
    local procedure TryFindFieldRef(var SourceRef: RecordRef; FieldName: Text; var TargetFieldRef: FieldRef): Boolean var
        FieldIndex: Integer;
        CandidateFieldRef: FieldRef;
    begin
        for FieldIndex:=1 to SourceRef.FieldCount()do begin
            CandidateFieldRef:=SourceRef.FieldIndex(FieldIndex);
            if(UpperCase(CandidateFieldRef.Name()) = UpperCase(FieldName)) or (UpperCase(CandidateFieldRef.Caption()) = UpperCase(FieldName))then begin
                TargetFieldRef:=CandidateFieldRef;
                exit(true);
            end;
        end;
        exit(false);
    end;
    local procedure ExtractJsonData(TipoNC: Boolean; JsonResponse: Text; var Resultado: Boolean; var MensajeError: Text; var UUID: Text; var XmlCertificado: Text; var Fecha: Text; var Serie: Text; var Numero: Text)
    var
        JsonObj: JsonObject;
        JsonToken: JsonToken;
        JsonArray: JsonArray;
        ArrayElement: JsonObject;
    begin
        if not JsonObj.ReadFrom(JsonResponse)then Error('El texto proporcionado no es un objeto JSON válido.');
        if JsonObj.Get('resultado', JsonToken)then Resultado:=JsonToken.AsValue().AsBoolean();
        if JsonObj.Get('fecha', JsonToken)then Fecha:=JsonToken.AsValue().AsText();
        if JsonObj.Get('descripcion_errores', JsonToken)then begin
            JsonArray:=JsonToken.AsArray();
            if JsonArray.Get(0, JsonToken)then begin
                ArrayElement:=JsonToken.AsObject();
                if ArrayElement.Get('mensaje_error', JsonToken)then MensajeError:=JsonToken.AsValue().AsText();
            end;
        end;
        if JsonObj.Get('descripcion', JsonToken) and (MensajeError = '')then MensajeError:=JsonToken.AsValue().AsText();
        if JsonObj.Get('uuid', JsonToken)then UUID:=JsonToken.AsValue().AsText();
        if JsonObj.Get('serie', JsonToken)then Serie:=JsonToken.AsValue().AsText();
        if not TipoNC then if JsonObj.Get('numero', JsonToken)then Numero:=JsonToken.AsValue().AsText();
        if JsonObj.Get('xml_certificado', JsonToken)then XmlCertificado:=JsonToken.AsValue().AsText();
    end;
    local procedure GetLineDescription(SalesInvoiceLine: Record "Sales Invoice Line"): Text begin
        if SalesInvoiceLine."Description XL" <> '' then exit(SalesInvoiceLine."Description XL");
        exit(SalesInvoiceLine.Description);
    end;
    local procedure GetLineDescription(SalesCrMemoLine: Record "Sales Cr.Memo Line"): Text begin
        if SalesCrMemoLine."Description XL" <> '' then exit(SalesCrMemoLine."Description XL");
        exit(SalesCrMemoLine.Description);
    end;
    local procedure SetFieldFilter(var SourceRef: RecordRef; FieldName: Text; Value: Text)
    var
        SourceFieldRef: FieldRef;
    begin
        if not TryFindFieldRef(SourceRef, FieldName, SourceFieldRef)then Error('Business Central field %1 is not available on %2.', FieldName, SourceRef.Name());
        SourceFieldRef.SetFilter('%1', Value);
    end;
    local procedure FormatDecimalNoComma(Value: Decimal): Text var
        TextValue: Text;
    begin
        TextValue:=Format(Value);
        TextValue:=DelChr(TextValue, '=', ',');
        exit(TextValue);
    end;
    local procedure ObtenerFolioVenta(NoFactura: Text; var RegresoFolio: Text)
    begin
        RegresoFolio:=DelChr(NoFactura, '=', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz');
    end;
    local procedure SeparacionAddenda(Factura: Text; var Letras: Text; var Numeros: Text)
    begin
        Letras:=DelChr(Factura, '=', '0123456789');
        Numeros:=DelChr(Factura, '=', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ');
    end;
    local procedure EscapeXml(Value: Text): Text begin
        Value:=Value.Replace('&', '&amp;');
        Value:=Value.Replace('<', '&lt;');
        Value:=Value.Replace('>', '&gt;');
        Value:=Value.Replace('"', '&quot;');
        Value:=Value.Replace('''', '&apos;');
        exit(Value);
    end;
}
