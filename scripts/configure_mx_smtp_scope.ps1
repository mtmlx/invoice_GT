param(
    [switch]$Apply,
    [Parameter(Mandatory=$true)][string]$EvidencePath
)
$ErrorActionPreference = 'Stop'
$smtpAppId = 'ea5a14b8-e008-49e5-8190-bbf37d50aeb5'
$smtpPrincipalId = '426ca269-9741-4718-a40f-251aa25d1578'
$gtScopeName = 'MTM BC SMTP Consuelo Only'
$mxScopeName = 'MTM BC SMTP Carlos Only'
$smtpRole = 'Application SMTP.SendAsApp'
$mxMailboxAddress = 'carlos@mtmlogix.com'
$gtMailboxAddress = 'connie@mtmlogix.com'

$connection = Get-ConnectionInformation | Where-Object State -eq 'Connected' | Select-Object -First 1
if (-not $connection) { throw 'An authenticated Exchange Online session is required.' }
$principal = Get-ServicePrincipal -Identity $smtpPrincipalId
if ($principal.AppId.ToString() -ne $smtpAppId) { throw 'SMTP application identity mismatch.' }
$gtScopeBefore = Get-ManagementScope -Identity $gtScopeName
$gtAssignmentsBefore = @(Get-ManagementRoleAssignment -RoleAssignee $smtpPrincipalId | Where-Object CustomResourceScope -eq $gtScopeName | Select-Object Name,Role,CustomResourceScope)
if ($gtAssignmentsBefore.Count -ne 1 -or $gtAssignmentsBefore[0].Role.ToString() -ne $smtpRole) { throw 'The existing Consuelo SMTP role assignment differs from the approved baseline.' }
$carlosMailbox = Get-Mailbox -Identity $mxMailboxAddress
if ($carlosMailbox.PrimarySmtpAddress.ToString().ToLowerInvariant() -ne $mxMailboxAddress) { throw 'Carlos primary mailbox identity mismatch.' }
$smtpSettings = Get-CASMailbox -Identity $mxMailboxAddress | Select-Object SmtpClientAuthenticationDisabled
$organizationSmtp = Get-TransportConfig | Select-Object SmtpClientAuthenticationDisabled
$gtSmtpBefore = Get-CASMailbox -Identity $gtMailboxAddress | Select-Object SmtpClientAuthenticationDisabled
$beforeCarlos = @(Test-ServicePrincipalAuthorization -Identity $smtpAppId -Resource $mxMailboxAddress)
$beforeGuatemala = @(Test-ServicePrincipalAuthorization -Identity $smtpAppId -Resource $gtMailboxAddress)
$createdScope = $false
$createdAssignment = $false
$enabledCarlosSmtp = $false
if ($Apply) {
    $mxScope = Get-ManagementScope -Identity $mxScopeName -ErrorAction SilentlyContinue
    if (-not $mxScope) {
        $mxScope = New-ManagementScope -Name $mxScopeName -RecipientRestrictionFilter "(PrimarySmtpAddress -eq '$mxMailboxAddress') -and (RecipientTypeDetails -eq 'UserMailbox')"
        $createdScope = $true
    }
    $scopedRecipients = @(Get-Recipient -RecipientPreviewFilter $mxScope.RecipientFilter -ResultSize Unlimited)
    if ($scopedRecipients.Count -ne 1 -or $scopedRecipients[0].PrimarySmtpAddress.ToString().ToLowerInvariant() -ne $mxMailboxAddress) { throw 'Carlos scope must resolve to exactly the approved mailbox.' }
    $assignment = @(Get-ManagementRoleAssignment -RoleAssignee $smtpPrincipalId | Where-Object { $_.CustomResourceScope -eq $mxScopeName -and $_.Role.ToString() -eq $smtpRole })
    if ($assignment.Count -eq 0) {
        New-ManagementRoleAssignment -Name $mxScopeName -Role $smtpRole -App $smtpPrincipalId -CustomResourceScope $mxScopeName | Out-Null
        $createdAssignment = $true
    } elseif ($assignment.Count -ne 1) { throw 'Multiple Carlos SMTP assignments require review.' }
    if ($smtpSettings.SmtpClientAuthenticationDisabled -ne $false) {
        Set-CASMailbox -Identity $mxMailboxAddress -SmtpClientAuthenticationDisabled $false
        $enabledCarlosSmtp = $true
    }
}
$gtScopeAfter = Get-ManagementScope -Identity $gtScopeName
$gtAssignmentsAfter = @(Get-ManagementRoleAssignment -RoleAssignee $smtpPrincipalId | Where-Object CustomResourceScope -eq $gtScopeName | Select-Object Name,Role,CustomResourceScope)
if ($gtScopeAfter.RecipientFilter -ne $gtScopeBefore.RecipientFilter -or ($gtAssignmentsBefore | ConvertTo-Json -Compress) -ne ($gtAssignmentsAfter | ConvertTo-Json -Compress)) { throw 'Consuelo authorization changed unexpectedly.' }
$gtSmtpAfter = Get-CASMailbox -Identity $gtMailboxAddress | Select-Object SmtpClientAuthenticationDisabled
$organizationSmtpAfter = Get-TransportConfig | Select-Object SmtpClientAuthenticationDisabled
if (($gtSmtpBefore | ConvertTo-Json -Compress) -ne ($gtSmtpAfter | ConvertTo-Json -Compress) -or ($organizationSmtp | ConvertTo-Json -Compress) -ne ($organizationSmtpAfter | ConvertTo-Json -Compress)) { throw 'Guatemala or organization SMTP settings changed unexpectedly.' }
$result = [ordered]@{
    reviewedAt = [DateTime]::UtcNow.ToString('o')
    applied = [bool]$Apply
    application = @{ displayName = $principal.DisplayName; appId = $smtpAppId; objectId = $smtpPrincipalId }
    carlosMailbox = $carlosMailbox | Select-Object DisplayName,PrimarySmtpAddress,RecipientTypeDetails,ExternalDirectoryObjectId
    smtpSettings = $smtpSettings
    smtpSettingsAfter = Get-CASMailbox -Identity $mxMailboxAddress | Select-Object SmtpClientAuthenticationDisabled
    organizationSmtp = $organizationSmtp
    guatemalaSmtpUnchanged = $true
    organizationSmtpUnchanged = $true
    guatemalaScope = $gtScopeAfter | Select-Object Name,RecipientFilter
    guatemalaAssignmentUnchanged = $true
    beforeCarlos = $beforeCarlos
    beforeGuatemala = $beforeGuatemala
    createdScope = $createdScope
    createdAssignment = $createdAssignment
    enabledCarlosSmtp = $enabledCarlosSmtp
    assignmentsAfter = @(Get-ManagementRoleAssignment -RoleAssignee $smtpPrincipalId | Select-Object Name,Role,CustomResourceScope)
    carlosAuthorization = @(Test-ServicePrincipalAuthorization -Identity $smtpAppId -Resource $mxMailboxAddress)
    guatemalaAuthorization = @(Test-ServicePrincipalAuthorization -Identity $smtpAppId -Resource $gtMailboxAddress)
    unrelatedMailboxAuthorization = @(Test-ServicePrincipalAuthorization -Identity $smtpAppId -Resource 'mario@mtmlogix.com')
    sendsEmail = $false
    changesCredentials = $false
}
$json = $result | ConvertTo-Json -Depth 7
[IO.File]::WriteAllText($EvidencePath, $json)
$json
