$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $taskRoot 'scripts/start-daily-worker.ps1'
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$scriptPath`""
$triggers = @((New-ScheduledTaskTrigger -Daily -At '15:00'), (New-ScheduledTaskTrigger -AtLogOn -User $taskUser))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'Clipper Daily Podcast Preparation' -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName 'Clipper Daily Podcast Preparation'
Get-ScheduledTask -TaskName 'Clipper Daily Podcast Preparation' | Select-Object TaskName,State
