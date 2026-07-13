# Start local Kiro-Go side pool (OpenAI/Anthropic gateway). Not Grok cpa_auths.
# Usage: powershell -File scripts/start_kiro_go_side_pool.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Dir = Join-Path $Root "side_pools\kiro-go"
$Exe = Join-Path $Dir "kiro-go.exe"
if (-not (Test-Path $Exe)) {
    Write-Error "Missing $Exe — rebuild from _community_ref/cursor_kiro_research/Kiro-Go"
}
New-Item -ItemType Directory -Force -Path (Join-Path $Dir "data") | Out-Null
# Default admin if config absent; change after first login.
if (-not $env:ADMIN_PASSWORD) { $env:ADMIN_PASSWORD = "local-kiro-side-pool" }
$env:CONFIG_PATH = "data/config.json"
$existing = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Port 8080 already listening (PID $($existing[0].OwningProcess)). Skip start."
    exit 0
}
Start-Process -FilePath $Exe -WorkingDirectory $Dir -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Dir "kiro-go.out.log") `
    -RedirectStandardError (Join-Path $Dir "kiro-go.err.log")
Start-Sleep -Seconds 2
Write-Host "Admin:  http://127.0.0.1:8080/admin  (password: `$env:ADMIN_PASSWORD or data/config.json)"
Write-Host "OpenAI: http://127.0.0.1:8080/v1/chat/completions"
Write-Host "Probe:  python scripts/probe_side_pool_gateway.py --base-url http://127.0.0.1:8080 --api-key any --label kiro-go"
