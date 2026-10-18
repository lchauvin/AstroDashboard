# Registers (or replaces) the Windows scheduled task that checks tonight's sky at the given times
# and sends the phone notification when it is due (notify-tonight.ps1). Runs as you, only while
# you are logged on. Remove it with:  Unregister-ScheduledTask -TaskName 'AstroDashboard night notification'
param(
    [string[]]$At = @('15:00', '19:30'),
    [int]$WebPort = 3006
)

$ErrorActionPreference = 'Stop'
$name = 'AstroDashboard night notification'
$script = Join-Path $PSScriptRoot 'notify-tonight.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -WebPort $WebPort"
$triggers = $At | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
# A check missed while the PC was asleep or off runs as soon as it is back.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings `
    -Description 'Sends tonight''s AstroDashboard Planning verdict to your phone through ntfy (Settings → Notifications).' -Force | Out-Null
Write-Output "Scheduled '$name' daily at $($At -join ', ')."
