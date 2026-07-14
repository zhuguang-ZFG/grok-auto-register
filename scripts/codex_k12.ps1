#Requires -Version 5.1
<#
.SYNOPSIS
  Launch Codex CLI against local K12 chatgpt2api (cc-switch provider k12-local-chatgpt2api).

.DESCRIPTION
  Clears env vars that override ~/.codex/auth.json (OPENAI_API_KEY / BASE_URL / CODEX_API_KEY),
  forces the local gateway key, and runs codex with remaining args.

  Gateway: http://127.0.0.1:8124/v1  (must be running)
  Auth:    k12-pool-local            (local only; do not commit)

.EXAMPLE
  .\scripts\codex_k12.ps1
  .\scripts\codex_k12.ps1 exec -m gpt-5.6 -s read-only --ephemeral "Reply: OK"
#>

$ErrorActionPreference = "Stop"

$GatewayBase = "http://127.0.0.1:8124/v1"
$AuthKey = "k12-pool-local"

function Test-Gateway {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8124/health?format=json" -UseBasicParsing -TimeoutSec 5
        return $r.StatusCode -ge 200 -and $r.StatusCode -lt 300
    } catch {
        return $false
    }
}

# Strip overrides that beat auth.json / provider config
foreach ($name in @(
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "CODEX_API_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID"
)) {
    if (Test-Path "Env:$name") {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
}

$env:OPENAI_API_KEY = $AuthKey
# Do NOT set OPENAI_BASE_URL — codex must use model_providers.k12local from config.toml

$codex = Get-Command codex -ErrorAction SilentlyContinue
if (-not $codex) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\OpenAI\Codex\bin\codex.exe",
        "$env:USERPROFILE\.codex\packages\standalone\releases\*\bin\codex.exe"
    )
    foreach ($c in $candidates) {
        $hit = Get-Item $c -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) {
            $codex = $hit.FullName
            break
        }
    }
}
if (-not $codex) {
    Write-Error "codex not found in PATH or default install locations."
    exit 127
}

if (-not (Test-Gateway)) {
    Write-Warning "Gateway not healthy at http://127.0.0.1:8124 — start chatgpt2api (SQLite) first."
    Write-Warning "  cd D:\Users\grok-auto-register\chatgpt2api"
    Write-Warning "  `$env:STORAGE_BACKEND='sqlite'"
    Write-Warning "  `$env:DATABASE_URL='sqlite:///D:/Users/grok-auto-register/chatgpt2api/data/accounts.db'"
    Write-Warning "  `$env:CHATGPT2API_AUTH_KEY='$AuthKey'"
    Write-Warning "  uv run uvicorn main:app --host 127.0.0.1 --port 8124"
}

Write-Host "[codex_k12] OPENAI_API_KEY=$AuthKey (local gateway)"
Write-Host "[codex_k12] provider expected: k12local @ $GatewayBase"
Write-Host "[codex_k12] tip: cc-switch --app codex provider current"

$exe = if ($codex -is [System.Management.Automation.CommandInfo]) { $codex.Source } else { $codex }
& $exe @args
exit $LASTEXITCODE
