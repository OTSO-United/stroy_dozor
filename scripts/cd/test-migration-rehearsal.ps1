# Tests restore/upgrade on a disposable copy only; does not invoke release or bypass its writer guard.
param([Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$TestId)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'migration-policy.ps1')
. (Join-Path $PSScriptRoot 'migration-runtime.ps1')
$testRoot='C:/StroyKontur/deployment/verification/migration-'+$TestId
$cfg=Get-Content (Join-Path $testRoot 'config.json') -Raw | ConvertFrom-Json
if ($cfg.ProjectName -cne ('stroykontur-cd-test-migration-'+$TestId) -or $cfg.HealthUrl -cne 'http://127.0.0.1:18190') { throw 'Only explicit isolated fixture is accepted' }
$script:docker=(Get-Command docker.exe).Source
$script:compose=Get-ComposeArguments $cfg
$script:dbId=Invoke-Compose @('ps','-q','db')
$dbContainer=@(Invoke-Native $script:docker @('inspect',$script:dbId) | ConvertFrom-Json)[0]
if ($dbContainer.Config.Labels.'com.docker.compose.project' -cne $cfg.ProjectName) { throw 'Database outside isolated fixture' }
$script:postgresImage=$dbContainer.Image
$previous=Read-CdMap (Join-Path $cfg.StateRoot 'current.json')
$images=Read-CdMap (Join-Path $testRoot 'candidate-images.json')
$id=[Guid]::NewGuid().ToString('N')
$operationRoot=Join-Path $testRoot ('rehearsal-only/'+$id)
New-Item -ItemType Directory -Path $operationRoot -Force | Out-Null
$pendingPath=Join-Path $cfg.StateRoot 'pending.json'
$operation=@{id=$id;mode='check';pending=$false;request=@{images=$images};previous=$previous;backupHash='';backupBytes=0;rehearsalVolume='';rehearsalVerified=$false;compatibilityVerified=$false}
Invoke-RestMethod ($cfg.HealthUrl+'/api/v1/projects') -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key'=$id} -Body '{"name":"SMOKE isolated migration preservation"}' | Out-Null
Save-FinalBackup -Online
Invoke-MigrationRehearsal
if ((Get-DatabaseRevision) -cne '003') { throw 'The fixture source database must remain at 003' }
if (Test-Path -LiteralPath $pendingPath) { throw 'Rehearsal must not create release pending' }
Write-Host 'PASS real PostgreSQL 16 restore, 003 -> 004, data/schema preservation, old-image compatibility and detector HTTP inference on isolated copy'
Write-Host ('Evidence directory: '+$operationRoot)
