# Explicit integration test. All writes go to tmp and a separate Compose project.
param([switch]$PrepareOnly, [ValidatePattern('^[a-z0-9-]+$')][string]$TestId='ops08')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'release-policy.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$testRoot=Join-Path $root ('tmp/cd-' + $TestId)
$projectName='stroykontur-cd-test-' + $TestId
if (Test-Path $testRoot) { throw 'Test directory already exists; inspect it and use a fresh -TestId' }
$source=Join-Path $testRoot 'checkout'
$runtime=Join-Path $testRoot 'runtime'
New-Item -ItemType Directory -Path $source,$runtime -Force | Out-Null
& git -C $root archive HEAD --format=zip -o (Join-Path $testRoot 'source.zip')
if ($LASTEXITCODE) { throw 'Archive failed' }
Expand-Archive -LiteralPath (Join-Path $testRoot 'source.zip') -DestinationPath $source
# Include the current requested changes without committing the user's worktree.
$files=@(& git -C $root ls-files --modified --others --exclude-standard)
foreach ($relative in $files) {
    if ($relative.StartsWith('.idea/')) { continue }
    $from=Join-Path $root $relative
    if (Test-Path $from -PathType Leaf) {
        $to=Join-Path $source $relative
        New-Item -ItemType Directory -Path (Split-Path $to -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $from -Destination $to
    }
}
& git -C $source init -q
& git -C $source add .
& git -C $source -c user.name=CD-Test -c user.email=cd-test@localhost commit -qm 'Local CD fixture, not GitHub-verified'
if ($LASTEXITCODE) { throw 'Fixture commit failed' }
$sha=(& git -C $source rev-parse HEAD).Trim()
[IO.File]::WriteAllText((Join-Path $runtime '.env'), "APP_PORT=18080`nPOSTGRES_PASSWORD=isolated-cd-test`nNODE_IMAGE=public.ecr.aws/docker/library/node:24-alpine`nPYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm`n")
[IO.File]::WriteAllText((Join-Path $runtime 'Caddyfile'), ":80 {`n reverse_proxy api:8000`n}`n")
$compose = & docker compose --env-file (Join-Path $runtime '.env') -p $projectName -f (Join-Path $root 'compose.yaml') -f (Join-Path $root 'compose.gpu.yaml') --profile public config --format json | ConvertFrom-Json
if ($LASTEXITCODE) { throw 'Compose config failed' }
foreach ($service in @('api','worker','monitor')) { $compose.services.$service.PSObject.Properties.Remove('build') }
$compose.services.caddy.ports = @(@{target=80;published='18081';host_ip='127.0.0.1';protocol='tcp';mode='ingress'})
$compose.services.caddy.volumes[0].source = Join-Path $runtime 'Caddyfile'
Write-ReleaseState (Join-Path $runtime 'compose.yaml') $compose
Write-ReleaseState (Join-Path $runtime 'compose.gpu.yaml') @{services=@{}}
$config=@{ProjectName=$projectName;StateRoot=(Join-Path $testRoot 'state');RuntimeRoot=$runtime;ModelDirectory=(Join-Path $root 'models/yolo26m');ExpectedModelSha256=(Assert-ModelPackage (Join-Path $root 'models/yolo26m'));HealthUrl='http://127.0.0.1:18080'}
$configPath=Join-Path $testRoot 'config.json'
Write-ReleaseState $configPath $config
Write-ReleaseState (Join-Path $testRoot 'fixture.json') @{sha=$sha;source=$source;config=$configPath}
if ($PrepareOnly) { Write-Host "Prepared isolated fixture $sha"; return }
& docker compose --project-directory $runtime --env-file (Join-Path $runtime '.env') -p $config.ProjectName -f (Join-Path $runtime 'compose.yaml') --profile public up -d --no-build --pull never --wait --wait-timeout 240
if ($LASTEXITCODE) { throw 'Isolated baseline failed' }
& (Join-Path $PSScriptRoot 'deploy-verified.ps1') -ConfigPath $configPath -SourceRoot $source -Sha $sha -RunId 1 -RunNumber 1
& (Join-Path $PSScriptRoot 'deploy-verified.ps1') -ConfigPath $configPath -SourceRoot $source -Sha $sha -RunId 1 -RunNumber 1
Write-Host 'Isolated update and same-SHA retry passed. Stop test containers after HTTP smoke.'
