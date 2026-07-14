#!/usr/bin/env bash
# Launch Codex CLI against local K12 chatgpt2api.
# Clears env vars that override ~/.codex/auth.json, forces gateway key.
#
# Usage:
#   bash scripts/codex_k12.sh
#   bash scripts/codex_k12.sh exec -m gpt-5.6 -s read-only --ephemeral "Reply: OK"

set -euo pipefail

GATEWAY_HEALTH="http://127.0.0.1:8124/health?format=json"
AUTH_KEY="k12-pool-local"

# Strip overrides that beat auth.json / provider config
unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE CODEX_API_KEY OPENAI_ORG_ID OPENAI_PROJECT_ID 2>/dev/null || true
export OPENAI_API_KEY="$AUTH_KEY"
# Do NOT set OPENAI_BASE_URL — codex uses model_providers.k12local from config.toml

if ! command -v codex >/dev/null 2>&1; then
  for c in \
    "$LOCALAPPDATA/Programs/OpenAI/Codex/bin/codex.exe" \
    "/c/Users/zhugu/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe"
  do
    if [[ -x "$c" ]]; then
      PATH="$(dirname "$c"):$PATH"
      export PATH
      break
    fi
  done
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "codex not found in PATH" >&2
  exit 127
fi

if ! curl -fsS --max-time 5 "$GATEWAY_HEALTH" >/dev/null 2>&1; then
  echo "[codex_k12] WARN: gateway not healthy at :8124 — start chatgpt2api first" >&2
fi

echo "[codex_k12] OPENAI_API_KEY=$AUTH_KEY (local gateway)"
echo "[codex_k12] tip: cc-switch --app codex provider current"

exec codex "$@"
