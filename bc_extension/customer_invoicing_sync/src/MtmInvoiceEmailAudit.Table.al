table 71011 "MTM Invoice Email Audit"
{
    Caption = 'MTM Invoice Email Audit';
    DataClassification = CustomerContent;

    fields
    {
        field(1; "Posted Invoice System Id"; Guid) { DataClassification = SystemMetadata; }
        field(2; "Posted Invoice No."; Code[20]) { DataClassification = CustomerContent; }
        field(3; "Customer No."; Code[20]) { DataClassification = CustomerContent; }
        field(4; Recipient; Text[250]) { DataClassification = CustomerContent; }
        field(5; "Sender Email"; Text[250]) { DataClassification = CustomerContent; }
        field(6; Status; Option)
        {
            DataClassification = SystemMetadata;
            OptionMembers = Pending,Sent,Failed;
            OptionCaption = 'Pending,Sent,Failed';
        }
        field(7; "Sent At"; DateTime) { DataClassification = SystemMetadata; }
        field(8; "Error Text"; Text[2048]) { DataClassification = CustomerContent; }
        field(9; "Attempt Count"; Integer) { DataClassification = SystemMetadata; }
        field(10; "Report Layout Name"; Text[100]) { DataClassification = SystemMetadata; }
        field(11; "BC Email Message Id"; Guid) { DataClassification = SystemMetadata; }
        field(12; "Sender Account Id"; Guid) { DataClassification = SystemMetadata; }
        field(13; "Native Send Accepted"; Boolean) { DataClassification = SystemMetadata; }
        field(14; "Native Sent Verified"; Boolean) { DataClassification = SystemMetadata; }
        field(15; "Last Attempt At"; DateTime) { DataClassification = SystemMetadata; }
        field(16; "Delivery Prepared"; Boolean) { DataClassification = SystemMetadata; }
        field(17; "CC Recipients"; Text[250]) { DataClassification = CustomerContent; }
        field(18; "Fiscal UUID"; Text[50]) { DataClassification = CustomerContent; }
        field(19; "PDF Attachment SHA256"; Text[64]) { DataClassification = SystemMetadata; }
        field(20; "XML Attachment SHA256"; Text[64]) { DataClassification = SystemMetadata; }
        field(21; "Expected External Document No."; Code[35]) { DataClassification = CustomerContent; }
        field(22; "Expected Amount Including VAT"; Decimal) { DataClassification = CustomerContent; }
        field(23; "Expected Due Date"; Date) { DataClassification = CustomerContent; }
        field(24; "Prepared At"; DateTime) { DataClassification = SystemMetadata; }
    }

    keys
    {
        key(PK; "Posted Invoice System Id") { Clustered = true; }
        key(InvoiceNo; "Posted Invoice No.") { }
    }

    trigger OnModify()
    begin
        if not xRec."Delivery Prepared" then
            exit;
        // Delivery status/evidence may advance, but approved recipient/document intent may not.
        TestField("Delivery Prepared", true);
        TestField(Recipient, xRec.Recipient);
        TestField("CC Recipients", xRec."CC Recipients");
        TestField("Fiscal UUID", xRec."Fiscal UUID");
        TestField("PDF Attachment SHA256", xRec."PDF Attachment SHA256");
        TestField("XML Attachment SHA256", xRec."XML Attachment SHA256");
        TestField("Expected External Document No.", xRec."Expected External Document No.");
        TestField("Expected Amount Including VAT", xRec."Expected Amount Including VAT");
        TestField("Expected Due Date", xRec."Expected Due Date");
        TestField("Prepared At", xRec."Prepared At");
        TestField("Posted Invoice No.", xRec."Posted Invoice No.");
        TestField("Customer No.", xRec."Customer No.");
    end;

    trigger OnDelete()
    begin
        if "Delivery Prepared" then
            Error('An approved Mexico delivery audit cannot be deleted.');
    end;

}
