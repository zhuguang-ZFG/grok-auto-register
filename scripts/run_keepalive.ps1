# CPA Keepalive — 每 3 小时跑一轮，保活 500 个号，6 线程并发
# 主机: 16GB RAM (79% used) / 8C16T CPU (100% load from Chromium+gateway)
# keepalive 是网络 I/O 密集型，不吃 CPU/RAM；6 线程在 16GB 机器上安全。
# 覆盖周期: 6200 号 / 500 每 3h ≈ 37 小时全池覆盖一轮
$ErrorActionPreference = "SilentlyContinue"
$python = "C:\Users\zhugu\scoop\apps\python313\current\python.exe"
$script = "D:\Users\grok-auto-register\scripts\cpa_keepalive.py"
$logDir = "D:\Users\grok-auto-register\logs"
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = "$logDir\keepalive_$ts.log"
& $python $script --max 500 --workers 6 --warn-below 200 2>&1 | Out-File -FilePath $logFile -Encoding utf8
# 清理 7 天前的日志
Get-ChildItem "$logDir\keepalive_*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } | Remove-Item -Force
