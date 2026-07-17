# CPA Keepalive — 每 3 小时跑一轮（默认 models-only，不烧 free 2M 额度）
# 2026-07-18: 禁止默认 --chat；全池 /responses 保活是号池耗尽主因之一。
# 主机: 16GB RAM / 8C16T；keepalive 网络 I/O 密集，6 线程安全。
# 覆盖: 仅 refresh + GET /models；显式 chat 需手工加 --chat（不推荐）。
$ErrorActionPreference = "SilentlyContinue"
$python = "C:\Users\zhugu\scoop\apps\python313\current\python.exe"
$script = "D:\Users\grok-auto-register\scripts\cpa_keepalive.py"
$logDir = "D:\Users\grok-auto-register\logs"
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = "$logDir\keepalive_$ts.log"
# 默认不带 --chat：只 /models 探活 + 临期 refresh
& $python $script --max 500 --workers 6 --warn-below 200 2>&1 | Out-File -FilePath $logFile -Encoding utf8
# 清理 7 天前的日志
Get-ChildItem "$logDir\keepalive_*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } | Remove-Item -Force
