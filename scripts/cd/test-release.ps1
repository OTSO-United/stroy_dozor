$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'release-policy.ps1')
$passed = 0
function Assert-Equal($Actual, $Expected) {
    if (($Actual | ConvertTo-Json -Compress) -cne ($Expected | ConvertTo-Json -Compress)) {
        throw "Expected [$Expected], got [$Actual]"
    }
}
function Test-Scenario {
    param([string]$Name, [string]$Failure='', $Current=$null, [string]$Sha='new', [long]$Number=2, [string[]]$Expected, [switch]$Throws)
    $events = [Collections.Generic.List[string]]::new()
    $steps = @{}
    foreach ($step in @('Preflight','Build','Snapshot','Backup','Pending','Activate','Verify','Commit','ClearPending')) {
        $nameCopy = $step
        $steps[$step] = {
            param($release)
            $suffix = if ($release -and $release.sha) { ':' + $release.sha } else { '' }
            $events.Add($nameCopy + $suffix)
            if ($Failure -eq $nameCopy -or ($Failure -eq 'health' -and $nameCopy -eq 'Verify' -and $release.sha -eq 'new') -or
                ($Failure -eq 'rollback' -and $nameCopy -eq 'Verify')) { throw 'Injected failure' }
            if ($nameCopy -eq 'Snapshot') { return @{sha='old'} }
        }.GetNewClosure()
    }
    $caught = $false
    try { Invoke-ReleaseTransaction @{sha=$Sha;runNumber=$Number} $Current $steps | Out-Null }
    catch { $caught = $true }
    Assert-Equal $caught ([bool]$Throws)
    Assert-Equal ($events -join ',') ($Expected -join ',')
    Write-Host "PASS $Name"
    $script:passed++
}
Test-Scenario 'successful update' -Expected Preflight,Build,Snapshot,Backup,Pending:old,Activate:new,Verify:new,Commit:new
Test-Scenario 'same SHA checks health without rebuilding' -Current @{sha='new';runNumber=2} -Expected Preflight,Verify:new
Test-Scenario 'build failure leaves production untouched' -Failure Build -Throws -Expected Preflight,Build
Test-Scenario 'Docker/model preflight failure leaves production untouched' -Failure Preflight -Throws -Expected Preflight
Test-Scenario 'backup failure prevents recreation' -Failure Backup -Throws -Expected Preflight,Build,Snapshot,Backup
Test-Scenario 'health failure restores old image' -Failure health -Throws -Expected Preflight,Build,Snapshot,Backup,Pending:old,Activate:new,Verify:new,Activate:old,Verify:old,ClearPending
Test-Scenario 'failed rollback retains pending state' -Failure rollback -Throws -Expected Preflight,Build,Snapshot,Backup,Pending:old,Activate:new,Verify:new,Activate:old,Verify:old
Test-Scenario 'state commit failure retains recovery marker' -Failure Commit -Throws -Expected Preflight,Build,Snapshot,Backup,Pending:old,Activate:new,Verify:new,Commit:new
Test-Scenario 'newer sequential push deploys' -Current @{sha='old';runNumber=1} -Expected Preflight,Build,Snapshot,Backup,Pending:old,Activate:new,Verify:new,Commit:new
Test-Scenario 'late older push cannot overwrite newer SHA' -Current @{sha='new';runNumber=3} -Sha old -Number 2 -Expected Preflight

$temp = Join-Path ([IO.Path]::GetTempPath()) ('stroy-cd-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    [IO.File]::WriteAllText((Join-Path $temp 'best.onnx'),'fixture, not a model')
    $hash = (Get-FileHash (Join-Path $temp 'best.onnx')).Hash.ToLowerInvariant()
    Write-ReleaseState (Join-Path $temp 'manifest.json') @{weights='best.onnx';sha256=$hash}
    Assert-Equal (Assert-ModelPackage $temp) $hash
    [IO.File]::AppendAllText((Join-Path $temp 'best.onnx'),'changed')
    $caught=$false
    try { Assert-ModelPackage $temp | Out-Null } catch { $caught=$true }
    Assert-Equal $caught $true
    Write-Host 'PASS actual file SHA corruption'
    $passed++
    $state = Join-Path $temp 'state.json'
    Write-ReleaseState $state @{sha='old'}
    Write-ReleaseState $state @{sha='new'}
    Assert-Equal (Get-Content $state -Raw | ConvertFrom-Json).sha 'new'
    $stream = [IO.File]::Open((Join-Path $temp 'lock'),'OpenOrCreate','ReadWrite','None')
    try {
        $caught=$false
        try { $other=[IO.File]::Open((Join-Path $temp 'lock'),'OpenOrCreate','ReadWrite','None'); $other.Dispose() } catch [IO.IOException] { $caught=$true }
        Assert-Equal $caught $true
    } finally { $stream.Dispose() }
    Write-Host 'PASS atomic state replacement and exclusive lock'
    $passed++
    $migrations=Join-Path $temp 'migrations'
    New-Item -ItemType Directory -Path $migrations | Out-Null
    $migration=Join-Path $migrations '001.py'
    [IO.File]::WriteAllText($migration,"revision = '001'`r`n")
    $digest=Get-MigrationDigest $migrations
    [IO.File]::WriteAllText($migration,"revision = '001'`n")
    Assert-Equal (Get-MigrationDigest $migrations) $digest
    [IO.File]::WriteAllText($migration,"revision = '002'`n")
    Assert-Equal ((Get-MigrationDigest $migrations) -eq $digest) $false
    Write-Host 'PASS migration changes detected, CRLF/LF normalized'
    $passed++
} finally {
    $resolved=[IO.Path]::GetFullPath($temp)
    if (!$resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()),[StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path $resolved -Leaf) -notlike 'stroy-cd-test-*') { throw 'Unsafe test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse
}
Write-Host "$passed deployment logic checks passed"
