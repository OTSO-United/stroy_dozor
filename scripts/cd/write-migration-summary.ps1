param([Parameter(Mandatory)][string]$ConfigPath,[string]$OperationId,
    [string]$Sha='', [string]$RunId='', [string]$ControllerSha='', [string]$Outcome='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'migration-policy.ps1')
if (!$OperationId) {
    if ($Sha -cnotmatch '^[a-f0-9]{40}$' -or $RunId -cnotmatch '^[1-9][0-9]*$' -or $ControllerSha -cnotmatch '^[a-f0-9]{40}$' -or $Outcome -notin @('success','failure','cancelled','skipped')) { throw 'Invalid preflight summary identity' }
    $text="## Migration preflight`nSource SHA: $Sha`n`nCI: $RunId`n`nController SHA: $ControllerSha`n`nExpected/target revision: 003 / 004. No new operation ID was emitted.`n`nOutcome: $Outcome. No new maintenance window was started; see the failed preflight step or already-current result. Existing recovery pending must not be removed."
    if ($env:GITHUB_STEP_SUMMARY) { Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Value $text -Encoding UTF8 }
    Write-Host $text
    return
}
if ($OperationId -cnotmatch '^[a-f0-9]{32}$') { throw 'Invalid operation ID' }
$config=Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$path=Join-Path $config.StateRoot ('migration-operations/'+$OperationId+'/operation.json')
if (!(Test-Path -LiteralPath $path)) { return }
$op=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
if ($Outcome -and $Outcome -notin @('success','failure','cancelled','skipped')) { throw 'Invalid attempt outcome' }
$downtime=Get-MaintenanceSummary $op
$lines=@('## Migration release',"- Operation: ``$OperationId``",("- Source SHA: ``{0}``; CI: {1}; run number: {2}" -f $op.sha,$op.runId,$op.runNumber),
    ("- Controller SHA: ``{0}``" -f $op.controllerSha),("- Revisions: expected {0}, target {1}, observed {2}" -f $op.expectedRevision,$op.targetRevision,$op.actualRevision),
    ("- Mode: {0}; status: {1}; result: {2}" -f $op.mode,$op.status,$op.result),
    ("- Restore/rehearsal: {0}; old-image compatibility: {1}" -f $op.rehearsalVerified,$op.compatibilityVerified),
    ("- Pending: {0}; maintenance window: {1}" -f $op.pending,$downtime),'','| Stage | UTC |','|---|---|')
foreach($event in $op.events) { $lines+=('| '+$event.stage+' | '+$event.utc+' |') }
if ($Outcome) { $lines+=@('',('Current Actions attempt: **'+$Outcome+'**. Operation status above is the saved journal, not a replacement for this attempt result.')) }
if ($op.pending) { $lines+=@('',"Recovery: run Deploy with migrations on main with the same source SHA/CI, mode recover and operation ID ``$OperationId``. No SQL/dump restore is automatic.") }
if ($env:GITHUB_STEP_SUMMARY) { Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Value ($lines -join "`n") -Encoding UTF8 }
Write-Host ($lines -join "`n")
