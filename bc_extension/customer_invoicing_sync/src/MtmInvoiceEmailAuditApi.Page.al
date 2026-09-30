page 71012 "MTM Invoice Email Audit API"
{
    PageType = API;
    APIPublisher = 'mtmlogix';
    APIGroup = 'invoiceSync';
    APIVersion = 'v1.0';
    EntityName = 'invoiceEmailDelivery';
    EntitySetName = 'invoiceEmailDeliveries';
    SourceTable = "MTM Invoice Email Audit";
    ODataKeyFields = "Posted Invoice System Id";
    DelayedInsert = false;
    Extensible = false;
    InsertAllowed = false;
    ModifyAllowed = false;
    DeleteAllowed = false;

    layout
    {
        area(Content)
        {
            repeater(General)
            {
                field(postedInvoiceId; Rec."Posted Invoice System Id") { Caption = 'Posted Invoice Id'; }
                field(postedInvoiceNumber; Rec."Posted Invoice No.") { Caption = 'Posted Invoice Number'; }
                field(customerNumber; Rec."Customer No.") { Caption = 'Customer Number'; }
                field(recipient; Rec.Recipient) { Caption = 'Recipient'; }
                field(senderEmail; Rec."Sender Email") { Caption = 'Sender Email'; }
                field(status; StatusTxt) { Caption = 'Status'; }
                field(sentAt; Rec."Sent At") { Caption = 'Sent At'; }
                field(errorText; Rec."Error Text") { Caption = 'Error Text'; }
                field(attemptCount; Rec."Attempt Count") { Caption = 'Attempt Count'; }
                field(reportLayoutName; Rec."Report Layout Name") { Caption = 'Report Layout Name'; }
                field(bcEmailMessageId; Rec."BC Email Message Id") { Caption = 'BC Email Message Id'; }
                field(senderAccountId; Rec."Sender Account Id") { Caption = 'Sender Account Id'; }
                field(nativeSendAccepted; Rec."Native Send Accepted") { Caption = 'Native Send Accepted'; }
                field(nativeSentVerified; Rec."Native Sent Verified") { Caption = 'Native Sent Verified'; }
                field(lastAttemptAt; Rec."Last Attempt At") { Caption = 'Last Attempt At'; }
                field(deliveryPrepared; Rec."Delivery Prepared") { Caption = 'Delivery Prepared'; }
                field(ccRecipients; Rec."CC Recipients") { Caption = 'CC Recipients'; }
                field(fiscalUuid; Rec."Fiscal UUID") { Caption = 'Fiscal UUID'; }
                field(pdfAttachmentSha256; Rec."PDF Attachment SHA256") { Caption = 'PDF Attachment SHA256'; }
                field(xmlAttachmentSha256; Rec."XML Attachment SHA256") { Caption = 'XML Attachment SHA256'; }
                field(expectedExternalDocumentNumber; Rec."Expected External Document No.") { Caption = 'Expected External Document Number'; }
                field(expectedAmountIncludingVat; Rec."Expected Amount Including VAT") { Caption = 'Expected Amount Including VAT'; }
                field(expectedDueDate; Rec."Expected Due Date") { Caption = 'Expected Due Date'; }
                field(preparedAt; Rec."Prepared At") { Caption = 'Prepared At'; }
            }
        }
    }

    trigger OnAfterGetRecord()
    begin
        StatusTxt := Format(Rec.Status);
    end;

    var
        StatusTxt: Text[30];
}
