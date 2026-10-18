# Asks the dashboard whether tonight is worth a phone notification and sends it if due
# (Settings → Notifications). Run by the scheduled task from install-notify-task.ps1.
# The engine and the web UI have to be running; every run is logged in var\notify.log.
param([int]$WebPort = 3006)

$log = Join-Path $PSScriptRoot '..\var\notify.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
try {
    $r = Invoke-RestMethod -Method Post -Uri "http://localhost:$WebPort/api/notify/tonight" -TimeoutSec 180
    $line = "$stamp  $($r.date) $($r.label): $($r.reason)$(if ($r.sent) { ' [sent]' })"
}
catch {
    $detail = $_.ErrorDetails.Message
    try { $detail = ($detail | ConvertFrom-Json).error } catch { }
    if (-not $detail) { $detail = $_.Exception.Message }
    $line = "$stamp  failed: $detail"
}
Add-Content -Path $log -Value $line -Encoding utf8
Write-Output $line
