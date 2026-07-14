#Requires -Version 5.1
<#
.SYNOPSIS
  Launch Claude Code with env from current cc-switch Claude provider (or overrides).

.DESCRIPTION
  Reads ~/.cc-switch/cc-switch.db for the current claude provider settings_config.env
  and injects ANTHROPIC_* into this process, then runs `claude`.

.EXAMPLE
  .\scripts\claude_code_start.ps1
  .\scripts\claude_code_start.ps1 -Model claude-opus-4-8
  .\scripts\claude_code_start.ps1 --resume
#>

param(
    [string]$Model = "",
    [string]$DbPath = "$env:USERPROFILE\.cc-switch\cc-switch.db",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ClaudeArgs
)

$ErrorActionPreference = "Stop"

function Get-CurrentClaudeEnv {
    param([string]$Database)
    if (-not (Test-Path $Database)) { throw "cc-switch db not found: $Database" }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { throw "python not on PATH" }
    $code = @'
import json, sqlite3, sys
db = sys.argv[1]
c = sqlite3.connect(db)
row = c.execute(
    "SELECT id, name, settings_config FROM providers WHERE app_type=\"claude\" AND is_current=1"
).fetchone()
if not row:
    print("{}", end="")
    sys.exit(0)
j = json.loads(row[2] or "{}")
env = j.get("env") if isinstance(j, dict) else {}
if not isinstance(env, dict):
    env = {}
print(json.dumps({"id": row[0], "name": row[1], "env": env}, ensure_ascii=False))
'@
    $json = & $py.Source -c $code $Database
    return ($json | ConvertFrom-Json)
}

$info = Get-CurrentClaudeEnv -Database $DbPath
if (-not $info -or -not $info.env) {
    Write-Warning "No current claude provider env in cc-switch; using existing process env"
} else {
    Write-Host "[claude] provider=$($info.id) ($($info.name))"
    foreach ($p in $info.env.PSObject.Properties) {
        Set-Item -Path "Env:$($p.Name)" -Value ([string]$p.Value)
    }
}

if ($Model) {
    $env:ANTHROPIC_MODEL = $Model
    Write-Host "[claude] ANTHROPIC_MODEL=$Model"
}

$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
    $candidates = @(
        "$env:APPDATA\npm\claude.cmd",
        "$env:APPDATA\npm\claude",
        "$env:LOCALAPPDATA\Programs\claude\claude.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $claude = $c; break }
    }
}
if (-not $claude) { throw "claude not found on PATH" }

$exe = if ($claude -is [System.Management.Automation.CommandInfo]) { $claude.Source } else { $claude }
Write-Host "[claude] base_url=$env:ANTHROPIC_BASE_URL model=$env:ANTHROPIC_MODEL"
& $exe @ClaudeArgs
exit $LASTEXITCODE
