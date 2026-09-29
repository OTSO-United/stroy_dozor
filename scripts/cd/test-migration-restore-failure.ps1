param([Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$TestId,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$OperationId)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'migration-policy.ps1')
. (Join-Path $PSScriptRoot 'release-runtime.ps1')
$root='C:/StroyKontur/deployment/verification/migration-'+$TestId+'/rehearsal-only/'+$OperationId
$op=Get-Content (Join-Path $root 'operation.json') -Raw | ConvertFrom-Json
$script:docker=(Get-Command docker.exe).Source
$volume=@(Invoke-Native $script:docker @('volume','inspect',$op.rehearsalVolume) | ConvertFrom-Json)[0]
if (!$op.rehearsalVerified -or $volume.Name -cnotmatch '^stroykontur-migration-' -or $volume.Labels.'stroykontur.cd.operation' -cne $OperationId) { throw 'Only a completed disposable rehearsal volume is permitted' }
$name='stroykontur-migration-negative-'+$OperationId
$image=@(Invoke-Native $script:docker @('image','inspect','postgres:16-alpine') | ConvertFrom-Json)[0].Id
try {
    Invoke-Native $script:docker @('run','-d','--name',$name,'--network','none','--env-file',(Join-Path $root 'rehearsal-db.env'),
        '--mount',('type=volume,source='+$volume.Name+',target=/var/lib/postgresql/data'),$image) | Out-Null
    $ready=$false
    for($i=0;$i -lt 30;$i++) {
        try { Invoke-Native $script:docker @('exec',$name,'pg_isready','-U','stroy','-d','stroy') | Out-Null; $ready=$true; break } catch { Start-Sleep -Seconds 1 }
    }
    if (!$ready) { throw 'Disposable DB did not start' }
    $before=Invoke-Native $script:docker @('exec',$name,'psql','-X','-U','stroy','-d','stroy','-Atc','SELECT version_num FROM alembic_version')
    if ($before -cne '004') { throw 'Expected committed 004 in disposable volume' }
    Invoke-Native $script:docker @('cp',(Join-Path $root 'before.dump'),"${name}:/tmp/duplicate.dump") | Out-Null
    $failed=$false
    try { Invoke-Native $script:docker @('exec',$name,'pg_restore','--exit-on-error','--single-transaction','--no-owner','--no-privileges','-U','stroy','-d','stroy','/tmp/duplicate.dump') | Out-Null }
    catch { $failed=$true }
    if (!$failed) { throw 'Restore into occupied schema unexpectedly succeeded' }
    $after=Invoke-Native $script:docker @('exec',$name,'psql','-X','-U','stroy','-d','stroy','-Atc','SELECT version_num FROM alembic_version')
    if ($after -cne '004') { throw 'Failed restore changed committed revision' }
    if ((Get-MigrationFailureAction $after $true $false $true) -cne 'compatible-recovery-only') { throw 'Committed migration incorrectly classified as base rollback' }
    Write-Host 'PASS real pg_restore failure is fatal, transaction preserves 004, post-commit policy forbids automatic base rollback'
} finally {
    try { Invoke-Native $script:docker @('rm','-f',$name) | Out-Null } catch { }
}
