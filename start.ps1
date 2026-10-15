# Starts the engine (API, :3007) in the background and the web UI (:3006) in the foreground.
# Ctrl+C stops both.
param(
    [int]$EnginePort = 3007,
    [int]$WebPort = 3006,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if (-not (Test-Path (Join-Path $root 'web\node_modules'))) {
    Write-Host 'Installing web dependencies...'
    Push-Location (Join-Path $root 'web')
    try { npm install } finally { Pop-Location }
}

Write-Host "Starting engine on :$EnginePort"
$engine = Start-Process -FilePath 'uv' -ArgumentList 'run', 'astrodash-engine', '--port', $EnginePort `
    -WorkingDirectory (Join-Path $root 'engine') -NoNewWindow -PassThru

try {
    if (-not $NoBrowser) {
        # Open the browser once the web server answers; give up quietly after a minute.
        Start-Job -ArgumentList $WebPort -ScriptBlock {
            param($port)
            $url = "http://localhost:$port"
            for ($i = 0; $i -lt 60; $i++) {
                try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process $url; return }
                catch { if ($_.Exception.Response) { Start-Process $url; return } }
                Start-Sleep -Seconds 1
            }
        } | Out-Null
    }

    Write-Host "Starting web UI on :$WebPort"
    Push-Location (Join-Path $root 'web')
    try { npm run dev -- --port $WebPort } finally { Pop-Location }
}
finally {
    Write-Host 'Stopping engine...'
    if (-not $engine.HasExited) { taskkill /T /F /PID $engine.Id | Out-Null }
    Get-Job | Remove-Job -Force
}
