# Explicit isolated integration test; never uses the production DB or media volumes.
param([Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$TestId,[switch]$PrepareOnly)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'release-policy.ps1')
. (Join-Path $PSScriptRoot 'release-runtime.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$testRoot='C:/StroyKontur/deployment/verification/migration-'+$TestId
if (Test-Path -LiteralPath $testRoot) { throw 'Choose a new isolated test ID' }
$project='stroykontur-cd-test-migration-'+$TestId
$runtimeRoot=Join-Path $testRoot 'runtime'
$stateRoot=Join-Path $testRoot 'state'
New-Item -ItemType Directory -Path $runtimeRoot,$stateRoot -Force | Out-Null
$script:docker=(Get-Command docker.exe).Source
$git=(Get-Command git.exe).Source
$live=Get-Content C:/StroyKontur/deployment/state/current.json -Raw | ConvertFrom-Json
$password=[Guid]::NewGuid().ToString('N')
$envFile=Join-Path $runtimeRoot '.env'
[IO.File]::WriteAllText($envFile,("APP_PORT=18190`nPOSTGRES_PASSWORD=$password`nCPU_IMAGE="+$live.images.api+"`nGPU_IMAGE="+$live.images.worker+"`nGEOCODER_REVERSE_URL=`nNODE_IMAGE=public.ecr.aws/docker/library/node:24-alpine`nPYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm`n"),[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $runtimeRoot 'Caddyfile'),":80 {`n    reverse_proxy api:8000`n}`n",[Text.UTF8Encoding]::new($false))
$rendered=Invoke-Native $script:docker @('compose','--env-file',$envFile,'-p',$project,'-f',(Join-Path $root 'compose.yaml'),'-f',(Join-Path $root 'compose.gpu.yaml'),'--profile','public','config','--format','json') | ConvertFrom-Json
foreach($service in @('api','worker','monitor')) { $rendered.services.$service.PSObject.Properties.Remove('build') }
$rendered.services.caddy.ports=@(@{target=80;published='18191';host_ip='127.0.0.1';protocol='tcp';mode='ingress'})
$rendered.services.caddy.volumes[0].source=Join-Path $runtimeRoot 'Caddyfile'
foreach($volume in $rendered.volumes.PSObject.Properties) {
    if (!$volume.Value.name.StartsWith($project+'_')) { throw 'Fixture references a volume outside its isolated project' }
}
Write-ReleaseState (Join-Path $runtimeRoot 'compose.yaml') $rendered
Write-ReleaseState (Join-Path $runtimeRoot 'compose.gpu.yaml') @{services=@{}}
$cfg=@{ProjectName=$project;RuntimeRoot=$runtimeRoot;StateRoot=$stateRoot;ModelDirectory='C:/StroyKontur/deployment/model';ExpectedModelSha256=$live.modelSha;HealthUrl='http://127.0.0.1:18190'}
$config=Join-Path $testRoot 'config.json'
Write-ReleaseState $config $cfg
$script:compose=Get-ComposeArguments $cfg
Invoke-Compose @('up','-d','--no-build','--pull','never','--wait','--wait-timeout','240') | Out-Null
$previous=Get-LiveRelease
$previous.sha=$live.sha
$previous.runId=$live.runId
$previous.runNumber=$live.runNumber
$previous.runtimeFingerprint=Get-RuntimeFingerprint $runtimeRoot
Write-ReleaseState (Join-Path $stateRoot 'current.json') $previous
$source=Join-Path $testRoot 'source'
Invoke-Native $git @('clone','--no-hardlinks','--local',$root,$source) | Out-Null
$sha=Invoke-Native $git @('-C',$source,'rev-parse','HEAD')
$fixture=@{root=$testRoot;config=$config;source=$source;sha=$sha;project=$project}
Write-ReleaseState (Join-Path $testRoot 'fixture.json') $fixture
if ($PrepareOnly) { $fixture | ConvertTo-Json; return }
try {
    & (Join-Path $PSScriptRoot 'deploy-migrations.ps1') -ConfigPath $config -SourceRoot $source -Sha $sha -RunId 35537685550 -RunNumber 9 -ControllerSha ('0'*40) -ExpectedRevision 003 -TargetRevision 004 -Mode release
    & (Join-Path $PSScriptRoot 'deploy-migrations.ps1') -ConfigPath $config -SourceRoot $source -Sha $sha -RunId 35537685550 -RunNumber 9 -ControllerSha ('0'*40) -ExpectedRevision 003 -TargetRevision 004 -Mode release
} finally {
    $script:compose=Get-ComposeArguments $cfg
    Invoke-Compose @('stop','--timeout','30') | Out-Null
}
Write-Host 'Isolated PostgreSQL 003 -> 004 release and same-SHA retry passed; volumes retained.'
