. (Join-Path $PSScriptRoot 'release-runtime.ps1')

function Write-CdText {
    param([string]$Path, [string]$Value)
    $temp=$Path+'.new'
    [IO.File]::WriteAllText($temp,$Value,[Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temp,$Path,[NullString]::Value) }
    else { [IO.File]::Move($temp,$Path) }
}

function Read-CdMap {
    param([string]$Path)
    $value=Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    $map=@{}
    foreach($property in $value.PSObject.Properties) { $map[$property.Name]=$property.Value }
    return $map
}

function Invoke-DbSql {
    param([string]$Sql)
    Invoke-Native $script:docker @('exec',$script:dbId,'psql','-X','-v','ON_ERROR_STOP=1','-U','stroy','-d','stroy','-Atc',$Sql)
}

function Get-DatabaseRevision {
    $revision=Invoke-DbSql 'SELECT version_num FROM alembic_version'
    if ($revision -cnotmatch '^(003|004)$') { throw 'Unexpected or ambiguous live revision' }
    return $revision
}

function Save-MigrationOperation {
    Write-ReleaseState (Join-Path $operationRoot 'operation.json') $operation
    if ($operation.pending) { Write-ReleaseState $pendingPath $operation }
}

function Set-MigrationStage {
    param([string]$Stage)
    $operation.stage=$Stage
    $operation.events=@($operation.events)+@(@{stage=$Stage;utc=[DateTime]::UtcNow.ToString('o')})
    Save-MigrationOperation
    Write-Host ("Migration operation {0}: {1}" -f $operation.id,$Stage)
}

function Invoke-DatabaseHelper {
    param([string]$Image, [string]$Network, [string]$EnvFile, [string]$Command, [string[]]$Extra=@())
    $name='stroykontur-migration-'+$operation.id+'-task'
    $arguments=@('run','--rm','--name',$name,'--label',('stroykontur.cd.operation='+$operation.id),
        '--network',$Network,'--env-file',$EnvFile,'--mount',('type=bind,source='+$PSScriptRoot+',target=/opt/stroy-cd,readonly'),
        $Image,'python','/opt/stroy-cd/migration-db.py',$Command)+$Extra
    return Invoke-Native $script:docker $arguments 1800
}

function Get-ProductionSnapshot {
    Invoke-DatabaseHelper $operation.previous.images.api $script:dbNetwork (Join-Path $operationRoot 'production.env') 'snapshot'
}

function Assert-SnapshotPreserved {
    param([string]$Before, [string]$After)
    $left=$Before | ConvertFrom-Json
    $right=$After | ConvertFrom-Json
    # Python emits sorted keys, providing a stable structured comparison in PS 5.1.
    foreach($key in @('tables','sequences')) {
        if (($left.$key | ConvertTo-Json -Depth 30 -Compress) -cne ($right.$key | ConvertTo-Json -Depth 30 -Compress)) {
            throw 'Existing database data/schema differs from the final frozen snapshot'
        }
    }
}

function Assert-NoOtherWriters {
    $allowed=@{}
    foreach($service in @('api','worker','monitor','db','caddy')) {
        $id=Invoke-Compose @('ps','-a','-q',$service)
        if (!$id -or $id.Contains("`n")) { throw 'Expected exactly one container per approved service' }
        $allowed[$id]=$true
    }
    $network=@(Invoke-Native $script:docker @('network','inspect',$script:dbNetwork) | ConvertFrom-Json)[0]
    foreach($property in $network.Containers.PSObject.Properties) {
        if (!$allowed.ContainsKey($property.Name)) { throw 'Unreviewed container attached to production network' }
    }
    $processes=@(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object {
        $_.CommandLine -match '(?i)(-m\s+app\.(serve|worker|monitor)|uvicorn\s+app\.api)'
    })
    if ($processes.Count) { throw 'Unreviewed host application writer process found' }
}

function Stop-MigrationWriters {
    Invoke-Compose @('stop','--timeout','60','api','worker','monitor') | Out-Null
    foreach($service in @('api','worker','monitor')) {
        $id=Invoke-Compose @('ps','-a','-q',$service)
        $container=@(Invoke-Native $script:docker @('inspect',$id) | ConvertFrom-Json)[0]
        if ($container.State.Running -or $container.State.Restarting) { throw 'Writer did not stop' }
    }
    Assert-NoOtherWriters
    $deadline=[DateTime]::UtcNow.AddSeconds(60)
    do {
        $connections=Invoke-DbSql "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid()"
        if ($connections -eq '0') { return }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Database sessions remain after stopping every approved writer'
}

function Save-FinalBackup {
    param([switch]$Online)
    if (!$Online) {
        Stop-MigrationWriters
        $before=Get-ProductionSnapshot
        Write-CdText (Join-Path $operationRoot 'baseline.json') $before
    }
    $remote='/tmp/cd-'+$operation.id+'.dump'
    Invoke-Native $script:docker @('exec',$script:dbId,'pg_dump','-U','stroy','-d','stroy','-Fc','-f',$remote) 1800 | Out-Null
    $dump=Join-Path $operationRoot 'before.dump'
    Invoke-Native $script:docker @('cp',"${script:dbId}:$remote",$dump) | Out-Null
    $operation.backupHash=Assert-BackupFile $dump
    $operation.backupBytes=(Get-Item -LiteralPath $dump).Length
    Invoke-Native $script:docker @('exec',$script:dbId,'pg_restore','--list',$remote) | Out-Null
    if (!$Online) { Assert-SnapshotPreserved $before (Get-ProductionSnapshot) }
    Save-MigrationOperation
}

function New-RuntimePlan {
    $original=Join-Path $operationRoot 'runtime-original'
    $planned=Join-Path $operationRoot 'runtime-planned'
    New-Item -ItemType Directory -Path $original,$planned,(Join-Path $planned 'controller') -Force | Out-Null
    foreach($file in @('.env','compose.yaml','compose.gpu.yaml','Caddyfile')) {
        Copy-Item -LiteralPath (Join-Path $cfg.RuntimeRoot $file) -Destination (Join-Path $original $file)
        Copy-Item -LiteralPath (Join-Path $cfg.RuntimeRoot $file) -Destination (Join-Path $planned $file)
    }
    foreach($file in @('compose.cd.json','controller/runtime-bootstrap.py')) {
        $from=Join-Path $cfg.RuntimeRoot $file
        if (Test-Path -LiteralPath $from) {
            New-Item -ItemType Directory -Path (Split-Path (Join-Path $original $file) -Parent) -Force | Out-Null
            Copy-Item -LiteralPath $from -Destination (Join-Path $original $file)
        }
    }
    $caddy=[IO.File]::ReadAllText((Join-Path $original 'Caddyfile'))
    $pattern='(?m)^[ \t]*reverse_proxy[ \t]+api:8000[ \t]*\r?$'
    if ([regex]::Matches($caddy,$pattern).Count -ne 1) { throw 'Caddy maintenance patch requires one reviewed API upstream directive' }
    Write-CdText (Join-Path $operationRoot 'Caddyfile.maintenance') ([regex]::Replace($caddy,$pattern,'    respond "Service maintenance" 503'))
    $control=Join-Path $cfg.StateRoot 'control'
    $bootstrap=Join-Path $cfg.RuntimeRoot 'controller'
    $services=@{}
    foreach($service in @('api','worker','monitor')) {
        $services[$service]=@{command=@('python','/opt/stroy-cd/runtime-bootstrap.py',$service);
            environment=@{CD_EXPECTED_REVISION='004'};
            volumes=@(@{type='bind';source=$bootstrap;target='/opt/stroy-cd';read_only=$true},@{type='bind';source=$control;target='/run/stroy-cd';read_only=$true})}
    }
    # Ask Compose to parse .env; retain explicitly empty values and do not parse it as shell.
    $sentinel='cd-absent-'+[Guid]::NewGuid().ToString('N')
    $probe=Join-Path $operationRoot 'environment-probe.json'
    Write-ReleaseState $probe @{services=@{probe=@{image='unused';environment=@{VALUE=('${GEOCODER_REVERSE_URL-'+$sentinel+'}')}}}}
    $environment=Invoke-Native $script:docker @('compose','--project-directory',$cfg.RuntimeRoot,'--env-file',(Join-Path $cfg.RuntimeRoot '.env'),'-f',$probe,'config','--format','json') | ConvertFrom-Json
    $geocoder=[string]$environment.services.probe.environment.VALUE
    if ($runtime.services.api.environment.PSObject.Properties.Name -contains 'GEOCODER_REVERSE_URL') {
        $geocoder=[string]$runtime.services.api.environment.GEOCODER_REVERSE_URL
    } elseif ($geocoder -ceq $sentinel) { $geocoder='https://photon.komoot.io/reverse' }
    if ($geocoder.Contains("`n") -or $geocoder.Contains("`r")) { throw 'Invalid configured geocoder value' }
    $services.api.environment.GEOCODER_REVERSE_URL=$geocoder.Replace('$','$$')
    $services.api.healthcheck=@{test=@('CMD','python','/opt/stroy-cd/runtime-bootstrap.py','health')}
    Write-ReleaseState (Join-Path $planned 'compose.cd.json') @{services=$services}
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'runtime-bootstrap.py') -Destination (Join-Path $planned 'controller/runtime-bootstrap.py')
    $operation.geocoder=$geocoder
    $operation.originalFingerprint=Get-RuntimeFingerprint $original
    $operation.targetFingerprint=Get-RuntimeFingerprint $planned
    $operation.runtimeFiles=@{}
    foreach($file in @('.env','compose.yaml','compose.gpu.yaml','Caddyfile','compose.cd.json','controller/runtime-bootstrap.py')) {
        $hashes=@()
        foreach($root in @($original,$planned)) {
            $path=Join-Path $root $file
            if (Test-Path -LiteralPath $path) { $hashes += (Get-FileHash -LiteralPath $path).Hash }
            else { $hashes += 'absent' }
        }
        if ($file -eq 'Caddyfile') { $hashes += (Get-FileHash -LiteralPath (Join-Path $operationRoot 'Caddyfile.maintenance')).Hash }
        $operation.runtimeFiles[$file]=@($hashes | Select-Object -Unique)
    }
    # Rollback at 003 uses the same reviewed bootstrap, with only its revision changed.
    foreach($service in @('api','worker','monitor')) { $services[$service].environment.CD_EXPECTED_REVISION='003' }
    Write-ReleaseState (Join-Path $operationRoot 'compose.cd.003.json') @{services=$services}
    $operation.runtimeFiles['compose.cd.json'] += (Get-FileHash -LiteralPath (Join-Path $operationRoot 'compose.cd.003.json')).Hash
}

function Assert-PlannedRuntime {
    foreach($file in @('.env','compose.yaml','compose.gpu.yaml','Caddyfile','compose.cd.json','controller/runtime-bootstrap.py')) {
        $path=Join-Path $cfg.RuntimeRoot $file
        $hash=if (Test-Path -LiteralPath $path) { (Get-FileHash -LiteralPath $path).Hash } else { 'absent' }
        $allowed=if ($operation.runtimeFiles -is [hashtable]) { $operation.runtimeFiles[$file] } else { $operation.runtimeFiles.$file }
        if ($hash -cnotin $allowed) { throw 'Runtime changed outside the reviewed migration operation' }
    }
}

function Copy-PlannedFile {
    param([string]$From, [string]$To)
    $relative=$To.Substring($cfg.RuntimeRoot.TrimEnd('/','\').Length).TrimStart('/','\').Replace('\','/')
    $allowed=if ($operation.runtimeFiles -is [hashtable]) { $operation.runtimeFiles[$relative] } else { $operation.runtimeFiles.$relative }
    if ((Get-FileHash -LiteralPath $From).Hash -cnotin $allowed) { throw 'Saved runtime patch content changed' }
    New-Item -ItemType Directory -Path (Split-Path $To -Parent) -Force | Out-Null
    $temp=$To+'.new'
    [IO.File]::Copy($From,$temp,$true)
    if (Test-Path -LiteralPath $To) { [IO.File]::Replace($temp,$To,[NullString]::Value) }
    else { [IO.File]::Move($temp,$To) }
}

function Reload-Caddy {
    $caddy=Invoke-Compose @('ps','-q','caddy')
    $path=Join-Path $cfg.RuntimeRoot 'Caddyfile'
    Invoke-Native $script:docker @('cp',$path,"${caddy}:/tmp/cd-Caddyfile") | Out-Null
    Invoke-Native $script:docker @('exec',$caddy,'caddy','validate','--config','/tmp/cd-Caddyfile','--adapter','caddyfile') | Out-Null
    Invoke-Native $script:docker @('exec',$caddy,'caddy','reload','--config','/tmp/cd-Caddyfile','--adapter','caddyfile') | Out-Null
    $mounted=Invoke-Native $script:docker @('exec',$caddy,'sha256sum','/etc/caddy/Caddyfile')
    if ($mounted.Split(' ')[0] -cne (Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant()) { throw 'Persistent Caddy bind mount does not match approved file' }
}

function Enable-MigrationMaintenance {
    Start-MaintenanceWindow $operation
    Save-MigrationOperation
    $control=Join-Path $cfg.StateRoot 'control'
    New-Item -ItemType Directory -Path $control -Force | Out-Null
    Write-ReleaseState (Join-Path $control 'maintenance.json') @{operationId=$operation.id;token=$operation.probeToken}
    Assert-PlannedRuntime
    Copy-PlannedFile (Join-Path $operationRoot 'Caddyfile.maintenance') (Join-Path $cfg.RuntimeRoot 'Caddyfile')
    Reload-Caddy
    Save-MigrationOperation
}

function Install-MigrationRuntime {
    param([string]$Revision='004')
    Assert-PlannedRuntime
    $planned=Join-Path $operationRoot 'runtime-planned'
    Copy-PlannedFile (Join-Path $planned 'controller/runtime-bootstrap.py') (Join-Path $cfg.RuntimeRoot 'controller/runtime-bootstrap.py')
    $overlay=if ($Revision -eq '003') { Join-Path $operationRoot 'compose.cd.003.json' } else { Join-Path $planned 'compose.cd.json' }
    Copy-PlannedFile $overlay (Join-Path $cfg.RuntimeRoot 'compose.cd.json')
    $script:compose=Get-ComposeArguments $cfg
    $rendered=Invoke-Compose @('config','--format','json') | ConvertFrom-Json
    if ($rendered.services.api.environment.GEOCODER_REVERSE_URL -cne $operation.geocoder) { throw 'Geocoder value not preserved in runtime' }
    # Compare all existing runtime fields except the explicitly reviewed patch.
    $before=Get-Content (Join-Path $operationRoot 'runtime-before.json') -Raw | ConvertFrom-Json
    foreach($service in @('api','worker','monitor')) {
        foreach($field in @('command','healthcheck')) { $rendered.services.$service.PSObject.Properties.Remove($field); $before.services.$service.PSObject.Properties.Remove($field) }
        foreach($key in @('CD_EXPECTED_REVISION','GEOCODER_REVERSE_URL')) { $rendered.services.$service.environment.PSObject.Properties.Remove($key); $before.services.$service.environment.PSObject.Properties.Remove($key) }
        $rendered.services.$service.volumes=@($rendered.services.$service.volumes | Where-Object {$_.target -notin @('/opt/stroy-cd','/run/stroy-cd')})
        $before.services.$service.volumes=@($before.services.$service.volumes | Where-Object {$_.target -notin @('/opt/stroy-cd','/run/stroy-cd')})
    }
    if (($before | ConvertTo-Json -Depth 30 -Compress) -cne ($rendered | ConvertTo-Json -Depth 30 -Compress)) { throw 'Unapproved runtime change outside allowlist' }
}

function Open-MigrationTraffic {
    Assert-PlannedRuntime
    # Persist intent first: after this point user/queue writes cannot be excluded.
    $operation.trafficOpened=$true
    Save-MigrationOperation
    Copy-PlannedFile (Join-Path $operationRoot 'runtime-original/Caddyfile') (Join-Path $cfg.RuntimeRoot 'Caddyfile')
    Reload-Caddy
    Remove-Item -LiteralPath (Join-Path $cfg.StateRoot 'control/maintenance.json')
    Complete-MaintenanceWindow $operation
    Save-MigrationOperation
}

function Stop-OperationContainers {
    $ids=Invoke-Native $script:docker @('ps','-a','-q','--filter',('label=stroykontur.cd.operation='+$operation.id))
    if ($ids) {
        foreach($id in $ids.Split("`n")) {
            Invoke-Native $script:docker @('stop','--time','30',$id.Trim()) | Out-Null
            Invoke-Native $script:docker @('rm',$id.Trim()) | Out-Null
        }
    }
}

function Invoke-MigrationRehearsal {
    $dump=Join-Path $operationRoot 'before.dump'
    Assert-BackupFile $dump $operation.backupHash | Out-Null
    $name='stroykontur-migration-'+$operation.id.Substring(0,12)+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
    $network=$name+'-net'
    $db=$name+'-db'
    $api=$name+'-api'
    $legacy=$name+'-legacy'
    $volume=$name+'-data'
    $password=[Guid]::NewGuid().ToString('N')+[Guid]::NewGuid().ToString('N')
    $dbEnv=Join-Path $operationRoot 'rehearsal-db.env'
    $appEnv=Join-Path $operationRoot 'rehearsal-app.env'
    Write-CdText $dbEnv "POSTGRES_USER=stroy`nPOSTGRES_DB=stroy`nPOSTGRES_PASSWORD=$password`n"
    Write-CdText $appEnv ("DATABASE_URL=postgresql+psycopg://stroy:${password}@db:5432/stroy`nSTROY_DATA_DIR=/data`nCD_EXPECTED_REVISION=004`nGEOCODER_REVERSE_URL=`nCD_ISOLATED_REHEARSAL=1`nREQUIRE_CUDA=0`nPGOPTIONS=-c statement_timeout=300000 -c lock_timeout=15000`n")
    Invoke-Native $script:docker @('network','create','--internal','--label',('stroykontur.cd.operation='+$operation.id),$network) | Out-Null
    Invoke-Native $script:docker @('volume','create','--label',('stroykontur.cd.operation='+$operation.id),$volume) | Out-Null
    $operation.rehearsalVolume=$volume
    Save-MigrationOperation
    try {
        Invoke-Native $script:docker @('run','-d','--name',$db,'--label',('stroykontur.cd.operation='+$operation.id),'--network',$network,'--network-alias','db',
            '--env-file',$dbEnv,'--mount',('type=volume,source='+$volume+',target=/var/lib/postgresql/data'),$script:postgresImage) | Out-Null
        $ready=$false
        for($i=0;$i -lt 40;$i++) {
            try { Invoke-Native $script:docker @('exec',$db,'pg_isready','-U','stroy','-d','stroy') 10 | Out-Null; $ready=$true; break } catch { Start-Sleep -Seconds 2 }
        }
        if (!$ready) { throw 'Isolated PostgreSQL did not become ready' }
        Invoke-Native $script:docker @('cp',$dump,"${db}:/tmp/before.dump") | Out-Null
        $inside=Invoke-Native $script:docker @('exec',$db,'sha256sum','/tmp/before.dump')
        if ($inside.Split(' ')[0] -cne $operation.backupHash) { throw 'Copied archive checksum mismatch' }
        Invoke-Native $script:docker @('exec',$db,'pg_restore','--exit-on-error','--single-transaction','--no-owner','--no-privileges','-U','stroy','-d','stroy','/tmp/before.dump') 1800 | Out-Null
        $restored=Invoke-DatabaseHelper $operation.request.images.api $network $appEnv 'snapshot'
        if (($restored | ConvertFrom-Json).revision -cne '003') { throw 'Restored backup revision differs from 003' }
        if ($operation.mode -eq 'check') { Write-CdText (Join-Path $operationRoot 'baseline.json') $restored }
        else { Assert-SnapshotPreserved (Get-Content (Join-Path $operationRoot 'baseline.json') -Raw) $restored }
        Invoke-DatabaseHelper $operation.request.images.api $network $appEnv 'upgrade' | Out-Null
        Invoke-DatabaseHelper $operation.request.images.api $network $appEnv 'schema' | Out-Null
        $after=Invoke-DatabaseHelper $operation.request.images.api $network $appEnv 'snapshot'
        Assert-SnapshotPreserved $restored $after
        Invoke-DatabaseHelper $operation.previous.images.api $network $appEnv 'compatibility' | Out-Null
        foreach($entry in @(@{name=$api;image=$operation.request.images.api},@{name=$legacy;image=$operation.previous.images.api})) {
            Invoke-Native $script:docker @('run','-d','--name',$entry.name,'--label',('stroykontur.cd.operation='+$operation.id),'--network',$network,
                '--env-file',$appEnv,'--mount',('type=bind,source='+$PSScriptRoot+',target=/opt/stroy-cd,readonly'),$entry.image,'python','/opt/stroy-cd/runtime-bootstrap.py','api') | Out-Null
            $ready=$false
            for($i=0;$i -lt 45;$i++) {
                try { Invoke-Native $script:docker @('exec',$entry.name,'python','/opt/stroy-cd/runtime-bootstrap.py','probe') 30 | Out-Null; $ready=$true; break } catch { Start-Sleep -Seconds 2 }
            }
            if (!$ready) { throw 'Rehearsal application HTTP failed' }
        }
        Invoke-Native $script:docker @('exec',$api,'python','/opt/stroy-cd/migration-db.py','smoke') 300 | Out-Null
        Invoke-Native $script:docker @('exec',$api,'python','/opt/stroy-cd/runtime-bootstrap.py','probe-detector') 60 | Out-Null
        # The isolated HTTP upload intentionally adds its idempotency record.
        Invoke-DatabaseHelper $operation.request.images.api $network $appEnv 'schema' | Out-Null
        $operation.compatibilityVerified=$true
        $operation.rehearsalVerified=$true
        Save-MigrationOperation
    } finally {
        foreach($container in @($api,$legacy,$db)) {
            try { Invoke-Native $script:docker @('rm','-f',$container) | Out-Null } catch { }
        }
        try { Invoke-Native $script:docker @('network','rm',$network) | Out-Null } catch { }
        # Keep the isolated volume and protected archive for diagnosis; never delete production data.
    }
}
