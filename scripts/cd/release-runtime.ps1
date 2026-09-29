function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [int]$Timeout = 900)
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Exe
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    # Windows CommandLineToArgvW quoting, including paths ending in backslashes.
    $info.Arguments = ($Arguments | ForEach-Object { '"' + (($_ -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"' }) -join ' '
    $process = [Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    try {
        if (!$process.WaitForExit($Timeout * 1000)) {
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
            throw "$Exe timed out"
        }
        if ($process.ExitCode -ne 0) { throw "$Exe failed (exit $($process.ExitCode)); command output withheld to protect runtime configuration" }
        return $stdout.GetAwaiter().GetResult().Trim()
    } finally { $process.Dispose() }
}

function Invoke-Compose {
    param([string[]]$Arguments)
    Invoke-Native $script:docker ($script:compose + $Arguments)
}

function Test-PackagedImage {
    param([string]$Image, [string]$ModelSha, [switch]$Cuda)
    $arguments = @('run','--rm','--network','none')
    if ($Cuda) { $arguments += @('--gpus','all','-e','REQUIRE_CUDA=1') }
    $probe = if ($Cuda) { '--require-cuda' } else { '--cpu' }
    $result = Invoke-Native $script:docker ($arguments + @($Image,'python','-m','app.check_model',$probe)) | ConvertFrom-Json
    if ($result.status -ne 'ok' -or $result.model.sha256 -cne $ModelSha -or
        ($Cuda -and 'CUDAExecutionProvider' -notin $result.model.providers)) { throw 'Image model/provider differs from approved package' }
}

function Assert-AbsoluteLocalPath {
    param([string]$Path)
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path.Contains('"')) { throw 'Use absolute local disk paths' }
    if ([IO.DriveInfo]::new([IO.Path]::GetPathRoot($Path)).DriveType -ne 'Fixed') { throw 'Mapped/network/removable deployment disks are unsupported' }
}

function Get-LiveRelease {
    param([switch]$Internal)
    $images = @{}
    foreach ($service in @('api','worker','monitor','db','caddy')) {
        $id = Invoke-Compose @('ps','-q',$service)
        if (!$id -or $id.Contains("`n")) { throw "Expected exactly one running $service" }
        $container = @(Invoke-Native $script:docker @('inspect',$id) | ConvertFrom-Json)[0]
        if (!$container.State.Running -or $container.State.Restarting) { throw "$service is not running" }
        if ($service -in @('api','db') -and $container.State.Health.Status -ne 'healthy') { throw "$service is unhealthy" }
        if ($service -eq 'worker' -and ('REQUIRE_CUDA=1' -notin $container.Config.Env -or !$container.HostConfig.DeviceRequests)) {
            throw 'Running worker must require CUDA and have a GPU reservation'
        }
        if ($service -in @('api','monitor') -and $container.HostConfig.DeviceRequests) { throw 'API and monitor must remain on CPU' }
        if ($service -in @('api','worker','monitor')) { $images[$service] = $container.Image }
    }
    $health = if ($Internal) { (Get-InternalProbe).health } else { Invoke-RestMethod ($cfg.HealthUrl + '/api/v1/health') -TimeoutSec 15 }
    if ($health.status -ne 'ok' -or !$health.model.ready) { throw 'Production model is not ready' }
    return @{ images=$images; modelSha=$health.model.sha256; sha='baseline'; runNumber=0 }
}

function Test-Release {
    param($Release, [switch]$Internal)
    $live = Get-LiveRelease -Internal:$Internal
    if ($live.modelSha -cne $Release.modelSha) { throw 'Live model SHA mismatch' }
    foreach ($service in @('api','worker','monitor')) {
        if ($live.images[$service] -cne $Release.images[$service]) { throw "Wrong image for $service" }
    }
    if ($Internal) {
        if (!(Get-InternalProbe).ui) { throw 'UI internal HTTP check failed' }
    } else {
        $http = Invoke-WebRequest $cfg.HealthUrl -UseBasicParsing -TimeoutSec 15
        if ($http.StatusCode -ne 200 -or $http.Content -notmatch '<html') { throw 'UI HTTP check failed' }
    }
    $probe = Invoke-Compose @('exec','-T','worker','python','-m','app.check_model','--require-cuda') | ConvertFrom-Json
    if ($probe.status -ne 'ok' -or $probe.model.sha256 -cne $Release.modelSha -or
        'CUDAExecutionProvider' -notin $probe.model.providers) { throw 'CUDA/model runtime verification failed' }
    # A second sample catches immediate exits/restart loops after the runtime probe.
    $after = Get-LiveRelease -Internal:$Internal
    foreach ($service in @('api','worker','monitor')) {
        if ($after.images[$service] -cne $Release.images[$service]) { throw 'Container changed during verification' }
    }
}

function Get-InternalProbe {
    Invoke-Compose @('exec','-T','api','python','/opt/stroy-cd/runtime-bootstrap.py','probe') | ConvertFrom-Json
}

function Get-RuntimeFingerprint {
    param([string]$Root)
    $fingerprint = ''
    foreach ($name in @('.env','compose.yaml','compose.gpu.yaml','Caddyfile')) {
        $fingerprint += (Get-FileHash -LiteralPath (Join-Path $Root $name)).Hash
    }
    foreach ($name in @('compose.cd.json','controller/runtime-bootstrap.py')) {
        $path = Join-Path $Root $name
        if (Test-Path -LiteralPath $path) { $fingerprint += (Get-FileHash -LiteralPath $path).Hash }
    }
    return $fingerprint
}

function Get-ComposeArguments {
    param($Config)
    $arguments = @('compose','--project-directory',$Config.RuntimeRoot,'--env-file',(Join-Path $Config.RuntimeRoot '.env'),'-p',$Config.ProjectName,
        '-f',(Join-Path $Config.RuntimeRoot 'compose.yaml'),'-f',(Join-Path $Config.RuntimeRoot 'compose.gpu.yaml'))
    $overlay = Join-Path $Config.RuntimeRoot 'compose.cd.json'
    if (Test-Path -LiteralPath $overlay) { $arguments += @('-f',$overlay) }
    return $arguments + @('--profile','public')
}

function New-ReleaseSource {
    param([string]$Directory, [string]$SourceRoot, [string]$Sha, [string]$ModelSha)
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $archive = Join-Path $Directory 'source.zip'
    Invoke-Native $git @('-C',$SourceRoot,'archive','--format=zip',('-o' + $archive),$Sha) | Out-Null
    $source = Join-Path $Directory 'source'
    Expand-Archive -LiteralPath $archive -DestinationPath $source
    Remove-Item -LiteralPath $archive
    $model = Join-Path $source 'models/yolo26m'
    New-Item -ItemType Directory -Path $model -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $cfg.ModelDirectory 'best.onnx'),(Join-Path $cfg.ModelDirectory 'manifest.json') -Destination $model
    if ((Assert-ModelPackage $model) -cne $ModelSha) { throw 'Copied model changed' }
    return $source
}

function Protect-ReleaseImages {
    param([hashtable]$Images, [string]$Sha)
    if ($Sha -cnotmatch '^[a-f0-9]{40}$') { throw 'Full source SHA required to retain images' }
    foreach($entry in @(@{role='cpu';image=$Images.api},@{role='gpu';image=$Images.worker})) {
        if ($entry.image -cnotmatch '^sha256:[a-f0-9]{64}$') { throw 'Resolved image ID required to retain images' }
        # A same-SHA rebuild may replace its tag in Docker Desktop's image store.
        $tag='stroykontur:retained-'+$entry.role+'-'+$Sha+'-'+$entry.image.Substring(7)
        Invoke-Native $script:docker @('image','tag',$entry.image,$tag) | Out-Null
    }
}

function Build-ReleaseImages {
    param([string]$Source, [string]$Sha, [string]$ModelSha)
    $cpu = "stroykontur:cpu-$Sha"
    $gpu = "stroykontur:gpu-$Sha"
    $buildArgs = @('compose','--project-directory',$Source,'--env-file',(Join-Path $cfg.RuntimeRoot '.env'),'-p',('build-' + $Sha),'-f',(Join-Path $Source 'compose.yaml'))
    $savedCpu = $env:CPU_IMAGE; $savedGpu = $env:GPU_IMAGE
    try {
        $env:CPU_IMAGE=$cpu; $env:GPU_IMAGE=$gpu
        Write-Host "Building CPU image for $Sha"
        Invoke-Native $script:docker ($buildArgs + @('build','api')) 5400 | Out-Null
        Write-Host "Building GPU image for $Sha"
        Invoke-Native $script:docker ($buildArgs + @('-f',(Join-Path $Source 'compose.gpu.yaml'),'build','worker')) 5400 | Out-Null
    } finally { $env:CPU_IMAGE=$savedCpu; $env:GPU_IMAGE=$savedGpu }
    $cpuId = @(Invoke-Native $script:docker @('image','inspect',$cpu) | ConvertFrom-Json)[0].Id
    $gpuId = @(Invoke-Native $script:docker @('image','inspect',$gpu) | ConvertFrom-Json)[0].Id
    $images=@{api=$cpuId;monitor=$cpuId;worker=$gpuId}
    Protect-ReleaseImages $images $Sha
    Test-PackagedImage $cpuId $ModelSha
    Test-PackagedImage $gpuId $ModelSha -Cuda
    return $images
}

function Set-LiveRelease {
    param($Release)
    $overlay = @{ services=@{} }
    foreach ($service in @('api','worker','monitor')) { $overlay.services[$service] = @{ image=$Release.images[$service] } }
    $overlayPath = Join-Path $cfg.StateRoot 'active-images.json'
    Write-ReleaseState $overlayPath $overlay
    $started = [DateTime]::UtcNow
    Write-Host "Recreation starts at $($started.ToString('o')); brief downtime is expected."
    Invoke-Native $script:docker ($script:compose + @('-f',$overlayPath,'up','-d','--no-deps','--no-build','--pull','never','--wait','--wait-timeout','240','api','worker','monitor')) | Out-Null
    Write-Host "Compose ready after $([int]([DateTime]::UtcNow - $started).TotalSeconds)s; runtime verification follows."
}
