# TASK: Read-only code review (NO file edits, NO git commits)

You are **Reasonix**. Review recent hardening work in this repo only.

## Repo (absolute)
`D:/Users/grok-auto-register`

## Scope — READ THESE FILES (do not wander the whole tree)
Primary code:
- `cpa_xai/authcode_mint.py` (new auth-code PKCE mint fallback)
- `cpa_xai/mint.py` (device → authcode → browser pipeline)
- `cpa_xai/protocol_mint.py` (transient TLS retries)
- `cpa_xai/usage.py` (6h recover window, reenable terminal skip)
- `quota_watch.py` — focus on `purge_dead_pool` terminal skip + soft-disable paths
- `refresh_pool.py` — `silent_refresh_pool` / soft_disable_dead
- `pool_health.py` — soft_disable / quarantine hard flag
- `clash_proxy.py` — rotate_node isolation defaults (force_global/close_conns/selector)
- `grok_register_ttk.py` — `rotate_egress_proxy` throttle + clash_selector wiring
- `pool_status.py` — sticky/reselect/authcode metrics
- `tests/test_authcode_and_purge.py`

Docs (context only):
- `docs/HARDEN.md`, `docs/CLASH_ISOLATE.md`, `docs/STATUS.md`

## Recent commits (for orientation)
- `3830907` feat: pool sticky, authcode mint fallback, host-safe proxy, perf
- `5e0ea38` docs: harden baseline
- `fde7474` feat(ops): clash isolate guide, sticky metrics, harden tests
- `923bb07` docs: snapshot runtime status

## What to look for
1. **Correctness**: mint fallback order; error handling; race with concurrent workers
2. **Sticky safety**: any remaining hard delete / atomic rewrite loops on live CPA files
3. **Security**: path traversal, SSRF in authcode redirects, secret leakage in logs
4. **Clash isolation**: defaults truly host-safe? selector edge cases
5. **Tests**: coverage gaps vs critical paths
6. **Ops**: dangerous defaults in config.example vs production

## Hard rules
- **READ ONLY**: do not edit any file, do not git commit/push, do not delete anything
- Workdir must stay `D:/Users/grok-auto-register`
- Prefer evidence: file + function + line-level notes when possible

## Output format (write ONLY to this new file)
Create **one** file: `D:/Users/grok-auto-register/_review/REASONIX_REVIEW.md`

Structure:
1. Executive summary (5–10 lines)
2. Findings table: Severity (P0/P1/P2) | Location | Issue | Why it matters | Suggested fix
3. What looks solid (bullet list)
4. Recommended next fixes (ordered, max 8)
5. Optional: risk residual after fixes

Keep the report under ~250 lines. Chinese or English OK; be concrete.
