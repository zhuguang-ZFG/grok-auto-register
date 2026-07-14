# K12 pool daily maintain: slim (optional) + refill water level + backup
# Headless-friendly: no GUI; Python HTTP/SQLite only (no browser).
# Safe for Task Scheduler. Does NOT re-import full 80k.
# When pool is all-disabled (dead workspace pause), skips probe/refill.
$ErrorActionPreference = "Continue"
$python = if (Test-Path "C:\Users\zhugu\scoop\apps\python313\current\python.exe") {
  "C:\Users\zhugu\scoop\apps\python313\current\python.exe"
} else {
  "python"
}
$root = "D:\Users\grok-auto-register"
$logDir = Join-Path $root "logs"
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDir "k12_pool_maintain_$ts.log"

function Run-Step($title, $argsList) {
  Add-Content -Path $logFile -Value "==== $title $(Get-Date -Format o) ====" -Encoding utf8
  & $python @argsList 2>&1 | Tee-Object -FilePath $logFile -Append
  Add-Content -Path $logFile -Value "exit=$LASTEXITCODE" -Encoding utf8
  return $LASTEXITCODE
}

Set-Location $root
Add-Content -Path $logFile -Value "k12_pool_maintain headless start $(Get-Date -Format o)" -Encoding utf8

# 1) backup live DB first
Run-Step "daily-backup" @("$root\scripts\k12_daily_backup.py", "--retention", "14") | Out-Null

# 2) status WITHOUT chat probe by default (status auto-skips probe when all disabled)
Run-Step "refill-status" @("$root\scripts\k12_pool_refill.py", "status", "--no-probe") | Out-Null

# 3) slim only if live pool is huge (safety: keep-recent 1500)
Run-Step "slim-dry" @("$root\scripts\k12_pool_slim.py", "--keep-recent", "1500", "--dry-run") | Out-Null

$db = Join-Path $root "chatgpt2api\data\accounts.db"
if (Test-Path $db) {
  $mb = [math]::Round((Get-Item $db).Length / 1MB, 1)
  Add-Content -Path $logFile -Value "db_size_mb=$mb" -Encoding utf8
  if ($mb -gt 80) {
    Run-Step "slim-apply" @("$root\scripts\k12_pool_slim.py", "--keep-recent", "1500") | Out-Null
  } else {
    Add-Content -Path $logFile -Value "skip slim (db ${mb}MB <= 80MB)" -Encoding utf8
  }
}

# 4) refill if water low — script refuses when all-disabled (dead workspace)
#    no --probe: avoid wasteful chat on paused pool; use k12_pool_ops when live again
$rc = Run-Step "refill" @(
  "$root\scripts\k12_pool_refill.py", "refill",
  "--min-ready", "800",
  "--target", "1800",
  "--hard-cap", "2500",
  "--max-add", "400"
)
Add-Content -Path $logFile -Value "refill_exit=$rc" -Encoding utf8

# prune old maintain logs
Get-ChildItem "$logDir\k12_pool_maintain_*.log" -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
  Remove-Item -Force -ErrorAction SilentlyContinue

Add-Content -Path $logFile -Value "done log=$logFile" -Encoding utf8
# Avoid Write-Host popup noise when run under Task Scheduler
