# Install / remove Windows Scheduled Task for quota_watch.py
# Run in elevated or normal PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_quota_watch_task.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_quota_watch_task.ps1 -Remove

param(
    [switch]$Remove,
    [string]$TaskName = "GrokQuotaWatch",
    [string]$Python = "",
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"

if (-not $Repo) {
    $Repo = Split-Path -Parent $PSScriptRoot
    if (-not (Test-Path (Join-Path $Repo "quota_watch.py"))) {
        $Repo = "D:\Users\grok-auto-register"
    }
}

if (-not $Python) {
    $candidates = @(
        "C:\Users\zhugu\scoop\apps\python313\current\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "python"
    )
    foreach ($c in $candidates) {
        if ($c -eq "python") {
            $Python = "python"
            break
        }
        if (Test-Path $c) {
            $Python = $c
            break
        }
    }
}

$script = Join-Path $Repo "quota_watch.py"
if (-not (Test-Path $script)) {
    throw "quota_watch.py not found: $script"
}

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

$logDir = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "quota_watch.out.log"
$stderr = Join-Path $logDir "quota_watch.err.log"

# At logon for current user; restart on failure.
$arg = "`"$script`""
$action = New-ScheduledTaskAction -Execute $Python -Argument $arg -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
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
    -Description "Grok CLI quota watch: rotate CPA auth.json / register when official quota exhausted" `
    -Force | Out-Null

# Start immediately once installed
Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Write-Host "Installed scheduled task: $TaskName"
Write-Host "  Python : $Python"
Write-Host "  Script : $script"
Write-Host "  Repo   : $Repo"
Write-Host "  Trigger: At logon ($env:USERNAME)"
Write-Host "  Start  : Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Status : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  Remove : powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Remove"
