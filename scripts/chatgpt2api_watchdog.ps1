# chatgpt2api gateway watchdog
# Monitors http://127.0.0.1:8124/v1/models and restarts if down.
# Backoff: min(30 * 2^(n-1), 300)s; circuit-open after 10 failures.
#
#   powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File scripts/chatgpt2api_watchdog.ps1

param(
    [string]$GatewayUrl = "http://127.0.0.1:8124",
    [string]$AuthKey = "k12-pool-local",
    [int]$CheckInterval = 30,
    [string]$ProjectDir = "D:\Users\grok-auto-register"
)

$ErrorActionPreference = "SilentlyContinue"
$LogFile = Join-Path $ProjectDir "logs\chatgpt2api_watchdog.log"
$PidFile = Join-Path $ProjectDir "logs\chatgpt2api_gateway.pid"
$GatewayDir = Join-Path $ProjectDir "chatgpt2api"

function Write-Log([string]$msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    $logDir = Split-Path $LogFile -Parent
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Test-Gateway {
    try {
        $headers = @{ "Authorization" = "Bearer $AuthKey" }
        $resp = Invoke-WebRequest -Uri "$GatewayUrl/v1/models" -Headers $headers -TimeoutSec 10 -UseBasicParsing
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-Gateway {
    # Keep SQLite backend (community harden: JSON cold-backup only)
    $env:STORAGE_BACKEND = "sqlite"
    $env:DATABASE_URL = "sqlite:///" + ((Join-Path $ProjectDir "chatgpt2api/data/accounts.db") -replace "\\", "/")
    $env:CHATGPT2API_AUTH_KEY = $AuthKey
    $proc = Start-Process -FilePath "uv" `
        -ArgumentList "run", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8124", "--log-level", "warning", "--timeout-keep-alive", "30", "--limit-concurrency", "10" `
        -WorkingDirectory $GatewayDir `
        -WindowStyle Hidden `
        -PassThru
    $proc.Id | Out-File -FilePath $PidFile -Encoding UTF8
    Write-Log "Gateway started: PID $($proc.Id) storage=sqlite"
    return $proc
}

function Stop-Gateway {
    if (Test-Path $PidFile) {
        $oldPid = [int](Get-Content $PidFile -Raw).Trim()
        if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $oldPid -Force
            Write-Log "Killed old gateway: PID $oldPid"
        }
        Remove-Item $PidFile -Force
    }
}

$consecutiveFailures = 0
Write-Log "Watchdog started, monitoring $GatewayUrl"

while ($true) {
    if (Test-Gateway) {
        if ($consecutiveFailures -gt 0) {
            Write-Log "Gateway recovered after $consecutiveFailures failures"
        }
        $consecutiveFailures = 0
    } else {
        $consecutiveFailures++
        Write-Log "Gateway DOWN (failure #$consecutiveFailures)"
        Stop-Gateway
        $backoff = [Math]::Min(30 * [Math]::Pow(2, $consecutiveFailures - 1), 300)
        Write-Log "Waiting ${backoff}s before restart..."
        Start-Sleep -Seconds $backoff
        if ($consecutiveFailures -ge 10) {
            Write-Log "CIRCUIT BREAKER: 10 failures, exit"
            exit 1
        }
        Write-Log "Starting gateway..."
        Start-Gateway
        Start-Sleep -Seconds 8
        if (Test-Gateway) {
            Write-Log "Gateway restarted successfully"
            $consecutiveFailures = 0
        } else {
            Write-Log "Gateway still down after restart"
        }
    }
    Start-Sleep -Seconds $CheckInterval
}
