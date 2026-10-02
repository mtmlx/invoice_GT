table 71040 "MTM MX Cancellation"
{
    DataClassification = CustomerContent;
    fields
    {
        field(1; "Invoice No."; Code[20]) { }
        field(2; "Replacement No."; Code[20]) { }
        field(3; "Original UUID"; Text[50]) { }
        field(4; "Replacement UUID"; Text[50]) { }
        field(5; "External Document No."; Code[35]) { }
        field(6; "Customer No."; Code[20]) { }
        field(7; "Currency Code"; Code[10]) { }
        field(8; Amount; Decimal) { }
        field(9; "Replacement Due Date"; Date) { }
        field(10; State; Option)
        {
            OptionMembers = Unknown,Pending,Rejected,Confirmed,Completed;
        }
        field(11; "Requested At"; DateTime) { }
        field(12; "Checked At"; DateTime) { }
        field(13; "Completed At"; DateTime) { }
        field(14; "Issuer RFC"; Text[30]) { }
        field(15; "Recipient RFC"; Text[30]) { }
        field(16; "SAT Status"; Text[100]) { }
        field(17; "SAT Cancellation Status"; Text[100]) { }
        field(18; "Result Code"; Text[100]) { }
        field(19; "Fiscal Receipt"; Blob) { }
        field(20; "SAT Evidence"; Blob) { }
        field(21; "Requested By"; Guid) { }
        field(22; "Credit Memo No."; Code[20]) { }
        field(23; "Accounting Attempted At"; DateTime) { }
        field(24; "Original Amount"; Decimal) { }
        field(25; "Final Invoice No."; Code[20]) { }
        field(26; "Final UUID"; Text[50]) { }
        field(27; "Final Amount"; Decimal) { }
        field(28; "Final Due Date"; Date) { }
        field(29; "Reason Description"; Text[250]) { }
        field(30; "Final SAT Checked At"; DateTime) { }
        field(31; "Final SAT Status"; Text[100]) { }
    }
    keys { key(PK; "Invoice No.") { Clustered = true; } }

    procedure InvoiceAmount(IsReplacement: Boolean): Decimal
    begin
        // Older operations used Amount for both documents.
        if IsReplacement or ("Original Amount" = 0) then
            exit(Amount);
        exit("Original Amount");
    end;
}
