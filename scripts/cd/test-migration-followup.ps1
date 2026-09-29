# Local synthetic successor checks ordinary CD after 004; it is never published.
param([Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$TestId)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'migration-policy.ps1')
. (Join-Path $PSScriptRoot 'migration-runtime.ps1')
$testRoot='C:/StroyKontur/deployment/verification/migration-'+$TestId
$fixture=Read-CdMap (Join-Path $testRoot 'fixture.json')
$cfg=Get-Content $fixture.config -Raw | ConvertFrom-Json
if ($cfg.ProjectName -cne ('stroykontur-cd-test-migration-'+$TestId) -or $cfg.HealthUrl -cne 'http://127.0.0.1:18190') { throw 'Only the explicit isolated fixture is accepted' }
$script:docker=(Get-Command docker.exe).Source
$git=(Get-Command git.exe).Source
$script:compose=Get-ComposeArguments $cfg
$state=Read-CdMap (Join-Path $cfg.StateRoot 'current.json')
$state.images=@{api=$state.images.api;worker=$state.images.worker;monitor=$state.images.monitor}
if ($state.schemaRevision -cne '004' -or $state.sha -cne $fixture.sha -or (Test-Path (Join-Path $cfg.StateRoot 'pending.json'))) { throw 'A completed isolated migration is required' }
try {
    Protect-ReleaseImages $state.images $state.sha
    $active=Join-Path $cfg.StateRoot 'active-images.json'
    Invoke-Native $script:docker ($script:compose+@('-f',$active,'up','-d','--no-build','--pull','never','--wait','--wait-timeout','240')) | Out-Null
    $script:dbId=Invoke-Compose @('ps','-q','db')
    & (Join-Path $PSScriptRoot 'deploy-verified.ps1') -ConfigPath $fixture.config -SourceRoot $fixture.source -Sha $fixture.sha -RunId 35537685550 -RunNumber 9
    Invoke-Native $git @('-C',$fixture.source,'-c','user.name=Isolated CD Test','-c','user.email=cd-test@localhost','commit','--allow-empty','-m','Isolated ordinary-CD successor; never publish') | Out-Null
    $next=Invoke-Native $git @('-C',$fixture.source,'rev-parse','HEAD')
    & (Join-Path $PSScriptRoot 'deploy-verified.ps1') -ConfigPath $fixture.config -SourceRoot $fixture.source -Sha $next -RunId 35537685550 -RunNumber 10
    $current=Read-CdMap (Join-Path $cfg.StateRoot 'current.json')
    if ($current.sha -cne $next -or $current.schemaRevision -cne '004' -or $current.migrationDigest -cne $state.migrationDigest -or (Get-DatabaseRevision) -cne '004') { throw 'Ordinary CD did not preserve migrated schema metadata' }
    if ((Get-RuntimeFingerprint $cfg.RuntimeRoot) -cne $state.runtimeFingerprint -or (Test-Path (Join-Path $cfg.StateRoot 'pending.json'))) { throw 'Ordinary CD changed reviewed runtime or left pending' }
    $current.images=@{api=$current.images.api;worker=$current.images.worker;monitor=$current.images.monitor}
    Test-Release $current
    Write-ReleaseState (Join-Path $testRoot 'followup-result.json') @{syntheticSourceSha=$next;schemaRevision='004';runtimeUnchanged=$true;ordinaryCdVerified=$true;completedUtc=[DateTime]::UtcNow.ToString('o')}
    Write-Host 'PASS ordinary CD same-SHA and synthetic successor after 004; no GitHub release claimed'
} finally {
    Invoke-Compose @('stop','--timeout','30') | Out-Null
}
