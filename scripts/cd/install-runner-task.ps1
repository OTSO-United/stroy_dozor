param([Parameter(Mandatory)][string]$RunnerRoot)
$ErrorActionPreference='Stop'
$account=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$scriptPath=Join-Path $PSScriptRoot 'start-runner.ps1'
foreach ($path in @($RunnerRoot,$scriptPath)) {
    if ($path -notmatch '^[A-Za-z]:[\\/]' -or $path.Contains('"') -or !(Test-Path -LiteralPath $path)) { throw 'Existing absolute paths required' }
}
if (!(Test-Path -LiteralPath (Join-Path $RunnerRoot '.runner'))) { throw 'Register the runner in the private deployment repository first' }
$action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $scriptPath + '" -RunnerRoot "' + $RunnerRoot + '"') -WorkingDirectory $RunnerRoot
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $account
$principal=New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName 'StroyKontur-DeploymentRunner' -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Write-Host "Installed logon task for $account. Start with Start-ScheduledTask StroyKontur-DeploymentRunner."
