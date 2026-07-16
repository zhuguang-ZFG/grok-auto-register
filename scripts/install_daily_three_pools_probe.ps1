#Requires -Version 5.1
$ErrorActionPreference = "Continue"
$root = "D:\Users\grok-auto-register"
$runner = Join-Path $root "scripts\run_daily_pool_health.cmd"

if (-not (Test-Path $runner)) {
    Write-Host "missing $runner"
    exit 1
}

$taskName = "GrokThreePoolsDailyProbe"
$time = "09:17"
# Current-user daily task; LIMITED so no admin elevation required when possible
$tr = "`"$runner`""
$create = schtasks /Create /TN $taskName /TR $tr /SC DAILY /ST $time /F /RL LIMITED 2>&1
Write-Host $create

# Verify
$query = schtasks /Query /TN $taskName /FO LIST 2>&1
Write-Host $query

Write-Host "Runner: $runner"
Write-Host "Docs: docs\DAILY_POOL_HEALTH.md"
# smoke once
& cmd /c $runner
Write-Host "Smoke run finished (see logs\daily_pool_health.log)"
