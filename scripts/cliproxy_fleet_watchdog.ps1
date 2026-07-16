#Requires -Version 5.1
<#
.SYNOPSIS
  Keep Grok/Codex/Claude CLIProxy instances alive (port + config aware).

  Root cause of Codex :8327 "randomly dies":
    Started under agent shell / non-hidden console → process ends when session ends.
    Old cliproxy_mem_watchdog only tracks ONE cli-proxy-api.exe and kills by name,
    which can take down sibling instances.

  This watchdog:
    - Tracks three configs by CommandLine match
    - Health-checks HTTP ports independently
    - Restarts only the dead instance via hidden VBS
    - Optional RSS restart per-instance (does not kill siblings)

  Install (logon + keep alive):
    powershell -ExecutionPolicy Bypass -File scripts/cliproxy_fleet_watchdog.ps1 -Install

  One-shot ensure all up:
    powershell -ExecutionPolicy Bypass -File scripts/cliproxy_fleet_watchdog.ps1 -Once
#>

param(
    [string]$Root = "D:\cli-proxy-api",
    [int]$CheckIntervalSec = 30,
    [int]$MaxRSSMB = 800,
    [switch]$Install,
    [switch]$Once,
    [switch]$Status
)

$ErrorActionPreference = "SilentlyContinue"
$LogDir = Join-Path $Root "logs"
$LogFile = Join-Path $LogDir "fleet_watchdog.log"
$Vbs = Join-Path $Root "_start_instance_hidden.vbs"
$Exe = Join-Path $Root "cli-proxy-api.exe"

$Fleet = @(
    @{
        Name   = "grok"
        Config = "config.yaml"
        Port   = 8317
        Url    = "http://127.0.0.1:8317/v1/models"
        Header = @{ Authorization = "Bearer sk-local-grok-pool-2026" }
    },
    @{
        Name   = "codex"
        Config = "config-codex.yaml"
        Port   = 8327
        Url    = "http://127.0.0.1:8327/v1/models"
        Header = @{ Authorization = "Bearer sk-local-codex-unified-2026" }
    },
    @{
        Name   = "claude"
        Config = "config-claude.yaml"
        Port   = 8337
        Url    = "http://127.0.0.1:8337/v1/models"
        Header = @{
            Authorization = "Bearer sk-local-claude-unified-2026"
            "x-api-key"   = "sk-local-claude-unified-2026"
        }
    },
    @{
        Name   = "glm"
        Config = "config-glm.yaml"
        Port   = 8347
        Url    = "http://127.0.0.1:8347/v1/models"
        Header = @{ Authorization = "Bearer sk-local-glm-unified-2026" }
    }
)

function Write-Log([string]$msg) {
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-InstanceProcess([string]$configName) {
    $needle = $configName.ToLowerInvariant()
    Get-CimInstance Win32_Process -Filter "Name='cli-proxy-api.exe'" | Where-Object {
        $cl = ($_.CommandLine + "").ToLowerInvariant()
        if (-not $cl) { return $false }
        # config.yaml is a suffix of nothing, but still exclude codex/claude configs
        if ($needle -eq "config.yaml") {
            # Bare config.yaml must not match config-codex/claude/glm siblings.
            return ($cl.Contains("config.yaml") -and
                    -not $cl.Contains("config-codex") -and
                    -not $cl.Contains("config-claude") -and
                    -not $cl.Contains("config-glm"))
        }
        return $cl.Contains($needle)
    } | Select-Object -First 1
}

function Test-Health($item) {
    try {
        $resp = Invoke-WebRequest -Uri $item.Url -Headers $item.Header -TimeoutSec 8 -UseBasicParsing
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Start-Instance([string]$configName) {
    $cfgPath = Join-Path $Root $configName
    if (-not (Test-Path $Exe)) {
        Write-Log "ERROR missing $Exe"
        return
    }
    if (-not (Test-Path $cfgPath)) {
        Write-Log "ERROR missing config $configName"
        return
    }
    # Prefer direct Start-Process (reliable); VBS is optional fallback
    $env:GOMEMLIMIT = "512MiB"
    $env:GOGC = "50"
    Start-Process -FilePath $Exe `
        -ArgumentList @("-config", $cfgPath) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

function Stop-InstanceProcess($proc, [string]$name) {
    if (-not $proc) { return }
    Write-Log "Stopping $name pid=$($proc.ProcessId)"
    Stop-Process -Id $proc.ProcessId -Force
    Start-Sleep -Seconds 2
}

function Ensure-Instance($item) {
    $cfg = $item.Config
    $name = $item.Name
    $proc = Get-InstanceProcess $cfg
    $healthy = Test-Health $item

    if ($healthy) {
        $rss = if ($proc) { [math]::Round($proc.WorkingSetSize / 1MB) } else { 0 }
        if ($proc -and $rss -gt $MaxRSSMB) {
            Write-Log "$name healthy but RSS ${rss}MB > $MaxRSSMB — recycle this instance only"
            Stop-InstanceProcess $proc $name
            Start-Instance $cfg
            Start-Sleep -Seconds 5
            $ok = Test-Health $item
            Write-Log "$name recycle result healthy=$ok"
            return $ok
        }
        return $true
    }

    # not healthy
    if ($proc) {
        Write-Log "$name unhealthy (port $($item.Port)) — kill pid=$($proc.ProcessId) and restart"
        Stop-InstanceProcess $proc $name
    } else {
        Write-Log "$name not running (port $($item.Port)) — start"
    }
    Start-Instance $cfg
    Start-Sleep -Seconds 5
    $ok = Test-Health $item
    Write-Log "$name start result healthy=$ok"
    return $ok
}

function Show-Status {
    foreach ($item in $Fleet) {
        $proc = Get-InstanceProcess $item.Config
        $h = Test-Health $item
        $procId = if ($proc) { $proc.ProcessId } else { "-" }
        $rss = if ($proc) { [math]::Round($proc.WorkingSetSize / 1MB) } else { 0 }
        Write-Host ("{0,-7} port={1} healthy={2} pid={3} rss={4}MB config={5}" -f `
            $item.Name, $item.Port, $h, $procId, $rss, $item.Config)
    }
}

if ($Install) {
    $taskName = "CLIProxyFleetWatchdog"
    $ps1 = $MyInvocation.MyCommand.Path
    $installed = $false
    try {
        $action = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ps1`""
        $triggerLogon = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit (New-TimeSpan -Days 365)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggerLogon `
            -Settings $settings -Force -ErrorAction Stop | Out-Null
        Write-Host "Scheduled task '$taskName' registered (AtLogOn, runs watchdog loop)."
        $installed = $true
    } catch {
        # Non-admin fallback: current-user Startup folder (no elevation needed)
        Write-Host "Register-ScheduledTask denied ($_); falling back to Startup folder"
        $startup = [Environment]::GetFolderPath("Startup")
        $cmdPath = Join-Path $startup "CLIProxyFleetWatchdog.cmd"
        $cmd = "@echo off`r`nstart `"`" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ps1`"`r`n"
        Set-Content -Path $cmdPath -Value $cmd -Encoding ASCII
        Write-Host "Installed Startup entry: $cmdPath"
        $installed = $true
    }
    # also ensure once now
    foreach ($item in $Fleet) { [void](Ensure-Instance $item) }
    Show-Status
    return
}

if ($Status) {
    Show-Status
    return
}

if ($Once) {
    Write-Log "fleet ensure once"
    foreach ($item in $Fleet) { [void](Ensure-Instance $item) }
    Show-Status
    return
}

Write-Log "fleet watchdog loop interval=${CheckIntervalSec}s maxRSS=${MaxRSSMB}MB"
foreach ($item in $Fleet) { [void](Ensure-Instance $item) }

while ($true) {
    Start-Sleep -Seconds $CheckIntervalSec
    foreach ($item in $Fleet) {
        try {
            [void](Ensure-Instance $item)
        } catch {
            Write-Log "ERROR $($item.Name): $_"
        }
    }
}
