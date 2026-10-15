@echo off
rem Double-click launcher for start.ps1 (engine + web UI).
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
