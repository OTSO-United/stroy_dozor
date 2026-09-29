Set-StrictMode -Version Latest

function Enter-DeploymentLock {
    param([string]$StateRoot, [int]$WaitSeconds=600)
    $deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
    while ($true) {
        try { return [IO.File]::Open((Join-Path $StateRoot 'deployment.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) { throw 'Another deployment holds the lock' }
            Start-Sleep -Seconds 2
        }
    }
}

function Assert-ModelPackage {
    param([string]$Directory)
    $manifest = Get-Content -LiteralPath (Join-Path $Directory 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.weights -cne 'best.onnx' -or $manifest.sha256 -cnotmatch '^[a-f0-9]{64}$') {
        throw 'Invalid packaged model manifest'
    }
    $hash = (Get-FileHash -LiteralPath (Join-Path $Directory 'best.onnx') -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -cne $manifest.sha256) { throw 'Model SHA256 mismatch' }
    return $hash
}

function Get-MigrationDigest {
    param([string]$Directory)
    $files = @(Get-ChildItem -LiteralPath $Directory -File -Recurse | Where-Object { $_.Extension -eq '.py' } | Sort-Object FullName)
    if (!$files.Count) { throw 'No migrations found' }
    $entries = foreach ($file in $files) {
        $relative = $file.FullName.Substring($Directory.TrimEnd('\','/').Length).Replace('\','/')
        $relative + ':' + ([IO.File]::ReadAllText($file.FullName).Replace("`r`n", "`n"))
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($entries -join "`n"))))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Write-ReleaseState {
    param([string]$Path, $Value)
    $temp = $Path + '.new'
    [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 15), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temp, $Path, [NullString]::Value) }
    else { [IO.File]::Move($temp, $Path) }
}

function Invoke-ReleaseTransaction {
    param($Request, $Current, [hashtable]$Steps)
    & $Steps.Preflight
    if ($Current -and [long]$Request.runNumber -lt [long]$Current.runNumber) {
        return 'superseded'
    }
    if ($Current -and $Request.sha -ceq $Current.sha) {
        & $Steps.Verify $Current
        return 'already-current'
    }
    & $Steps.Build
    $previous = & $Steps.Snapshot
    & $Steps.Backup
    & $Steps.Pending $previous
    try {
        & $Steps.Activate $Request
        & $Steps.Verify $Request
    } catch {
        $failure = $_
        try {
            & $Steps.Activate $previous
            & $Steps.Verify $previous
            & $Steps.ClearPending
        } catch {
            throw "Deployment and rollback failed; pending state retained. Recovery required. $($_.Exception.Message)"
        }
        throw "Deployment failed; previous images restored and verified. $($failure.Exception.Message)"
    }
    # A state-write failure retains pending; recovery reconciles images and pointer.
    & $Steps.Commit $Request
    return 'deployed'
}
