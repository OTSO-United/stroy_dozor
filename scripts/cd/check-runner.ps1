param([string]$GpuImage='stroykontur:gpu')
$ErrorActionPreference='Stop'
Write-Host ('Account: ' + [Security.Principal.WindowsIdentity]::GetCurrent().Name)
Write-Host ('PowerShell: ' + $PSVersionTable.PSVersion)
foreach ($command in @('git.exe','docker.exe')) {
    Write-Host (Get-Command $command -ErrorAction Stop).Source
}
& git --version
if ($LASTEXITCODE) { throw 'Git unavailable' }
& docker version --format '{{.Server.Version}} {{.Server.Os}}'
if ($LASTEXITCODE) { throw 'Docker unavailable to this account' }
& docker compose version
if ($LASTEXITCODE) { throw 'Compose unavailable' }
& docker run --rm --network none --gpus all -e REQUIRE_CUDA=1 $GpuImage python -m app.check_model --require-cuda
if ($LASTEXITCODE) { throw 'GPU model inference failed' }
