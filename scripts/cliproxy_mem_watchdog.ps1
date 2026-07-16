#!/usr/bin/env powershell
#Requires -Version 5.1
<#
.SYNOPSIS
  CLIProxyAPI memory watchdog — proactive restart before OOM.

  Root cause (github.com/router-for-me/CLIProxyAPI issue #2215):
    Go runtime without GOMEMLIMIT allows heap to grow 2x before GC.
    io.ReadAll on response bodies + signatureCache sync.Map have no limits.
    Under sustained traffic, RSS grows ~1GB/hour → eventually OOM.

  This watchdog:
    1. Sets GOMEMLIMIT=512MB (forces GC at 256MB soft limit)
    2. Monitors RSS every 5 minutes
    3. Restarts CLIProxyAPI when RSS > 600MB OR every 6 hours (whichever first)
    4. Zero-downtime: kills old process, waits, starts new one

  Install as scheduled task:
    powershell -ExecutionPolicy Bypass -File scripts/cliproxy_mem_watchdog.ps1 -Install
#>

param(
    [string]$ExePath = "D:\cli-proxy-api\cli-proxy-api.exe",
    [string]$ConfigPath = "D:\cli-proxy-api\config.yaml",
    [int]$MaxRSSMB = 600,
    [int]$MaxUptimeHours = 6,
    [int]$CheckIntervalSec = 300,
    [int]$GoMemLimitMB = 512,
    [switch]$Install
)

# DEPRECATED 2026-07-17: single-instance killer (Get-ProxyProcess | Select-Object -First 1)
# will randomly murder codex/claude/glm siblings. Fleet RSS/uptime is owned by
# scripts/cliproxy_fleet_watchdog.ps1 (+ Startup CLIProxyFleetWatchdog.cmd).
# Scheduled task CLIProxyMemWatchdog may still exist (needs admin to Disable);
# this script is now a permanent no-op so a boot trigger cannot harm the fleet.
$LogDir = "D:\cli-proxy-api\logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir "mem_watchdog.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "[$ts] NO-OP: deprecated single-instance mem watchdog; use cliproxy_fleet_watchdog.ps1" -Encoding UTF8
Write-Host "CLIProxyMemWatchdog is deprecated (fleet-unsafe). Use scripts/cliproxy_fleet_watchdog.ps1 instead."
if ($Install) {
    Write-Host "Refusing -Install: would re-register a fleet-unsafe task."
}
return

function Write-Log([string]$msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-ProxyProcess {
    return Get-CimInstance Win32_Process -Filter "Name='cli-proxy-api.exe'" | Select-Object -First 1
}

function Get-RSS-MB($proc) {
    if (-not $proc) { return 0 }
    return [math]::Round($proc.WorkingSetSize / 1MB)
}

function Get-Uptime-Hours($proc) {
    if (-not $proc) { return 0 }
    return ((Get-Date) - $proc.CreationDate).TotalHours
}

function Restart-Proxy {
    Write-Log "Restarting CLIProxyAPI..."
    $old = Get-ProxyProcess
    if ($old) {
        Stop-Process -Id $old.ProcessId -Force
        Start-Sleep -Seconds 3
    }

    # Set GOMEMLIMIT before starting (forces Go GC at ~50% of limit)
    $env:GOMEMLIMIT = "${GoMemLimitMB}MiB"
    $env:GOGC = "50"  # More aggressive GC (default is 100)

    Start-Process -FilePath $ExePath -ArgumentList @("-config", $ConfigPath) -WindowStyle Hidden
    Start-Sleep -Seconds 8

    $new = Get-ProxyProcess
    if ($new) {
        $rss = Get-RSS-MB $new
        Write-Log "Restarted OK pid=$($new.ProcessId) rss=${rss}MB GOMEMLIMIT=${GoMemLimitMB}MB"
    } else {
        Write-Log "ERROR: CLIProxyAPI failed to start after restart!"
    }
}

# --- Install mode: create scheduled task ---
if ($Install) {
    $taskName = "CLIProxyMemWatchdog"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -RunLevel Highest -Force
    Write-Host "Scheduled task '$taskName' created (runs at startup)."
    return
}

# --- Main watchdog loop ---
Write-Log "mem watchdog start: maxRSS=${MaxRSSMB}MB maxUptime=${MaxUptimeHours}h GOMEMLIMIT=${GoMemLimitMB}MB interval=${CheckIntervalSec}s"

# Ensure proxy is running at startup
$proc = Get-ProxyProcess
if (-not $proc) {
    Write-Log "CLIProxyAPI not running, starting fresh..."
    Restart-Proxy
}

while ($true) {
    Start-Sleep -Seconds $CheckIntervalSec
    $proc = Get-ProxyProcess
    if (-not $proc) {
        Write-Log "CLIProxyAPI not running! Starting..."
        Restart-Proxy
        continue
    }

    $rss = Get-RSS-MB $proc
    $uptime = Get-Uptime-Hours $proc

    $needRestart = $false
    $reason = ""

    if ($rss -gt $MaxRSSMB) {
        $needRestart = $true
        $reason = "RSS ${rss}MB > ${MaxRSSMB}MB threshold"
    }

    if ($uptime -gt $MaxUptimeHours) {
        $needRestart = $true
        $reason = "uptime ${uptime}h > ${MaxUptimeHours}h limit"
    }

    if ($needRestart) {
        Write-Log "Restart triggered: $reason"
        Restart-Proxy
    } else {
        # Quiet: only log every 30 min (6 checks at 5-min interval)
        $modulo = [int](1800 / $CheckIntervalSec)
        $tick = [int](Get-Date -UFormat %s)
        if ($tick % $modulo -lt $CheckIntervalSec) {
            Write-Log "OK pid=$($proc.ProcessId) rss=${rss}MB uptime=$([math]::Round($uptime,1))h"
        }
    }
}
