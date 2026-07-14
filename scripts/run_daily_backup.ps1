# K12 Daily Backup — 每天凌晨 4 点跑 SQLite 快照，保留 14 天
$ErrorActionPreference = "SilentlyContinue"
$python = "C:\Users\zhugu\scoop\apps\python313\current\python.exe"
$script = "D:\Users\grok-auto-register\scripts\k12_daily_backup.py"
$logDir = "D:\Users\grok-auto-register\logs"
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = "$logDir\db_backup_$ts.log"
& $python $script --retention 14 2>&1 | Out-File -FilePath $logFile -Encoding utf8
Get-ChildItem "$logDir\db_backup_*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } | Remove-Item -Force
