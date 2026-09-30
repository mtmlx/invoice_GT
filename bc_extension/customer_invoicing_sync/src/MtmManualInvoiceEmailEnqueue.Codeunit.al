codeunit 71015 "MTM Manual Inv Email Enqueue"
{
    InherentEntitlements = X;
    InherentPermissions = X;
    Permissions = tabledata "MTM Manual Inv Email Setup"=R,
        tabledata "MTM Manual Inv Email Queue"=RIMD;

    [EventSubscriber(ObjectType::Table, Database::"Sales Invoice Header", 'OnAfterInsertEvent', '', false, true)]
    local procedure SalesInvoiceHeaderOnAfterInsert(var Rec: Record "Sales Invoice Header"; RunTrigger: Boolean)
    var
        Setup: Record "MTM Manual Inv Email Setup";
    begin
        // Mexico customer invoice delivery is not configured yet. Keep manual
        // posting independent from the GT-only email queue until MX is enabled.
        if CompanyName() = 'MTM_MX_PROD' then exit;
        if not Setup.Get('')then exit;
        if not Setup."Capture Enabled" then exit;
        if Rec.Cancelled or (Rec."No." = '')then exit;
        if not TryEnqueue(Rec, Setup)then Session.LogMessage('MTMINVEMAIL001', StrSubstNo('Could not enqueue posted invoice %1 for MTM customer email processing.', Rec."No."), Verbosity::Error, DataClassification::CustomerContent, TelemetryScope::ExtensionPublisher, 'PostedInvoiceNo', Rec."No.");
    end;
    [TryFunction]
    local procedure TryEnqueue(PostedInvoice: Record "Sales Invoice Header"; Setup: Record "MTM Manual Inv Email Setup")
    var
        Queue: Record "MTM Manual Inv Email Queue";
        EnqueuedAt: DateTime;
    begin
        if Queue.Get(PostedInvoice.SystemId)then exit;
        EnqueuedAt:=CurrentDateTime();
        Queue.Init();
        Queue."Posted Invoice System Id":=PostedInvoice.SystemId;
        Queue."Posted Invoice No.":=PostedInvoice."No.";
        Queue."Customer No.":=PostedInvoice."Sell-to Customer No.";
        Queue."External Document No.":=PostedInvoice."External Document No.";
        Queue.Status:=Queue.Status::WaitingForStamp;
        Queue."Enqueued At":=EnqueuedAt;
        Queue."Next Attempt At":=EnqueuedAt + (Setup."Initial Delay Seconds" * 1000);
        Queue."Stamp Wait Deadline":=EnqueuedAt + (Setup."Stamp Wait Timeout Minutes" * 60000);
        Queue."Captured By":=CopyStr(UserId(), 1, MaxStrLen(Queue."Captured By"));
        Queue.Insert(true);
    end;
}
