pageextension 71040 "MTM MX Cancellation Guard" extends "Posted Sales Invoice"
{
    actions
    {
        modify(CancelInvoice)
        {
            trigger OnBeforeAction()
            begin
                RequireControlledCancellation();
            end;
        }
        modify("Cancela MX")
        {
            trigger OnBeforeAction()
            begin
                RequireControlledCancellation();
            end;
        }
    }
    local procedure RequireControlledCancellation()
    begin
        if (CompanyName() = 'MTM_MX_PROD') and
           ((Rec."Sell-to Customer No." = 'C00067') or (Rec."Bill-to Customer No." = 'C00067'))
        then
            Error('TAGOMAGO cancellation must use the controlled Mexico replacement process. Fiscal confirmation is required before accounting reversal.');
    end;
}
