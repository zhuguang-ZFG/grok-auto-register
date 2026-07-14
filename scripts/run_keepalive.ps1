# CPA Keepalive — 每 3 小时跑一轮，保活 150 个号
$ErrorActionPreference = "SilentlyContinue"
$python = "C:\Users\zhugu\scoop\apps\python313\current\python.exe"
$script = "D:\Users\grok-auto-register\scripts\cpa_keepalive.py"
$logDir = "D:\Users\grok-auto-register\logs"
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = "$logDir\keepalive_$ts.log"
& $python $script --max 150 2>&1 | Out-File -FilePath $logFile -Encoding utf8
# 清理 7 天前的日志
Get-ChildItem "$logDir\keepalive_*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } | Remove-Item -Force
