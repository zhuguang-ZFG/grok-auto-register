# Install / remove Windows Scheduled Task for K12 stack watchdog
# (gateway :8124 + k12_pool_monitor + k12_pool_ops, single-instance).
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_k12_stack_watchdog_task.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_k12_stack_watchdog_task.ps1 -Remove
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_k12_stack_watchdog_task.ps1 -StartNow

param(
    [switch]$Remove,
    [switch]$StartNow,
    [string]$TaskName = "K12StackWatchdog",
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"

if (-not $Repo) {
    $Repo = Split-Path -Parent $PSScriptRoot
    if (-not (Test-Path (Join-Path $Repo "scripts\k12_stack_watchdog.ps1"))) {
        $Repo = "D:\Users\grok-auto-register"
    }
}

$watchdog = Join-Path $Repo "scripts\k12_stack_watchdog.ps1"
if (-not (Test-Path $watchdog)) {
    throw "k12_stack_watchdog.ps1 not found: $watchdog"
}

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

$logDir = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Hidden PowerShell host; script has its own lock file (logs/k12_stack_watchdog.lock)
$ps = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchdog`""

$action = New-ScheduledTaskAction -Execute $ps -Argument $arg -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "K12 stack: chatgpt2api SQLite gateway + pool monitor/ops (community single-instance watchdog)" `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "  Watchdog : $watchdog"
Write-Host "  Repo     : $Repo"
Write-Host "  Trigger  : At logon ($env:USERNAME)"
Write-Host "  Instances: IgnoreNew (singleton at Task Scheduler layer too)"
Write-Host "  Start    : Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Status   : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  Remove   : powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Remove"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host "  Started  : yes (if another instance holds the lock, script exits 0)"
}
