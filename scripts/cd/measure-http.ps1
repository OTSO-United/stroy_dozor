param(
    [Parameter(Mandatory)][string]$OutputPath,
    [Parameter(Mandatory)][string]$StopFile,
    [ValidatePattern('^http://127\.0\.0\.1:[0-9]{1,5}/$')][string]$Url='http://127.0.0.1:18080/',
    [ValidateRange(1,180)][int]$DurationMinutes=20
)
$ErrorActionPreference='Stop'
$samples=[Collections.Generic.List[object]]::new()
$deadline=[DateTime]::UtcNow.AddMinutes($DurationMinutes)
while (!(Test-Path -LiteralPath $StopFile) -and [DateTime]::UtcNow -lt $deadline) {
    $time=[DateTime]::UtcNow
    $ok=$false
    try {
        $request=[Net.HttpWebRequest]::Create($Url)
        $request.Timeout=1000
        $response=$request.GetResponse()
        $ok=([int]$response.StatusCode -eq 200)
        $response.Close()
    } catch { }
    $samples.Add(@{time=$time.ToString('o');ok=$ok})
    Start-Sleep -Milliseconds 250
}
[IO.File]::WriteAllText($OutputPath,($samples | ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
