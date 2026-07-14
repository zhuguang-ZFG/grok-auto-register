#!/usr/bin/env powershell
#Requires -Version 5.1
<#
.SYNOPSIS
  Start cpa-auth-inspect service (FastAPI on :18318)
  Single-instance: exits if already running.
#>

$ErrorActionPreference = "SilentlyContinue"
$ProjectDir = "D:\Users\grok-auto-register"
$Python = Join-Path $ProjectDir ".venv-inspect\Scripts\python.exe"
$App = Join-Path $ProjectDir "_tools\cpa-auth-inspect\app.py"
$LogDir = Join-Path $ProjectDir "logs"
$LogFile = Join-Path $LogDir "cpa_auth_inspect.log"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# Check if already running
$existing = Get-NetTCPConnection -LocalPort 18318 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    $proc = Get-Process -Id $existing.OwningProcess -ErrorAction SilentlyContinue
    $name = if ($proc) { $proc.ProcessName } else { "unknown" }
    Write-Host "cpa-auth-inspect already running on :18318 (pid=$($existing.OwningProcess), $name)"
    exit 0
}

if (-not (Test-Path $Python)) {
    Write-Host "ERROR: venv python not found at $Python"
    exit 1
}
if (-not (Test-Path $App)) {
    Write-Host "ERROR: app.py not found at $App"
    exit 1
}

$env:AUTH_DIR = "D:\Users\grok-auto-register\cpa_auths"
$env:HOST = "127.0.0.1"
$env:PORT = "18318"
$env:PROBE_CONCURRENCY = "8"
$env:PROBE_TIMEOUT = "12"
$env:PROBE_MODEL = "grok-3-mini"

Write-Host "Starting cpa-auth-inspect on http://127.0.0.1:18318 ..."
Write-Host "  AUTH_DIR = $($env:AUTH_DIR)"
Write-Host "  Log      = $LogFile"

$proc = Start-Process -FilePath $Python `
    -ArgumentList @($App) `
    -WorkingDirectory $ProjectDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError (Join-Path $LogDir "cpa_auth_inspect.err.log") `
    -PassThru

Start-Sleep -Seconds 4

$check = Get-NetTCPConnection -LocalPort 18318 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($check) {
    Write-Host "OK: cpa-auth-inspect started (pid=$($proc.Id), port=18318)"
    Write-Host "  Web UI:  http://127.0.0.1:18318/"
    Write-Host "  Health:  http://127.0.0.1:18318/healthz"
    Write-Host "  API:     http://127.0.0.1:18318/api/status"
} else {
    Write-Host "WARN: port 18318 not listening yet — check $LogFile"
}
