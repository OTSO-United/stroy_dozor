param(
    [Parameter(Mandatory)][string]$ConfigPath,
    [Parameter(Mandatory)][string]$SourceRoot,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{40}$')][string]$Sha,
    [Parameter(Mandatory)][ValidatePattern('^[1-9][0-9]*$')][string]$RunId,
    [Parameter(Mandatory)][long]$RunNumber,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{40}$')][string]$ControllerSha,
    [Parameter(Mandatory)][string]$ExpectedRevision,
    [Parameter(Mandatory)][string]$TargetRevision,
    [ValidateSet('check','release','recover')][string]$Mode='check',
    [string]$OperationId='',
    [ValidateSet('resume','restore-previous')][string]$RecoveryAction='resume'
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'migration-policy.ps1')
. (Join-Path $PSScriptRoot 'migration-runtime.ps1')
Assert-MigrationRequest $ExpectedRevision $TargetRevision $Mode $OperationId
if ($RunNumber -lt 1) { throw 'API-verified positive run number required' }
Assert-AbsoluteLocalPath $ConfigPath
Assert-AbsoluteLocalPath $SourceRoot
$cfg=Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($path in @($cfg.StateRoot,$cfg.RuntimeRoot,$cfg.ModelDirectory)) { Assert-AbsoluteLocalPath $path }
if ($cfg.ProjectName -cne 'stroykontur' -and $cfg.ProjectName -cnotmatch '^stroykontur-cd-test-[a-z0-9-]+$') { throw 'Unapproved Compose project' }
if ($cfg.HealthUrl -notmatch '^http://127\.0\.0\.1:[0-9]+$' -or $cfg.ExpectedModelSha256 -cnotmatch '^[a-f0-9]{64}$') { throw 'Invalid protected config' }
$script:docker=(Get-Command docker.exe -ErrorAction Stop).Source
$git=(Get-Command git.exe -ErrorAction Stop).Source
$policyPath=Join-Path $PSScriptRoot 'migration-policy.json'
$policy=Get-Content -LiteralPath $policyPath -Raw | ConvertFrom-Json
$policyHash=Get-NormalizedFileHash $policyPath
New-Item -ItemType Directory -Path $cfg.StateRoot -Force | Out-Null
$lock=Enter-DeploymentLock $cfg.StateRoot
$operation=$null
try {
    $statePath=Join-Path $cfg.StateRoot 'current.json'
    $pendingPath=Join-Path $cfg.StateRoot 'pending.json'
    $script:compose=Get-ComposeArguments $cfg
    $deadline=[DateTime]::UtcNow.AddSeconds(120)
    while ($true) {
        try { if ((Invoke-Native $script:docker @('info','--format','{{.OSType}}') 10) -ne 'linux') { throw 'Linux Docker required' }; break }
        catch { if ([DateTime]::UtcNow -ge $deadline) { throw 'Docker unavailable; release not modified' }; Start-Sleep -Seconds 5 }
    }
    if ((Invoke-Native $git @('-C',$SourceRoot,'rev-parse','HEAD')) -cne $Sha -or
        (Invoke-Native $git @('-C',$SourceRoot,'status','--porcelain','--untracked-files=all'))) { throw 'Exact clean verified source checkout required' }
    $current=Read-CdMap $statePath
    $current.images=@{api=$current.images.api;worker=$current.images.worker;monitor=$current.images.monitor}
    $runtime=Invoke-Compose @('config','--format','json') | ConvertFrom-Json
    $script:dbId=Invoke-Compose @('ps','-q','db')
    $dbContainer=@(Invoke-Native $script:docker @('inspect',$script:dbId) | ConvertFrom-Json)[0]
    if (!$dbContainer.State.Running -or $dbContainer.State.Health.Status -ne 'healthy' -or @($dbContainer.HostConfig.PortBindings.PSObject.Properties).Count) { throw 'Healthy unexposed PostgreSQL required' }
    $networks=@($dbContainer.NetworkSettings.Networks.PSObject.Properties.Name)
    if ($networks.Count -ne 1) { throw 'Expected one private production network' }
    $script:dbNetwork=$networks[0]
    $script:postgresImage=$dbContainer.Image
    if ([int](Invoke-DbSql 'SHOW server_version_num') -lt 160000 -or [int](Invoke-DbSql 'SHOW server_version_num') -ge 170000) { throw 'PostgreSQL 16 required' }
    if ((Assert-ModelPackage $cfg.ModelDirectory) -cne $cfg.ExpectedModelSha256) { throw 'Approved model differs' }
    $caddyId=Invoke-Compose @('ps','-q','caddy')
    $caddyContainer=@(Invoke-Native $script:docker @('inspect',$caddyId) | ConvertFrom-Json)[0]
    Assert-CaddyConfigMount $caddyContainer.Mounts $cfg.RuntimeRoot
    if ($Mode -ne 'recover') {
        if (Test-Path -LiteralPath $pendingPath) { throw 'Unfinished deployment; recover its operation first' }
        if ($current.runtimeFingerprint -cne (Get-RuntimeFingerprint $cfg.RuntimeRoot)) { throw 'Unapproved runtime fingerprint' }
        $disposition=Get-MigrationDisposition $Sha $RunNumber $current (Get-DatabaseRevision) $cfg.ExpectedModelSha256
        if ($disposition -eq 'already-current') {
            Test-Release $current
            Invoke-Compose @('exec','-T','api','python','/opt/stroy-cd/runtime-bootstrap.py','probe-detector') | Out-Null
            Write-Host 'Migration result: already-current; no build, backup or migration repeated.'
            if ($env:GITHUB_STEP_SUMMARY) { Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Value "Migration result: already-current. SHA $Sha, revision $TargetRevision; read-only checks passed." }
            return
        }
        Assert-NoOtherWriters
        Test-Release $current
        $size=[long](Invoke-DbSql 'SELECT pg_database_size(current_database())')
        $required=[Math]::Max([long]$policy.minimumFreeBytes,4*$size)
        foreach($path in @($cfg.StateRoot,$cfg.RuntimeRoot)) {
            if ([IO.DriveInfo]::new([IO.Path]::GetPathRoot($path)).AvailableFreeSpace -lt $required) { throw 'Insufficient free space for images, backup and rehearsal' }
        }
        $df=(Invoke-Native $script:docker @('exec',$script:dbId,'df','-Pk','/var/lib/postgresql/data')).Split("`n")[-1] -split '\s+'
        if ([long]$df[3]*1024 -lt $required) { throw 'Insufficient Docker VM data space' }
        $OperationId=[Guid]::NewGuid().ToString('N')
        $operationRoot=Join-Path $cfg.StateRoot ('migration-operations/'+$OperationId)
        New-Item -ItemType Directory -Path $operationRoot -Force | Out-Null
        $operation=@{kind='migration';version=1;id=$OperationId;mode=$Mode;stage='Prepared';status='running';pending=$false;
            sha=$Sha;runId=$RunId;runNumber=$RunNumber;controllerSha=$ControllerSha;recoveryControllerSha='';policyHash=$policyHash;
            expectedRevision=$ExpectedRevision;targetRevision=$TargetRevision;actualRevision=$ExpectedRevision;previous=$current;
            request=@{sha=$Sha;runId=$RunId;runNumber=$RunNumber;controllerSha=$ControllerSha;schemaRevision=$TargetRevision;
                migrationDigest=$policy.targetMigrationDigest;modelSha=$cfg.ExpectedModelSha256;images=@{};runtimeFingerprint='';operationId=$OperationId;checks=@{}};
            probeToken=([Guid]::NewGuid().ToString('N')+[Guid]::NewGuid().ToString('N'));events=@();backupHash='';backupBytes=0;
            compatibilityVerified=$false;rehearsalVerified=$false;trafficOpened=$false;downtimeStarted='';downtimeEnded='';
            originalFingerprint='';targetFingerprint='';runtimeFiles=@{};geocoder='';rehearsalVolume='';failureStage='';result='';errorCategory=''}
        Write-ReleaseState (Join-Path $operationRoot 'runtime-before.json') $runtime
        New-RuntimePlan
        $operation.request.runtimeFingerprint=$operation.targetFingerprint
        $databaseUrl=[string]$runtime.services.api.environment.DATABASE_URL
        if ($databaseUrl.Contains("`n") -or $databaseUrl.Contains("`r")) { throw 'Invalid DB environment' }
        Write-CdText (Join-Path $operationRoot 'production.env') ("DATABASE_URL=$databaseUrl`nSTROY_DATA_DIR=/data`nPGOPTIONS=-c statement_timeout=300000 -c lock_timeout=15000`n")
        Save-MigrationOperation
    } else {
        if (!(Test-Path -LiteralPath $pendingPath)) { throw 'No pending operation to recover' }
        $operationRoot=Join-Path $cfg.StateRoot ('migration-operations/'+$OperationId)
        $operation=Read-CdMap $pendingPath
        if ($operation.kind -cne 'migration' -or $operation.id -cne $OperationId -or $operation.sha -cne $Sha -or
            $operation.runId -cne $RunId -or [long]$operation.runNumber -ne $RunNumber -or $operation.version -ne 1 -or
            $operation.policyHash -cne $policyHash -or $operation.expectedRevision -cne $ExpectedRevision -or $operation.targetRevision -cne $TargetRevision) { throw 'Recovery identity/policy does not match pending' }
        if ($RunNumber -lt [long]$current.runNumber) { throw 'Newer release state forbids this recovery' }
        if ($operation.request.modelSha -cne $cfg.ExpectedModelSha256) { throw 'Recovery model differs from approved package' }
        $operation.recoveryControllerSha=$ControllerSha
        $operation.previous.images=@{api=$operation.previous.images.api;worker=$operation.previous.images.worker;monitor=$operation.previous.images.monitor}
        $operation.request.images=@{api=$operation.request.images.api;worker=$operation.request.images.worker;monitor=$operation.request.images.monitor}
        Assert-PlannedRuntime
        Stop-OperationContainers
        foreach($role in @('api','worker')) {
            Test-PackagedImage $operation.request.images[$role] $operation.request.modelSha -Cuda:($role -eq 'worker')
            Test-PackagedImage $operation.previous.images[$role] $operation.previous.modelSha -Cuda:($role -eq 'worker')
        }
    }
    Write-Host ("Operation ID: "+$OperationId)
    if ($env:GITHUB_OUTPUT) { Add-Content -LiteralPath $env:GITHUB_OUTPUT -Value ("operationId="+$OperationId) }

    function Test-MigrationTarget {
        if ((Get-DatabaseRevision) -cne '004') { throw 'Production target revision failed' }
        Invoke-DatabaseHelper $operation.request.images.api $script:dbNetwork (Join-Path $operationRoot 'production.env') 'schema' | Out-Null
        Test-Release $operation.request -Internal
        $probe=Invoke-Compose @('exec','-T','api','python','/opt/stroy-cd/runtime-bootstrap.py','probe-detector') | ConvertFrom-Json
        $api=Invoke-Compose @('ps','-q','api')
        $actual=@(Invoke-Native $script:docker @('inspect',$api) | ConvertFrom-Json)[0]
        if (('GEOCODER_REVERSE_URL='+$operation.geocoder) -cnotin $actual.Config.Env) { throw 'Geocoder environment not delivered into API' }
        $operation.request.checks=@{schema=$true;health=$true;ui=$true;model=$true;cuda=$true;detector=$true;geocoder=$probe.geocoder;rehearsal=$operation.rehearsalVerified}
    }

    function Complete-MigrationRelease {
        if ((Get-RuntimeFingerprint $cfg.RuntimeRoot) -cne $operation.targetFingerprint) { throw 'Final runtime differs from planned fingerprint' }
        $operation.actualRevision=Get-DatabaseRevision
        if ($operation.actualRevision -cne '004') { throw 'Final schema revision differs' }
        Write-ReleaseState $statePath $operation.request
        $operation.result='deployed-with-migration'
        $operation.status='success'
        Set-MigrationStage 'Completed'
        Remove-Item -LiteralPath $pendingPath
        $operation.pending=$false
        Save-MigrationOperation
    }

    function Restore-CompatiblePrevious {
        $actualRevision=Get-DatabaseRevision
        if ($operation.trafficOpened) { throw 'Traffic may have accepted writes; previous-image recovery is not permitted' }
        if ($actualRevision -eq '004' -and !$operation.compatibilityVerified) { throw 'Previous application compatibility was not proven' }
        Assert-SnapshotPreserved (Get-Content (Join-Path $operationRoot 'baseline.json') -Raw) (Get-ProductionSnapshot)
        Install-MigrationRuntime $actualRevision
        Set-LiveRelease $operation.previous
        Test-Release $operation.previous -Internal
        $restored=@{}
        foreach($property in $operation.previous.PSObject.Properties) { $restored[$property.Name]=$property.Value }
        if ($operation.previous -is [hashtable]) { $restored=$operation.previous.Clone() }
        $restored.schemaRevision=$actualRevision
        $restored.migrationDigest=if ($actualRevision -eq '004') { $policy.targetMigrationDigest } else { $policy.baseMigrationDigest }
        $restored.controllerSha=$ControllerSha
        $restored.operationId=$operation.id
        Open-MigrationTraffic
        Test-Release $operation.previous
        $restored.runtimeFingerprint=Get-RuntimeFingerprint $cfg.RuntimeRoot
        Write-ReleaseState $statePath $restored
        $operation.actualRevision=$actualRevision
        $operation.result='restored-previous-schema-'+$actualRevision
        $operation.status='recovered-previous'
        Set-MigrationStage 'RestoredPrevious'
        if ($actualRevision -eq '003') {
            Remove-Item -LiteralPath $pendingPath
            $operation.pending=$false
            Save-MigrationOperation
        }
        # At 004 pending remains, because old image migration files still end at 003.
    }

    function Hold-MigrationFailure {
        $operation.failureStage=$operation.stage
        $operation.errorCategory='StageFailed'
        $operation.status='failed'
        try {
            Stop-OperationContainers
            Enable-MigrationMaintenance
            Stop-MigrationWriters
            $operation.actualRevision=Get-DatabaseRevision
            $unchanged=$false
            if (Test-Path -LiteralPath (Join-Path $operationRoot 'baseline.json')) {
                try { Assert-SnapshotPreserved (Get-Content (Join-Path $operationRoot 'baseline.json') -Raw) (Get-ProductionSnapshot); $unchanged=$true } catch { }
            }
            $action=Get-MigrationFailureAction $operation.actualRevision $unchanged $operation.trafficOpened $operation.compatibilityVerified
            if ($action -eq 'restore-previous') { Restore-CompatiblePrevious }
            else { $operation.result='maintenance-pending-recovery-required'; Set-MigrationStage 'RecoveryRequired' }
        } catch {
            # A failed previous-image recovery may already have opened traffic.
            # Close it again, but do not claim maintenance if either action fails.
            $closed=$true
            try { Enable-MigrationMaintenance } catch { $closed=$false }
            try { Stop-MigrationWriters } catch { $closed=$false }
            $operation.result=if ($closed) { 'maintenance-pending-inspect-required' } else { 'pending-inspect-required-maintenance-unconfirmed' }
            try { Save-MigrationOperation } catch { }
        }
    }

    $steps=@{
        Preflight={ if ((Get-DatabaseRevision) -cne '003') { throw 'Base revision changed' } }
        AlreadyCurrent={ return $false }
        Build={
            $source=New-ReleaseSource (Join-Path $operationRoot 'build') $SourceRoot $Sha $cfg.ExpectedModelSha256
            $old=Join-Path $operationRoot 'live-migrations'
            $api=Invoke-Compose @('ps','-q','api')
            Invoke-Native $script:docker @('cp',"${api}:/app/migrations",$old) | Out-Null
            if ((Get-MigrationDigest $old) -cne $policy.baseMigrationDigest -or
                (Get-MigrationDigest (Join-Path $source 'backend/migrations')) -cne $policy.targetMigrationDigest) { throw 'Unreviewed migration tree' }
            $operation.request.images=Build-ReleaseImages $source $Sha $cfg.ExpectedModelSha256
            Invoke-Native $script:docker @('run','--rm','--network','none','--mount',('type=bind,source='+$PSScriptRoot+',target=/opt/stroy-cd,readonly'),
                '--mount',('type=bind,source='+$old+',target=/previous,readonly'),$operation.request.images.api,'python','/opt/stroy-cd/migration-db.py','chain','--previous','/previous','--approved-hash',$policy.migration004Sha256) | Out-Null
            foreach($role in @('api','worker')) {
                Test-PackagedImage $operation.previous.images[$role] $operation.previous.modelSha -Cuda:($role -eq 'worker')
                Invoke-Native $script:docker @('image','tag',$operation.previous.images[$role],('stroykontur:migration-rollback-'+$operation.id+'-'+$role)) | Out-Null
            }
            Save-MigrationOperation
        }
        CheckBackup={ Save-FinalBackup -Online }
        Pending={
            if ((Get-RuntimeFingerprint $cfg.RuntimeRoot) -cne $operation.originalFingerprint) { throw 'Runtime changed during build' }
            if ((Get-DatabaseRevision) -cne '003') { throw 'Revision changed during build' }
            Test-Release $operation.previous
            $operation.pending=$true
            Save-MigrationOperation
        }
        Stage={ param($stage) Set-MigrationStage $stage }
        Maintenance={ Enable-MigrationMaintenance }
        Quiesce={ Stop-MigrationWriters }
        Backup={ Save-FinalBackup }
        Rehearse={ Invoke-MigrationRehearsal }
        Recheck={
            Assert-PlannedRuntime
            Stop-MigrationWriters
            Assert-BackupFile (Join-Path $operationRoot 'before.dump') $operation.backupHash | Out-Null
            if (!$operation.rehearsalVerified -or (Get-DatabaseRevision) -cne '003') { throw 'Rehearsal/base revision gate failed' }
            Assert-SnapshotPreserved (Get-Content (Join-Path $operationRoot 'baseline.json') -Raw) (Get-ProductionSnapshot)
        }
        Upgrade={
            Invoke-DatabaseHelper $operation.request.images.api $script:dbNetwork (Join-Path $operationRoot 'production.env') 'upgrade' | Out-Null
            $operation.actualRevision=Get-DatabaseRevision
            Save-MigrationOperation
            Assert-SnapshotPreserved (Get-Content (Join-Path $operationRoot 'baseline.json') -Raw) (Get-ProductionSnapshot)
        }
        Runtime={ Install-MigrationRuntime }
        Activate={ Set-LiveRelease $operation.request }
        Verify={ Test-MigrationTarget }
        CommitPrepared={ Write-ReleaseState $statePath $operation.request }
        Open={ Open-MigrationTraffic }
        VerifyOpened={ Test-Release $operation.request }
        CommitComplete={ Complete-MigrationRelease }
        Failure={ Hold-MigrationFailure }
    }
    if ($Mode -eq 'recover') {
        try {
            Set-MigrationStage 'Recovering'
            Enable-MigrationMaintenance
            Stop-MigrationWriters
            $operation.actualRevision=Get-DatabaseRevision
            if ($RecoveryAction -eq 'restore-previous') {
                Restore-CompatiblePrevious
            } else {
                if ($operation.actualRevision -eq '003') {
                    if ($operation.trafficOpened) { throw 'Opened traffic with unexpected base revision; automatic recovery unsafe' }
                    if (!$operation.backupHash) { Save-FinalBackup }
                    else { Assert-SnapshotPreserved (Get-Content (Join-Path $operationRoot 'baseline.json') -Raw) (Get-ProductionSnapshot) }
                    Invoke-MigrationRehearsal
                    & $steps.Recheck
                    Set-MigrationStage 'Upgrade'
                    & $steps.Upgrade
                } elseif (!$operation.rehearsalVerified -or !$operation.compatibilityVerified) { throw 'Committed revision without verified rehearsal; hold for investigation' }
                Invoke-DatabaseHelper $operation.request.images.api $script:dbNetwork (Join-Path $operationRoot 'production.env') 'schema' | Out-Null
                foreach($stage in @('Runtime','Activate','Verify','CommitPrepared','Open','VerifyOpened','CommitComplete')) {
                    Set-MigrationStage $stage
                    & $steps[$stage]
                }
            }
        } catch { Hold-MigrationFailure; throw 'Recovery failed; maintenance/pending retained for investigation' }
    } else {
        $result=Invoke-MigrationStages $steps -CheckOnly:($Mode -eq 'check')
        if ($Mode -eq 'check') { $operation.result=$result; $operation.status='success'; Set-MigrationStage 'CheckComplete' }
    }
    Write-Host ("Migration result: "+$operation.result+"; operation="+$OperationId+"; source="+$Sha)
} finally {
    if ($operation) {
        try { & (Join-Path $PSScriptRoot 'write-migration-summary.ps1') -ConfigPath $ConfigPath -OperationId $operation.id } catch { }
    }
    $lock.Dispose()
}
