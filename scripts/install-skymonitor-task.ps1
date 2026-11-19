# Registers (or replaces) the Windows scheduled task that keeps the sky monitor running: the ASCOM Alpaca safety
# monitor NINA needs to image (astrodash-skymonitor). It starts at logon and is started again every minute; a second
# copy finds the running one and leaves at once, so a supervisor that died is back within a minute. The engine also
# starts it when nothing answers on its port. Runs as you, only while you are logged on.
# Remove it with:  Unregister-ScheduledTask -TaskName 'AstroDashboard sky monitor'
$ErrorActionPreference = 'Stop'
$name = 'AstroDashboard sky monitor'
$engine = Join-Path (Split-Path $PSScriptRoot -Parent) 'engine'
$pythonw = Join-Path $engine '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "No $pythonw : run 'uv sync' in $engine first." }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument '-m astrodash.skymonitor' -WorkingDirectory $engine
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$everyMinute = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 1)
# No time limit, never stopped on idle or batteries; a start while it runs is ignored (it is already up).
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $name -Action $action -Trigger @($atLogon, $everyMinute) -Settings $settings `
    -Description 'Keeps the AstroDashboard sky monitor (ASCOM Alpaca safety monitor for NINA, port 3008) running.' -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output "Scheduled '$name': at logon and every minute (it stays running; extra starts leave at once)."
