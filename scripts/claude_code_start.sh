#!/usr/bin/env bash
# Launch Claude Code with env from current cc-switch Claude provider.
# Usage: bash scripts/claude_code_start.sh [--resume] ...

set -euo pipefail
DB="${CC_SWITCH_DB:-$HOME/.cc-switch/cc-switch.db}"
# Windows Git Bash home may differ
if [[ ! -f "$DB" && -f "/c/Users/zhugu/.cc-switch/cc-switch.db" ]]; then
  DB="/c/Users/zhugu/.cc-switch/cc-switch.db"
fi

eval "$(python - "$DB" <<'PY'
import json, sqlite3, shlex, sys
db = sys.argv[1]
c = sqlite3.connect(db)
row = c.execute(
    "SELECT id, name, settings_config FROM providers WHERE app_type='claude' AND is_current=1"
).fetchone()
if not row:
    print("echo '[claude] no current provider; using existing env' >&2")
    raise SystemExit(0)
j = json.loads(row[2] or "{}")
env = j.get("env") or {}
print(f"echo '[claude] provider={row[0]} ({row[1]})' >&2")
for k, v in env.items():
    if not isinstance(v, str):
        v = str(v)
    print(f"export {k}={shlex.quote(v)}")
PY
)"

if [[ -n "${CLAUDE_MODEL_OVERRIDE:-}" ]]; then
  export ANTHROPIC_MODEL="$CLAUDE_MODEL_OVERRIDE"
fi

echo "[claude] base_url=${ANTHROPIC_BASE_URL:-} model=${ANTHROPIC_MODEL:-}" >&2
exec claude "$@"
