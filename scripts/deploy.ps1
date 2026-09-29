param(
    [switch]$Gpu,
    [switch]$Public,
    [switch]$CheckOnly,
    [string]$ProjectName = "stroykontur"
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $manifestPath = Join-Path $root 'models/yolo26m/manifest.json'
    $weightPath = Join-Path $root 'models/yolo26m/best.onnx'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.weights -ne 'best.onnx') { throw 'Packaged manifest must use best.onnx' }
    $hash = (Get-FileHash -LiteralPath $weightPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $manifest.sha256) { throw 'Model SHA256 mismatch' }
    Write-Host "Verified $($manifest.name): $hash"

    function Invoke-Docker {
        param([string[]]$DockerArgs)
        & docker @DockerArgs
        if ($LASTEXITCODE -ne 0) { throw "Docker failed (exit $LASTEXITCODE): $($DockerArgs -join ' ')" }
    }

    $baseArgs = @('compose', '-p', $ProjectName, '-f', 'compose.yaml')
    $composeArgs = $baseArgs
    if ($Gpu) { $composeArgs += @('-f', 'compose.gpu.yaml') }
    if ($Public) { $composeArgs += @('--profile', 'public') }
    Invoke-Docker -DockerArgs ($composeArgs + @('config', '--quiet'))
    if ($CheckOnly) { return }
    Invoke-Docker -DockerArgs @('info', '--format', '{{.ServerVersion}}')
    # The GPU Dockerfile derives from the CPU image, so build it first.
    Invoke-Docker -DockerArgs ($baseArgs + @('build', 'api'))
    if ($Gpu) { Invoke-Docker -DockerArgs ($composeArgs + @('build', 'worker')) }
    Invoke-Docker -DockerArgs ($composeArgs + @('up', '-d', '--no-build', '--wait', '--wait-timeout', '180'))
    $probe = if ($Gpu) { '--require-cuda' } else { '--cpu' }
    Invoke-Docker -DockerArgs ($composeArgs + @('exec', '-T', 'worker', 'python', '-m', 'app.check_model', $probe))
    Invoke-Docker -DockerArgs ($composeArgs + @('ps'))
    Write-Host 'Deployment ready. Local URL uses APP_PORT (8080 by default).'
} finally {
    Pop-Location
}
