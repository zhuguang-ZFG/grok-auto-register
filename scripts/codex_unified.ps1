# Start Codex against cc-switch codex-unified (CLIProxy :8327).
# Clears shell OPENAI_* so auth.json / config.toml win.
$ErrorActionPreference = "Stop"
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:OPENAI_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:OPENAI_API_BASE -ErrorAction SilentlyContinue

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$root\cc_switch_codex_provider.py" switch codex-unified | Out-Host

# health hint
try {
  $h = Invoke-WebRequest -Uri "http://127.0.0.1:8327/v1/models" -Headers @{ Authorization = "Bearer sk-local-codex-unified-2026" } -UseBasicParsing -TimeoutSec 5
  Write-Host "[ok] codex unified :8327 models HTTP $($h.StatusCode)"
} catch {
  Write-Host "[warn] :8327 not reachable — start D:\cli-proxy-api\start-codex.bat"
}

& codex @args
