page 71040 "MTM MX Cancellation API"
{
    PageType = API;
    APIPublisher = 'mtmlogix';
    APIGroup = 'invoiceSync';
    APIVersion = 'v1.0';
    EntityName = 'mxCancellation';
    EntitySetName = 'mxCancellations';
    SourceTable = "MTM MX Cancellation";
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
                field(id; Rec.SystemId) { }
                field(invoiceNumber; Rec."Invoice No.") { }
                field(replacementNumber; Rec."Replacement No.") { }
                field(originalUuid; Rec."Original UUID") { }
                field(replacementUuid; Rec."Replacement UUID") { }
                field(externalDocumentNumber; Rec."External Document No.") { }
                field(amount; Rec.Amount) { }
                field(replacementDueDate; Rec."Replacement Due Date") { }
                field(state; Rec.State) { }
                field(requestedAt; Rec."Requested At") { }
                field(checkedAt; Rec."Checked At") { }
                field(completedAt; Rec."Completed At") { }
                field(satStatus; Rec."SAT Status") { }
                field(satCancellationStatus; Rec."SAT Cancellation Status") { }
                field(resultCode; Rec."Result Code") { }
                field(creditMemoNumber; Rec."Credit Memo No.") { }
                field(accountingAttemptedAt; Rec."Accounting Attempted At") { }
            }
        }
    }
    [ServiceEnabled]
    procedure RefreshStatus(var ActionContext: WebServiceActionContext)
    var
        Management: Codeunit "MTM MX Cancellation Mgt";
    begin
        Management.Refresh(Rec);
        SetActionResult(ActionContext);
    end;
    [ServiceEnabled]
    procedure FinalizeAccounting(var ActionContext: WebServiceActionContext)
    var
        Management: Codeunit "MTM MX Cancellation Mgt";
    begin
        Management.Finalize(Rec);
        SetActionResult(ActionContext);
    end;
    local procedure SetActionResult(var ActionContext: WebServiceActionContext)
    begin
        ActionContext.SetObjectType(ObjectType::Page);
        ActionContext.SetObjectId(Page::"MTM MX Cancellation API");
        ActionContext.AddEntityKey(Rec.FieldNo(SystemId), Rec.SystemId);
        ActionContext.SetResultCode(WebServiceActionResultCode::Updated);
    end;
}
