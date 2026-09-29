. (Join-Path $PSScriptRoot 'release-policy.ps1')

function Get-NormalizedFileHash {
    param([string]$Path)
    $algorithm=[Security.Cryptography.SHA256]::Create()
    try {
        $bytes=[Text.Encoding]::UTF8.GetBytes([IO.File]::ReadAllText($Path).Replace("`r`n","`n"))
        return ([BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
    } finally { $algorithm.Dispose() }
}

function Assert-MigrationRequest {
    param([string]$ExpectedRevision, [string]$TargetRevision, [string]$Mode, [string]$OperationId='')
    if ($ExpectedRevision -cne '003' -or $TargetRevision -cne '004') { throw 'Only reviewed 003 -> 004 is supported' }
    if ($Mode -notin @('check','release','recover')) { throw 'Unknown migration mode' }
    if ($Mode -eq 'recover') {
        if ($OperationId -cnotmatch '^[a-f0-9]{32}$') { throw 'Recovery requires an operation ID' }
    } elseif ($OperationId) { throw 'Operation ID is valid only for recovery' }
}

function Assert-BackupFile {
    param([string]$Path, [string]$ExpectedHash='')
    if (!(Test-Path -LiteralPath $Path -PathType Leaf) -or (Get-Item -LiteralPath $Path).Length -lt 1024) { throw 'Backup is absent or too small' }
    $stream = [IO.File]::OpenRead($Path)
    try {
        $magic = New-Object byte[] 5
        if ($stream.Read($magic,0,5) -ne 5 -or [Text.Encoding]::ASCII.GetString($magic) -cne 'PGDMP') { throw 'Not a PostgreSQL custom archive' }
    } finally { $stream.Dispose() }
    $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ExpectedHash -and $hash -cne $ExpectedHash) { throw 'Backup checksum changed' }
    return $hash
}

function Get-MigrationDisposition {
    param([string]$Sha, [long]$RunNumber, [hashtable]$Current, [string]$ActualRevision, [string]$ModelSha)
    if ($RunNumber -lt [long]$Current.runNumber) { throw 'Superseded source CI cannot migrate a newer release' }
    if ($Current.sha -ceq $Sha) {
        if ($ActualRevision -cne '004' -or !$Current.ContainsKey('schemaRevision') -or $Current.schemaRevision -cne '004') { throw 'Current SHA has no verified target migration state' }
        if ($Current.modelSha -cne $ModelSha) { throw 'Model changes require a new verified source SHA' }
        return 'already-current'
    }
    if ($ActualRevision -cne '003') { throw 'Actual revision differs from expected base; use operation recovery after commit' }
    return 'release'
}

function Get-MigrationFailureAction {
    param([string]$Revision, [bool]$DataUnchanged, [bool]$TrafficOpened, [bool]$CompatibilityVerified)
    if ($TrafficOpened -or !$DataUnchanged) { return 'hold-maintenance' }
    if ($Revision -eq '003') { return 'restore-previous' }
    if ($Revision -eq '004' -and $CompatibilityVerified) { return 'compatible-recovery-only' }
    return 'hold-maintenance'
}

function Start-MaintenanceWindow {
    param([hashtable]$Operation, [DateTimeOffset]$Now=[DateTimeOffset]::UtcNow)
    if (!$Operation.ContainsKey('maintenanceWindows')) {
        $Operation.maintenanceWindows=@()
        if ($Operation.downtimeStarted) {
            $Operation.maintenanceWindows=@(@{started=$Operation.downtimeStarted;ended=$Operation.downtimeEnded})
        }
    }
    $windows=@($Operation.maintenanceWindows)
    if (!$windows.Count -or $windows[-1].ended) { $windows+=@{started=$Now.ToString('o');ended=''} }
    $Operation.maintenanceWindows=$windows
    $Operation.downtimeStarted=$windows[0].started
    $Operation.downtimeEnded=''
}

function Complete-MaintenanceWindow {
    param([hashtable]$Operation, [DateTimeOffset]$Now=[DateTimeOffset]::UtcNow)
    if (!$Operation.ContainsKey('maintenanceWindows') -or !@($Operation.maintenanceWindows).Count) { throw 'Maintenance window was not started' }
    $Operation.maintenanceWindows[-1].ended=$Now.ToString('o')
    $Operation.downtimeEnded=$Now.ToString('o')
}

function Get-MaintenanceSummary {
    param($Operation, [DateTimeOffset]$Now=[DateTimeOffset]::UtcNow)
    if (!$Operation.downtimeStarted) { return 'not started' }
    $hasWindows=if ($Operation -is [hashtable]) { $Operation.ContainsKey('maintenanceWindows') } else { $Operation.PSObject.Properties.Name -contains 'maintenanceWindows' }
    $windows=if ($hasWindows) { @($Operation.maintenanceWindows) } else { @(@{started=$Operation.downtimeStarted;ended=$Operation.downtimeEnded}) }
    $seconds=0.0
    $ongoing=$false
    foreach($window in $windows) {
        $end=if ($window.ended) { [DateTimeOffset]::Parse($window.ended) } else { $ongoing=$true; $Now }
        $seconds+=($end-[DateTimeOffset]::Parse($window.started)).TotalSeconds
    }
    $text=[Math]::Round($seconds,1).ToString('0.0',[Globalization.CultureInfo]::InvariantCulture)+' s across '+@($windows).Count+' window(s)'
    if (!$hasWindows) { $text+=' (legacy interval; may include time between attempts)' }
    if ($ongoing) { $text+=' (maintenance not confirmed ended)' }
    return $text
}

function Invoke-MigrationStages {
    param([hashtable]$Steps, [switch]$CheckOnly)
    & $Steps.Preflight
    if (& $Steps.AlreadyCurrent) { return 'already-current' }
    & $Steps.Build
    if ($CheckOnly) {
        & $Steps.CheckBackup
        & $Steps.Rehearse
        return 'check-passed-no-production-change'
    }
    & $Steps.Pending
    try {
        foreach ($stage in @('Maintenance','Quiesce','Backup','Rehearse','Recheck','Upgrade','Runtime','Activate','Verify','CommitPrepared','Open','VerifyOpened','CommitComplete')) {
            & $Steps.Stage $stage
            & $Steps[$stage]
        }
    } catch {
        & $Steps.Failure
        throw 'Migration release failed; inspect operation summary and use GitHub recovery if pending remains'
    }
    return 'deployed-with-migration'
}
function Assert-CaddyConfigMount {
    param($Mounts, [string]$RuntimeRoot)
    $mount=@($Mounts | Where-Object { $_.Destination -ceq '/etc/caddy/Caddyfile' })
    $expected=[IO.Path]::GetFullPath((Join-Path $RuntimeRoot 'Caddyfile')).Replace('\','/')
    if ($mount.Count -ne 1 -or $mount[0].Type -cne 'bind' -or $mount[0].RW -ne $false -or
        [IO.Path]::GetFullPath([string]$mount[0].Source).Replace('\','/') -ine $expected) {
        throw 'Caddy must mount the protected runtime Caddyfile read-only; reconcile the container before release'
    }
}
