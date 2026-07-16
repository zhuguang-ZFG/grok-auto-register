# Claude Code via cc-switch claude-unified -> CLIProxy :8337
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

python "$root\cc_switch_claude_provider.py" switch claude-unified | Out-Host

try {
  $null = Invoke-WebRequest -Uri "http://127.0.0.1:8337/v1/models" -Headers @{
    Authorization = "Bearer sk-local-claude-unified-2026"
    "x-api-key"   = "sk-local-claude-unified-2026"
  } -UseBasicParsing -TimeoutSec 5
  Write-Host "[ok] claude unified :8337 up"
} catch {
  Write-Host "[warn] :8337 not reachable — start D:\cli-proxy-api\start-claude.bat"
}

# Reuse existing env injector from current provider
& "$root\claude_code_start.ps1" @args
