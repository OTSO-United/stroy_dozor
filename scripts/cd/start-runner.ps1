param([Parameter(Mandatory)][string]$RunnerRoot)
$ErrorActionPreference='Stop'
if ($RunnerRoot -notmatch '^[A-Za-z]:[\\/]' -or !(Test-Path -LiteralPath (Join-Path $RunnerRoot '.runner'))) { throw 'Registered runner on an absolute local path required' }
if ([IO.DriveInfo]::new([IO.Path]::GetPathRoot($RunnerRoot)).DriveType -ne 'Fixed') { throw 'Runner requires fixed local disk' }
$env:PATH = 'C:\Program Files\Docker\Docker\resources\bin;C:\Program Files\Git\cmd;' + $env:PATH
Push-Location $RunnerRoot
try {
    if (!(Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath 'C:\Program Files\Docker\Docker\Docker Desktop.exe' -WindowStyle Hidden
    }
    # Listener stays online during Docker outages; deployment has a bounded Engine retry.
    & (Join-Path $RunnerRoot 'run.cmd')
    if ($LASTEXITCODE -ne 0) { throw "Runner stopped: $LASTEXITCODE" }
} finally { Pop-Location }
