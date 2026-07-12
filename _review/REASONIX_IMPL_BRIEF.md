# Reasonix implementation brief — P0 ops harden

Project: `D:/Users/grok-auto-register`  
Do **not** touch: `cpa_auths/`, `config.json` secrets, tokens, live auth JSON bulk.

## Goal
Close community gaps for self-hosted pool (no VPS):
1. Heartbeat / dead-process + low-pool alert (file-based, no Telegram required)
2. Power/sleep self-check surface in `pool_status`
3. Sticky / REMOVE diagnostics improvement in `pool_status`
4. Light egress: on TLS mint fail, ensure clash `report_fail` is invoked if available
5. Tests for pure logic (no network)

## Constraints
- Minimal diffs; match existing style (type hints, no new heavy deps)
- Windows-first; PowerShell ok for power check helper
- Soft-disable sticky-safe invariants: **never** hard-delete live CPA in new code
- Chinese comments only where file already uses Chinese

## Concrete tasks

### A. `ops_heartbeat.py` (new)
CLI: `python ops_heartbeat.py [--json] [--write logs/heartbeat.json]`

Check:
- processes: register (`grok_register_ttk.py` auto), `quota_watch.py`, `cli-proxy-api` (reuse patterns from `logs/_check_procs.py` / wmic or psutil-free: `tasklist` + cmdline via existing project helpers if any)
- pool: count `cpa_auths/xai-*.json` not disabled (read disabled flag only; no probe)
- compare to config `pool_min_live` / `quota_watch_min_pool` (default 100)
- exit code: `0` ok, `1` warn (low pool or missing optional), `2` critical (register or cliproxy dead)

Output JSON fields: `ok`, `level`, `procs`, `pool_live_est`, `min_live`, `alerts[]`, `ts`

### B. `pool_status.py`
- Call power check: if `scripts/ensure_power_awake.ps1` exists, run **read-only** check via `powercfg` subprocess (do not change power plan from pool_status). Show AC standby/lid as never/do-nothing or WARN.
- Affinity section: also report absolute reselect and suggest if `reselect_rate > 0.15` → "check REMOVE churn / disabled sticky targets"
- Optionally read last `logs/heartbeat.json` if present and print one line

### C. Mint TLS → clash report_fail
In `cpa_xai/mint.py` (or `egress_rotate.py` if cleaner): when transient TLS fails and `rotate_mint_egress` runs, if clash module has `report_fail`, call it for last node so bad exits get soft-disabled faster. Guard import errors.

### D. Tests
`tests/test_ops_heartbeat.py`:
- mock process list / empty pool thresholds
- pure functions preferred

### E. Docs
Short section in `docs/UNATTENDED.md`: how to run heartbeat + optional Task Scheduler every 10–15 min.

## Done criteria
- `python -m py_compile` on touched files
- `pytest tests/test_ops_heartbeat.py tests/test_authcode_and_purge.py -q` pass
- No secrets in commits
- Write summary to `_review/REASONIX_IMPL_DONE.md`

## Out of scope
- VPS deploy
- New email domains
- Raising concurrency
- Rewriting browser mint
