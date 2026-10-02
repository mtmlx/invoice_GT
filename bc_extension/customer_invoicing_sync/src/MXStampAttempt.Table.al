table 71042 "MTM MX Stamp Attempt"
{
    Caption = 'MTM Mexico Stamp Attempt';
    DataClassification = CustomerContent;

    fields
    {
        field(1; "Invoice No."; Code[20]) { }
        field(2; "Invoice SystemId"; Guid) { }
        field(3; "Attempted At UTC"; DateTime) { }
        field(4; Outcome; Option)
        {
            OptionMembers = Unknown,Rejected,Stamped;
            OptionCaption = 'Unknown,Rejected,Stamped';
        }
        field(5; "HTTP Status"; Integer) { }
        field(6; "Fiscal UUID"; Text[50]) { }
        field(7; "Error Code"; Text[30]) { }
        field(8; Diagnostic; Text[2048]) { }
        field(9; "Approved Total"; Decimal) { }
        field(10; "Fiscal Timestamp"; Text[19]) { }
    }

    keys
    {
        key(PK; "Invoice No.") { Clustered = true; }
    }
}
