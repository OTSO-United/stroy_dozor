# Fault injection is confined to a disposable controller copy and isolated fixture.
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$TestId,
    [Parameter(Mandatory)][ValidateSet('RehearsalFailure','AfterCommit','HealthFailure','StateCommitFailure')][string]$Scenario
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'migration-policy.ps1')
. (Join-Path $PSScriptRoot 'migration-runtime.ps1')
& (Join-Path $PSScriptRoot 'test-migration-integration.ps1') -TestId $TestId -PrepareOnly | Out-Null
$testRoot='C:/StroyKontur/deployment/verification/migration-'+$TestId
$fixture=Read-CdMap (Join-Path $testRoot 'fixture.json')
$cfg=Get-Content $fixture.config -Raw | ConvertFrom-Json
if ($cfg.ProjectName -cne ('stroykontur-cd-test-migration-'+$TestId) -or $cfg.HealthUrl -cne 'http://127.0.0.1:18190') { throw 'Only the explicit isolated fixture is accepted' }
if ($fixture.sha -cne 'b5f174a51d756f44c2e3f58d8effdce3aa520940') { throw 'Fixture candidate changed; review test CI identity' }
$script:docker=(Get-Command docker.exe).Source
$script:compose=Get-ComposeArguments $cfg
$script:dbId=Invoke-Compose @('ps','-q','db')
$previous=Read-CdMap (Join-Path $cfg.StateRoot 'current.json')
$previous.images=@{api=$previous.images.api;worker=$previous.images.worker;monitor=$previous.images.monitor}
$pendingPath=Join-Path $cfg.StateRoot 'pending.json'
$copy=Join-Path $testRoot 'fault-controller'
New-Item -ItemType Directory -Path $copy -Force | Out-Null
Get-ChildItem -LiteralPath $PSScriptRoot -File | Copy-Item -Destination $copy
$faultScript=Join-Path $copy 'deploy-migrations.ps1'
$code=[IO.File]::ReadAllText($faultScript)
$needle=switch($Scenario) {
    RehearsalFailure { 'Rehearse={ Invoke-MigrationRehearsal }' }
    AfterCommit { 'Runtime={ Install-MigrationRuntime }' }
    HealthFailure { 'Verify={ Test-MigrationTarget }' }
    StateCommitFailure { 'CommitPrepared={ Write-ReleaseState $statePath $operation.request }' }
}
$replacement=switch($Scenario) {
    RehearsalFailure { "Rehearse={ throw 'Injected isolated rehearsal failure' }" }
    AfterCommit { 'Runtime={ [Environment]::Exit(73) }' }
    HealthFailure { "Verify={ throw 'Injected isolated health failure' }" }
    StateCommitFailure { "CommitPrepared={ throw 'Injected isolated state commit failure' }" }
}
if (($code.Split(@($needle),[StringSplitOptions]::None)).Count -ne 2) { throw 'Fault injection point changed; review harness' }
# Mechanical test instrumentation, never installed in the controller or recovery copy.
[IO.File]::WriteAllText($faultScript,$code.Replace($needle,$replacement),[Text.UTF8Encoding]::new($false))
$arguments=@('-NoProfile','-File',$faultScript,'-ConfigPath',$fixture.config,'-SourceRoot',$fixture.source,
    '-Sha',$fixture.sha,'-RunId','35537685550','-RunNumber','9','-ControllerSha',('0'*40),
    '-ExpectedRevision','003','-TargetRevision','004','-Mode','release')
$report=@{scenario=$Scenario;sha=$fixture.sha;operationId='';faultObserved=$false;recoveryVerified=$false;completedUtc=''}
try {
    $failed=$false
    try { Invoke-Native (Join-Path $PSHOME 'powershell.exe') $arguments 3600 | Out-Null } catch { $failed=$true }
    if (!$failed) { throw 'Injected release unexpectedly succeeded' }
    $report.faultObserved=$true
    if ($Scenario -eq 'RehearsalFailure') {
        if ((Get-DatabaseRevision) -cne '003' -or (Test-Path $pendingPath)) { throw 'Pre-migration failure did not recover base schema' }
        $script:compose=Get-ComposeArguments $cfg
        Test-Release $previous
        $state=Read-CdMap (Join-Path $cfg.StateRoot 'current.json')
        if ($state.schemaRevision -cne '003' -or $state.runtimeFingerprint -cne (Get-RuntimeFingerprint $cfg.RuntimeRoot)) { throw 'Recovered state/runtime differs' }
    } else {
        if ((Get-DatabaseRevision) -cne '004' -or !(Test-Path $pendingPath)) { throw 'Committed migration was lost or pending was removed' }
        $operation=Read-CdMap $pendingPath
        $report.operationId=$operation.id
        if (!$operation.rehearsalVerified -or !$operation.compatibilityVerified -or !(Test-Path (Join-Path $cfg.StateRoot 'control/maintenance.json'))) { throw 'Recovery prerequisites missing' }
        $status=try { [int](Invoke-WebRequest 'http://127.0.0.1:18191/' -UseBasicParsing).StatusCode } catch { [int]$_.Exception.Response.StatusCode }
        if ($status -ne 503) { throw 'Public fixture traffic is not in maintenance' }
        $recovery=@{ConfigPath=$fixture.config;SourceRoot=$fixture.source;Sha=$fixture.sha;RunId='35537685550';RunNumber=9;ControllerSha=('0'*40);ExpectedRevision='003';TargetRevision='004';Mode='recover';OperationId=$operation.id}
        if ($Scenario -eq 'HealthFailure') {
            & (Join-Path $PSScriptRoot 'deploy-migrations.ps1') @recovery -RecoveryAction restore-previous
            $script:compose=Get-ComposeArguments $cfg
            Test-Release $previous
            $state=Read-CdMap (Join-Path $cfg.StateRoot 'current.json')
            if ($state.schemaRevision -cne '004' -or !(Test-Path $pendingPath)) { throw 'Compatible recovery concealed actual revision or cleared pending' }
        }
        & (Join-Path $PSScriptRoot 'deploy-migrations.ps1') @recovery -RecoveryAction resume
        $script:compose=Get-ComposeArguments $cfg
        $state=Read-CdMap (Join-Path $cfg.StateRoot 'current.json')
        if ((Get-DatabaseRevision) -cne '004' -or $state.sha -cne $fixture.sha -or (Test-Path $pendingPath)) { throw 'Forward recovery did not complete' }
        $state.images=@{api=$state.images.api;worker=$state.images.worker;monitor=$state.images.monitor}
        Test-Release $state
        & (Join-Path $PSScriptRoot 'deploy-migrations.ps1') -ConfigPath $fixture.config -SourceRoot $fixture.source -Sha $fixture.sha -RunId 35537685550 -RunNumber 9 -ControllerSha ('0'*40) -ExpectedRevision 003 -TargetRevision 004 -Mode release
    }
    $report.recoveryVerified=$true
    $report.completedUtc=[DateTime]::UtcNow.ToString('o')
    Write-ReleaseState (Join-Path $testRoot 'fault-result.json') $report
    Write-Host ("PASS isolated $Scenario and unchanged-controller recovery")
} finally {
    $script:compose=Get-ComposeArguments $cfg
    Invoke-Compose @('stop','--timeout','30') | Out-Null
}
