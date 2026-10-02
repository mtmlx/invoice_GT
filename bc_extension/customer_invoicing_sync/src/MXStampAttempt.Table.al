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
        field(11; "Attempt Count"; Integer) { }
        field(12; "Previous Attempted At UTC"; DateTime) { }
        field(13; "Previous Error Code"; Text[30]) { }
        field(14; "Previous Diagnostic"; Text[2048]) { }
        field(15; "Previous HTTP Status"; Integer) { }
    }

    keys
    {
        key(PK; "Invoice No.") { Clustered = true; }
    }

    procedure IsConfirmedFxRejection(ExpectedAttemptAt: DateTime; ExpectedTotal: Decimal): Boolean
    begin
        exit((Outcome = Outcome::Rejected) and ("HTTP Status" = 200) and
            ("Error Code" = '101') and ("Fiscal UUID" = '') and
            ("Attempt Count" >= 0) and ("Attempt Count" <= 1) and
            (ExpectedAttemptAt <> 0DT) and ("Attempted At UTC" = ExpectedAttemptAt) and
            (ExpectedTotal > 0) and ("Approved Total" = ExpectedTotal) and
            (StrPos(LowerCase(Diagnostic), 'tipo de cambio a 4 decimales') > 0));
    end;
}
