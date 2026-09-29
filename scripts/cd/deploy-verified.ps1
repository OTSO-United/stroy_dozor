param(
    [Parameter(Mandatory)][string]$ConfigPath,
    [string]$SourceRoot,
    [ValidatePattern('^[a-f0-9]{40}$')][string]$Sha,
    [ValidatePattern('^[1-9][0-9]*$')][string]$RunId,
    [long]$RunNumber,
    [switch]$Recover
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'release-policy.ps1')

. (Join-Path $PSScriptRoot 'release-runtime.ps1')

Assert-AbsoluteLocalPath $ConfigPath
$cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($path in @($cfg.StateRoot,$cfg.RuntimeRoot,$cfg.ModelDirectory)) { Assert-AbsoluteLocalPath $path }
if ($cfg.ProjectName -cne 'stroykontur' -and $cfg.ProjectName -cnotmatch '^stroykontur-cd-test-[a-z0-9-]+$') { throw 'Unapproved Compose project' }
if ($cfg.HealthUrl -notmatch '^http://127\.0\.0\.1:[0-9]+$') { throw 'HealthUrl must be explicit HTTP loopback port' }
if ($cfg.ExpectedModelSha256 -cnotmatch '^[a-f0-9]{64}$') { throw 'ExpectedModelSha256 is required' }
$script:docker = (Get-Command docker.exe -ErrorAction Stop).Source
$git = (Get-Command git.exe -ErrorAction Stop).Source
New-Item -ItemType Directory -Path $cfg.StateRoot -Force | Out-Null
$lock = Enter-DeploymentLock $cfg.StateRoot
try {
    $statePath = Join-Path $cfg.StateRoot 'current.json'
    $pendingPath = Join-Path $cfg.StateRoot 'pending.json'
    $script:compose = Get-ComposeArguments $cfg
    if (Test-Path -LiteralPath $pendingPath) {
        $unresolved = Get-Content -LiteralPath $pendingPath -Raw | ConvertFrom-Json
        if ($unresolved.PSObject.Properties.Name -contains 'kind' -and $unresolved.kind -eq 'migration') {
            throw 'Migration pending: use Deploy with migrations recovery; image-only recovery is forbidden'
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    while ($true) {
        try {
            if ((Invoke-Native $script:docker @('info','--format','{{.OSType}}') 10) -ne 'linux') { throw 'Linux Engine required' }
            break
        } catch {
            if ([DateTime]::UtcNow -ge $deadline) { throw 'Docker unavailable; running deployment not modified' }
            Start-Sleep -Seconds 5
        }
    }
    $runtime = Invoke-Compose @('config','--format','json') | ConvertFrom-Json
    if ($runtime.services.worker.environment.REQUIRE_CUDA -ne '1' -or
        $runtime.services.api.environment.MODEL_MANIFEST -ne '/models/yolo26m/manifest.json' -or
        $runtime.services.worker.environment.MODEL_MANIFEST -ne '/models/yolo26m/manifest.json') { throw 'Expected packaged GPU runtime configuration' }
    $runtimeFingerprint = Get-RuntimeFingerprint $cfg.RuntimeRoot
    $current = $null
    if (Test-Path -LiteralPath $statePath) {
        $current = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $current.images = @{api=$current.images.api; worker=$current.images.worker; monitor=$current.images.monitor}
        if ($current.runtimeFingerprint -cne $runtimeFingerprint) { throw 'Runtime configuration changed; reconcile manually before CD' }
    }
    if (Test-Path -LiteralPath $pendingPath) {
        if (!$Recover) { throw 'Interrupted deployment found; run -Recover before accepting another SHA' }
        $pending = Get-Content -LiteralPath $pendingPath -Raw | ConvertFrom-Json
        if ($pending.runtimeFingerprint -cne $runtimeFingerprint) { throw 'Pending runtime configuration changed' }
        $pending.images = @{api=$pending.images.api; worker=$pending.images.worker; monitor=$pending.images.monitor}
        Set-LiveRelease $pending
        Test-Release $pending
        if ($pending.PSObject.Properties.Name -contains 'priorState' -and $pending.priorState) {
            Write-ReleaseState $statePath $pending.priorState
        } elseif (Test-Path -LiteralPath $statePath) {
            Remove-Item -LiteralPath $statePath
        }
        Remove-Item -LiteralPath $pendingPath
        Write-Host 'Previous images recovered; no database downgrade performed.'
        return
    }
    if ($Recover) { throw 'No pending deployment to recover' }
    if (!$Sha -or !$RunId -or $RunNumber -lt 1) { throw 'Verified SHA, run ID and run number are required' }
    Assert-AbsoluteLocalPath $SourceRoot
    if ((Invoke-Native $git @('-C',$SourceRoot,'rev-parse','HEAD')) -cne $Sha) { throw 'Checkout differs from verified SHA' }
    if (Invoke-Native $git @('-C',$SourceRoot,'status','--porcelain','--untracked-files=all')) { throw 'Source checkout is dirty' }
    $request = @{sha=$Sha;runId=$RunId;runNumber=$RunNumber;runtimeFingerprint=$runtimeFingerprint;modelSha=$cfg.ExpectedModelSha256}
    if ($current -and $current.PSObject.Properties.Name -contains 'schemaRevision') {
        $revision=Invoke-Compose @('exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1','-U','stroy','-d','stroy','-Atc','SELECT version_num FROM alembic_version')
        if ($revision -cne $current.schemaRevision) { throw 'Database revision changed outside its managed migration release' }
        $request.schemaRevision=$current.schemaRevision
        $request.migrationDigest=$current.migrationDigest
    }
    if ($env:GITHUB_SHA -cmatch '^[a-f0-9]{40}$') { $request.controllerSha=$env:GITHUB_SHA }
    $releaseDirectory = Join-Path $cfg.StateRoot ('releases/' + $Sha + '-' + [Guid]::NewGuid().ToString('N'))
    $steps = @{
        Preflight = {
            if ((Assert-ModelPackage $cfg.ModelDirectory) -cne $cfg.ExpectedModelSha256) { throw 'Model differs from locally approved SHA' }
            if ($current -and $current.sha -ceq $Sha -and $current.modelSha -cne $request.modelSha) {
                throw 'A model change requires a new verified source commit'
            }
        }
        Build = {
            $source = New-ReleaseSource $releaseDirectory $SourceRoot $Sha $request.modelSha
            $liveMigrations = Join-Path $releaseDirectory 'live-migrations'
            $api = Invoke-Compose @('ps','-q','api')
            Invoke-Native $script:docker @('cp',"${api}:/app/migrations",$liveMigrations) | Out-Null
            if ((Get-MigrationDigest $liveMigrations) -cne (Get-MigrationDigest (Join-Path $source 'backend/migrations'))) {
                throw 'Migration changes require a separate reviewed migration/restore procedure'
            }
            $request.images = Build-ReleaseImages $source $Sha $request.modelSha
        }
        Snapshot = {
            $previous = Get-LiveRelease
            $previous.runtimeFingerprint = $runtimeFingerprint
            $previous.priorState = $current
            Test-Release $previous
            foreach ($service in @('api','worker')) {
                Invoke-Native $script:docker @('image','tag',$previous.images[$service],('stroykontur:rollback-' + $cfg.ProjectName + '-' + $service + '-' + $RunId)) | Out-Null
            }
            Test-PackagedImage $previous.images.api $previous.modelSha
            Test-PackagedImage $previous.images.worker $previous.modelSha -Cuda
            return $previous
        }
        Backup = {
            $db = Invoke-Compose @('ps','-q','db')
            Invoke-Native $script:docker @('exec',$db,'pg_dump','-U','stroy','-d','stroy','-Fc','-f','/tmp/cd-before.dump') | Out-Null
            Invoke-Native $script:docker @('exec',$db,'pg_restore','--list','/tmp/cd-before.dump') | Out-Null
            Invoke-Native $script:docker @('cp',"${db}:/tmp/cd-before.dump",(Join-Path $releaseDirectory 'before.dump')) | Out-Null
        }
        Pending = { param($previous) Write-ReleaseState $pendingPath $previous }
        Activate = { param($release) Set-LiveRelease $release }
        Verify = { param($release) Test-Release $release }
        Commit = {
            param($release)
            Write-ReleaseState $statePath $release
            Remove-Item -LiteralPath $pendingPath
        }
        ClearPending = { Remove-Item -LiteralPath $pendingPath }
    }
    $result = Invoke-ReleaseTransaction $request $current $steps
    Write-Host "CD result: $result; verified source SHA=$Sha, CI run=$RunId"
} finally { $lock.Dispose() }
