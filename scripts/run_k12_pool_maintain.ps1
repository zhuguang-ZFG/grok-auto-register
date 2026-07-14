# K12 pool daily maintain: slim (optional) + refill water level + backup
# Safe for Task Scheduler. Does NOT re-import full 80k.
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
}

Set-Location $root

# 1) backup live DB first
Run-Step "daily-backup" @("$root\scripts\k12_daily_backup.py", "--retention", "14")

# 2) status
Run-Step "refill-status" @("$root\scripts\k12_pool_refill.py", "status")

# 3) slim only if live pool is huge (safety: keep-recent 1500)
#    skip when already small — dry-run first to log plan
Run-Step "slim-dry" @("$root\scripts\k12_pool_slim.py", "--keep-recent", "1500", "--dry-run")

# Auto slim when total from dry-run log is not parsed; call slim with high bar via env
# Prefer: only slim if DB > 80MB (still fat)
$db = Join-Path $root "chatgpt2api\data\accounts.db"
if (Test-Path $db) {
  $mb = [math]::Round((Get-Item $db).Length / 1MB, 1)
  Add-Content -Path $logFile -Value "db_size_mb=$mb" -Encoding utf8
  if ($mb -gt 80) {
    Run-Step "slim-apply" @("$root\scripts\k12_pool_slim.py", "--keep-recent", "1500")
  } else {
    Add-Content -Path $logFile -Value "skip slim (db ${mb}MB <= 80MB)" -Encoding utf8
  }
}

# 4) refill if water low (never exceed hard-cap 2500)
Run-Step "refill" @(
  "$root\scripts\k12_pool_refill.py", "refill",
  "--min-ready", "800",
  "--target", "1800",
  "--hard-cap", "2500",
  "--max-add", "400",
  "--probe"
)

# prune old maintain logs
Get-ChildItem "$logDir\k12_pool_maintain_*.log" -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
  Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "done log=$logFile"
