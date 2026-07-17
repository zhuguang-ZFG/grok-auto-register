#Requires -Version 5.1
$ErrorActionPreference = "Continue"
$root = "D:\Users\grok-auto-register"
$runner = Join-Path $root "scripts\run_retest_quarantine.cmd"

if (-not (Test-Path $runner)) {
    Write-Host "missing $runner"
    exit 1
}

$taskName = "GrokQuarantineRetest"
$time = "03:17"
# Run every 6 hours starting at 03:17
$tr = "`"$runner`""
$create = schtasks /Create /TN $taskName /TR $tr /SC HOURLY /MO 6 /ST $time /F /RL LIMITED 2>&1
Write-Host $create

$query = schtasks /Query /TN $taskName /FO LIST 2>&1
Write-Host $query

Write-Host "Runner: $runner"
& cmd /c $runner
Write-Host "Smoke run finished (see logs\retest_quarantine.log)"
