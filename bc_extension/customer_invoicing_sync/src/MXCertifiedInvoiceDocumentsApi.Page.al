page 71043 "MTM MX Certified Docs API"
{
    PageType = API;
    APIPublisher = 'mtmlogix';
    APIGroup = 'invoiceSync';
    APIVersion = 'v1.0';
    EntityName = 'mxCertifiedInvoiceDocument';
    EntitySetName = 'mxCertifiedInvoiceDocuments';
    SourceTable = "Sales Invoice Header";
    ODataKeyFields = SystemId;
    InsertAllowed = false;
    ModifyAllowed = false;
    DeleteAllowed = false;
    Extensible = false;
    layout
    {
        area(Content)
        {
            repeater(General)
            {
                field(id; Rec.SystemId) { Editable = false; }
                field(number; Rec."No.") { Editable = false; }
                field(pdfBase64Content; Rec.PDF_64) { Editable = false; }
                field(certifiedXmlBase64; CertifiedXmlBase64) { Editable = false; }
            }
        }
    }
    trigger OnOpenPage()
    begin
        if CompanyName() <> 'MTM_MX_PROD' then
            Error('Certified Mexico document access is scoped to MTM_MX_PROD.');
        Rec.SetRange("Sell-to Customer No.", 'C00067');
        Rec.SetRange("Currency Code", 'USD');
    end;
    trigger OnAfterGetRecord()
    var
        InvoiceRef: RecordRef;
        BlobField: FieldRef;
        TempBlob: Codeunit "Temp Blob";
        Base64Convert: Codeunit "Base64 Convert";
        Stream: InStream;
        Index: Integer;
    begin
        Rec.CalcFields(PDF_64);
        CertifiedXmlBase64 := '';
        InvoiceRef.GetTable(Rec);
        for Index := 1 to InvoiceRef.FieldCount() do begin
            BlobField := InvoiceRef.FieldIndex(Index);
            if BlobField.Name() = 'Original Document XML' then begin
                BlobField.CalcField();
                TempBlob.FromFieldRef(BlobField);
                TempBlob.CreateInStream(Stream);
                CertifiedXmlBase64 := Base64Convert.ToBase64(Stream);
                exit;
            end;
        end;
        Error('The native Mexico certified XML field is unavailable.');
    end;
    var
        CertifiedXmlBase64: Text;
}
