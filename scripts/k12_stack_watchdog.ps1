#Requires -Version 5.1
<#
.SYNOPSIS
  Single-instance stack for local K12 chatgpt2api + pool monitors (community harden).

.DESCRIPTION
  Community pattern (chatgpt2api / chat2api pool ops):
    - one gateway process on :8124 (SQLite backend)
    - health = /health + /v1/models (not bare /accounts/check)
    - one k12_pool_monitor --watch
    - one k12_pool_ops watch --probe-n 0 (no direct-check spam on shared K12)
    - exponential backoff + circuit open

  Lock: logs/k12_stack_watchdog.lock (PID). Python watch scripts also have their own locks.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File scripts/k12_stack_watchdog.ps1
#>

param(
    [string]$ProjectDir = "D:\Users\grok-auto-register",
    [string]$GatewayUrl = "http://127.0.0.1:8124",
    [string]$AuthKey = "k12-pool-local",
    [int]$CheckInterval = 45,
    [int]$CircuitOpenAfter = 10
)

$ErrorActionPreference = "SilentlyContinue"
$LogDir = Join-Path $ProjectDir "logs"
$LogFile = Join-Path $LogDir "k12_stack_watchdog.log"
$LockFile = Join-Path $LogDir "k12_stack_watchdog.lock"
$GatewayDir = Join-Path $ProjectDir "chatgpt2api"
$DbUrl = "sqlite:///" + ((Join-Path $ProjectDir "chatgpt2api/data/accounts.db") -replace "\\", "/")
$Python = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $Python) { $Python = "python" }

function Write-Log([string]$msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Test-PidAlive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-PythonByPattern([string]$Pattern) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match 'python' -and $_.CommandLine -and ($_.CommandLine -match $Pattern)
        }
}

function Ensure-SingletonSelf {
    if (Test-Path $LockFile) {
        $old = 0
        try { $old = [int]((Get-Content $LockFile -Raw).Trim().Split()[0]) } catch { $old = 0 }
        if ($old -and $old -ne $PID -and (Test-PidAlive $old)) {
            Write-Log "stack watchdog already running pid=$old; exit"
            exit 0
        }
    }
    Set-Content -Path $LockFile -Value "$PID" -Encoding UTF8
}

function Test-PortOpen {
    try {
        $c = Get-NetTCPConnection -LocalPort 8124 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        return [bool]$c
    } catch {
        # fallback: TcpClient
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $iar = $tcp.BeginConnect("127.0.0.1", 8124, $null, $null)
            $ok = $iar.AsyncWaitHandle.WaitOne(800)
            if ($ok -and $tcp.Connected) { $tcp.Close(); return $true }
            $tcp.Close()
            return $false
        } catch { return $false }
    }
}

function Test-Gateway {
    # Prefer /healthz (no account scan). Fall back to port LISTEN if busy.
    # Full /health?format=json can block under load iterating ~80k accounts.
    try {
        $h = Invoke-WebRequest -Uri "$GatewayUrl/healthz" -TimeoutSec 5 -UseBasicParsing
        if ($h.StatusCode -eq 200) { return $true }
    } catch {}
    try {
        $h2 = Invoke-WebRequest -Uri "$GatewayUrl/health?format=json" -TimeoutSec 8 -UseBasicParsing
        if ($h2.StatusCode -eq 200) { return $true }
    } catch {}
    if (Test-PortOpen) {
        return $true  # port LISTEN = gateway alive, just busy; no log spam
    }
    return $false
}

function Start-Gateway {
    if (Test-PortOpen) {
        Write-Log "skip start: :8124 already LISTEN"
        return
    }
    # Prefer uv in chatgpt2api venv path via PATH; fall back to python -m uvicorn in .venv
    $env:STORAGE_BACKEND = "sqlite"
    $env:DATABASE_URL = $DbUrl
    $env:CHATGPT2API_AUTH_KEY = $AuthKey

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        $proc = Start-Process -FilePath $uv.Source `
            -ArgumentList @("run", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8124", "--log-level", "warning", "--timeout-keep-alive", "30", "--limit-concurrency", "10") `
            -WorkingDirectory $GatewayDir `
            -WindowStyle Hidden `
            -PassThru
        Write-Log "gateway start via uv pid=$($proc.Id)"
        return
    }

    $venvUvicorn = Join-Path $GatewayDir ".venv\Scripts\uvicorn.exe"
    if (Test-Path $venvUvicorn) {
        $proc = Start-Process -FilePath $venvUvicorn `
            -ArgumentList @("main:app", "--host", "127.0.0.1", "--port", "8124", "--log-level", "warning", "--timeout-keep-alive", "30", "--limit-concurrency", "10") `
            -WorkingDirectory $GatewayDir `
            -WindowStyle Hidden `
            -PassThru
        Write-Log "gateway start via venv uvicorn pid=$($proc.Id)"
        return
    }

    Write-Log "ERROR: uv/uvicorn not found; cannot start gateway"
}

function Ensure-OnePython([string]$Pattern, [string[]]$StartArgs, [string]$Name) {
    $procs = @(Get-PythonByPattern $Pattern)
    if ($procs.Count -gt 1) {
        # keep youngest (highest PID usually), kill rest
        $keep = $procs | Sort-Object ProcessId -Descending | Select-Object -First 1
        foreach ($p in $procs) {
            if ($p.ProcessId -ne $keep.ProcessId) {
                Write-Log "kill duplicate $Name pid=$($p.ProcessId)"
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        return
    }
    if ($procs.Count -eq 1) { return }

    # clear stale app lock so python can re-acquire
    if ($Name -eq "monitor") {
        Remove-Item (Join-Path $LogDir "k12_pool_monitor.watch.lock") -Force -ErrorAction SilentlyContinue
    }
    if ($Name -eq "ops") {
        Remove-Item (Join-Path $LogDir "k12_pool_ops.watch.lock") -Force -ErrorAction SilentlyContinue
    }

    $argLine = ($StartArgs -join " ")
    $proc = Start-Process -FilePath $Python `
        -ArgumentList $StartArgs `
        -WorkingDirectory $ProjectDir `
        -WindowStyle Hidden `
        -PassThru
    Write-Log "started $Name pid=$($proc.Id) args=$argLine"
}

Ensure-SingletonSelf
Write-Log "stack watchdog start project=$ProjectDir gateway=$GatewayUrl"

$failures = 0
try {
    while ($true) {
        $up = Test-Gateway
        if ($up) {
            if ($failures -gt 0) { Write-Log "gateway OK after $failures failure(s)" }
            $failures = 0
        } else {
            $failures++
            Write-Log "gateway DOWN failure=$failures"
            $backoff = [Math]::Min(30 * [Math]::Pow(2, [Math]::Max(0, $failures - 1)), 300)
            if ($failures -ge $CircuitOpenAfter) {
                Write-Log "CIRCUIT OPEN after $failures failures; exit 1"
                exit 1
            }
            Write-Log "restart gateway after ${backoff}s"
            Start-Sleep -Seconds $backoff
            Start-Gateway
            Start-Sleep -Seconds 10
            if (Test-Gateway) {
                Write-Log "gateway recovered"
                $failures = 0
            } else {
                Write-Log "gateway still down"
            }
        }

        # pool watchers: chat SSOT, probe-n 0 (shared K12 community harden)
        Ensure-OnePython -Pattern "k12_pool_monitor\.py" -Name "monitor" -StartArgs @(
            "scripts/k12_pool_monitor.py", "--watch", "--interval", "300"
        )
        Ensure-OnePython -Pattern "k12_pool_ops\.py\s+watch" -Name "ops" -StartArgs @(
            "scripts/k12_pool_ops.py", "watch", "--interval", "300", "--probe-n", "0", "--auto-purge-abnormal"
        )

        Start-Sleep -Seconds $CheckInterval
    }
} finally {
    try {
        if (Test-Path $LockFile) {
            $cur = [int]((Get-Content $LockFile -Raw).Trim().Split()[0])
            if ($cur -eq $PID) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
        }
    } catch {}
}
