$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'migration-policy.ps1')
$passed=0
function Assert-Cd($Condition,[string]$Message) { if (!$Condition) { throw $Message } }
function Scenario([string]$Failure='', [switch]$CheckOnly) {
    $events=[Collections.Generic.List[string]]::new()
    $steps=@{}
    foreach($name in @('Preflight','AlreadyCurrent','Build','CheckBackup','Pending','Maintenance','Quiesce','Backup','Rehearse','Recheck','Upgrade','Runtime','Activate','Verify','CommitPrepared','Open','VerifyOpened','CommitComplete','Failure')) {
        $copy=$name
        $steps[$name]={
            $events.Add($copy)
            if ($copy -eq $Failure) { throw 'Injected failure' }
            if ($copy -eq 'AlreadyCurrent') { return $false }
        }.GetNewClosure()
    }
    $steps.Stage={param($Stage)}
    $caught=$false
    try { Invoke-MigrationStages $steps -CheckOnly:$CheckOnly | Out-Null } catch { $caught=$true }
    Assert-Cd ($caught -eq [bool]$Failure) 'Unexpected stage result'
    return ,$events
}
$events=Scenario
Assert-Cd (($events -join ',') -eq 'Preflight,AlreadyCurrent,Build,Pending,Maintenance,Quiesce,Backup,Rehearse,Recheck,Upgrade,Runtime,Activate,Verify,CommitPrepared,Open,VerifyOpened,CommitComplete') 'Unsafe migration order'
Write-Host 'PASS pending precedes mutation; final dump follows quiescence; rehearsal precedes upgrade; probes precede opening'
$passed++
$events=Scenario -CheckOnly
Assert-Cd (($events -join ',') -eq 'Preflight,AlreadyCurrent,Build,CheckBackup,Rehearse') 'Check mode modified production'
Write-Host 'PASS check mode does not mutate production'
$passed++
foreach($failure in @('Build','Backup','Rehearse','Recheck','Upgrade','Activate','Verify','CommitPrepared','Open','VerifyOpened','CommitComplete')) {
    $events=Scenario $failure
    Assert-Cd (!$events.Contains('CommitComplete') -or $failure -eq 'CommitComplete') 'Failure reported as committed'
    if ($failure -ne 'Build') { Assert-Cd ($events[-1] -eq 'Failure') 'Recovery policy not called' }
    if ($failure -in @('Backup','Rehearse','Recheck')) { Assert-Cd (!$events.Contains('Upgrade')) 'Unsafe production upgrade' }
    Write-Host "PASS fail closed at $failure"
    $passed++
}
Assert-Cd ((Get-MigrationFailureAction '003' $true $false $false) -eq 'restore-previous') 'Base rollback classification'
Assert-Cd ((Get-MigrationFailureAction '004' $true $false $true) -eq 'compatible-recovery-only') 'Post-commit recovery classification'
foreach($revision in @('003','004','unknown')) {
    Assert-Cd ((Get-MigrationFailureAction $revision $true $true $true) -eq 'hold-maintenance') 'Opened traffic must disable old-image recovery'
    Assert-Cd ((Get-MigrationFailureAction $revision $false $false $true) -eq 'hold-maintenance') 'Changed data must disable rollback'
}
Write-Host 'PASS committed migration/cancellation and possible user writes require explicit recovery'
$passed++
$root=Join-Path ([IO.Path]::GetTempPath()) ('stroy-cd-migration-unit-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$lock=Enter-DeploymentLock $root
try {
    $caught=$false
    try { $other=Enter-DeploymentLock $root -WaitSeconds 0; $other.Dispose() } catch { $caught=$true }
    Assert-Cd $caught 'Shared lock allowed overlap'
} finally { $lock.Dispose() }
Write-Host 'PASS shared deployment.lock excludes concurrent migration and ordinary CD'
$passed++
$path=Join-Path $root 'backup.dump'
$bytes=New-Object byte[] 2048
[Array]::Copy([Text.Encoding]::ASCII.GetBytes('PGDMP'),$bytes,5)
[IO.File]::WriteAllBytes($path,$bytes)
$hash=Assert-BackupFile $path
$bytes[100]=1
[IO.File]::WriteAllBytes($path,$bytes)
$caught=$false
try { Assert-BackupFile $path $hash | Out-Null } catch { $caught=$true }
Assert-Cd $caught 'Corrupted backup was accepted'
Write-Host 'PASS corrupt archive checksum rejected'
$passed++
foreach($values in @(@('002','004','release',''),@('003','head','release',''),@('003','004','recover','../state'))) {
    $caught=$false
    try { Assert-MigrationRequest @values } catch { $caught=$true }
    Assert-Cd $caught 'Unsafe revision or recovery ID accepted'
}
Write-Host 'PASS revision and recovery identity validation'
$passed++
$current=@{sha='same';runNumber=9;schemaRevision='004';modelSha='approved'}
Assert-Cd ((Get-MigrationDisposition 'same' 9 $current '004' 'approved') -eq 'already-current') 'Same SHA must be idempotent'
foreach($case in @(@('old',8,'003','approved'),@('new',10,'004','approved'),@('same',9,'003','approved'),@('same',9,'004','changed'))) {
    $caught=$false
    try { Get-MigrationDisposition $case[0] $case[1] $current $case[2] $case[3] | Out-Null } catch { $caught=$true }
    Assert-Cd $caught 'Stale run/wrong revision/model change accepted'
}
Write-Host 'PASS repeat SHA, stale run, wrong actual revision and changed model policy'
$passed++
$policyFile=Join-Path $root 'policy.json'
[IO.File]::WriteAllText($policyFile,"{`n}`n")
$normalized=Get-NormalizedFileHash $policyFile
[IO.File]::WriteAllText($policyFile,"{`r`n}`r`n")
Assert-Cd ((Get-NormalizedFileHash $policyFile) -eq $normalized) 'Recovery policy depends on checkout line endings'
Write-Host 'PASS recovery policy hash survives CRLF/LF controller checkout'
$passed++
$timing=@{downtimeStarted='';downtimeEnded=''}
$start=[DateTimeOffset]::Parse('2026-09-21T00:00:00Z')
Start-MaintenanceWindow $timing $start
Start-MaintenanceWindow $timing $start.AddSeconds(10)
Assert-Cd ($timing.maintenanceWindows.Count -eq 1) 'Repeated maintenance created overlapping windows'
Complete-MaintenanceWindow $timing $start.AddSeconds(60)
Start-MaintenanceWindow $timing $start.AddSeconds(180)
Complete-MaintenanceWindow $timing $start.AddSeconds(240)
Assert-Cd ((Get-MaintenanceSummary $timing) -ceq '120.0 s across 2 window(s)') 'Application uptime was counted as downtime'
$serialized=$timing | ConvertTo-Json -Depth 10 | ConvertFrom-Json
$restoredTiming=@{}
foreach($property in $serialized.PSObject.Properties) { $restoredTiming[$property.Name]=$property.Value }
Start-MaintenanceWindow $restoredTiming $start.AddSeconds(300)
Complete-MaintenanceWindow $restoredTiming $start.AddSeconds(330)
Assert-Cd ((Get-MaintenanceSummary $restoredTiming) -ceq '150.0 s across 3 window(s)') 'Serialized maintenance state cannot resume'
Write-Host 'PASS separate maintenance windows exclude service uptime between recoveries'
$passed++
$legacy=@{downtimeStarted=$start.ToString('o');downtimeEnded=$start.AddSeconds(60).ToString('o')}
Assert-Cd ((Get-MaintenanceSummary $legacy).Contains('legacy interval')) 'Legacy timing presented as precise downtime'
Start-MaintenanceWindow $legacy $start.AddSeconds(180)
Assert-Cd ((Get-MaintenanceSummary $legacy $start.AddSeconds(210)) -ceq '90.0 s across 2 window(s) (maintenance not confirmed ended)') 'Legacy pending timing not preserved'
Write-Host 'PASS legacy operation timing and ongoing recovery are reported explicitly'
$passed++
. (Join-Path $PSScriptRoot 'release-runtime.ps1')
$retained=[Collections.Generic.List[object]]::new()
function Invoke-Native { param([string]$Exe,[string[]]$Arguments) $retained.Add($Arguments) }
$script:docker='docker.exe'
$imageIds=@{api=('sha256:'+('a'*64));worker=('sha256:'+('b'*64))}
Protect-ReleaseImages $imageIds ('c'*40)
Assert-Cd ($retained.Count -eq 2) 'Both CPU and GPU images must be retained'
Assert-Cd (($retained[0] -join ' ') -ceq ('image tag '+$imageIds.api+' stroykontur:retained-cpu-'+('c'*40)+'-'+('a'*64))) 'CPU retention tag does not contain its full immutable ID'
Assert-Cd (($retained[1] -join ' ') -ceq ('image tag '+$imageIds.worker+' stroykontur:retained-gpu-'+('c'*40)+'-'+('b'*64))) 'GPU retention tag does not contain its full immutable ID'
Write-Host 'PASS same-SHA rebuild cannot replace content-specific retention tags'
$passed++
$caddyMount=@{Type='bind';Source='C:\StroyKontur\deployment\runtime\Caddyfile';Destination='/etc/caddy/Caddyfile';RW=$false}
Assert-CaddyConfigMount @($caddyMount) 'C:/StroyKontur/deployment/runtime'
foreach($bad in @(
    @{Type='bind';Source='Z:/PycharmProjects/hackaton/Caddyfile';Destination='/etc/caddy/Caddyfile';RW=$false},
    @{Type='bind';Source=$caddyMount.Source;Destination='/etc/caddy/Caddyfile';RW=$true},
    @{Type='volume';Source=$caddyMount.Source;Destination='/etc/caddy/Caddyfile';RW=$false}
)) {
    $caught=$false
    try { Assert-CaddyConfigMount @($bad) 'C:/StroyKontur/deployment/runtime' } catch { $caught=$true }
    Assert-Cd $caught 'Caddy checkout/writable/non-bind mount accepted'
}
$controller=Get-Content -Raw (Join-Path $PSScriptRoot 'deploy-migrations.ps1')
Assert-Cd ($controller.IndexOf('Assert-CaddyConfigMount') -lt $controller.IndexOf('$OperationId=[Guid]')) 'Persistent Caddy mount must be checked before creating the operation'
Write-Host 'PASS persistent Caddy mount is checked before release mutation'
$passed++
Write-Host "$passed migration policy checks passed"
