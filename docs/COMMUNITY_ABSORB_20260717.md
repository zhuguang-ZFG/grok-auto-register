# Community absorb notes — 2026-07-17

Sources:

- `D:/Downloads/归档.zip` → `_community_ref/archive_zip_20260717/`
- `D:/Downloads/grok-free-register-oss-release.tar.gz (1).zip` → `_community_ref/oss_release_20260717_1/`

## Absorbed (landed in this repo)

| Idea | From | Where |
|------|------|--------|
| cli-chat-proxy wash + grok-cli headers + JWT email | acpa_watchdog `finalize` | `import_cpa_with_probe.normalize` (prior P0) + `DEFAULT_HEADERS` sync |
| warmup + 403 short retry, no RT on chat gate | acpa_watchdog | `admit_candidate` (prior P0) |
| Global 429 single-probe gate | oss `GlobalRateLimitGate` | `cpa_xai/rate_limit_gate.py` + `oauth_device.refresh_access_token` |
| Dual-case `X-XAI-Token-Auth` + `x-compaction-at` | acpa_watchdog CLIPROXY_HEADERS | `cpa_xai/schema.DEFAULT_CLIENT_HEADERS` |
| JWT `bot_flag_source` / risk claims | archive `probe.decode_token_risk` | `cpa_xai/probe.py` |
| chat `error_kind` + `probe_account_health` | archive probe | `cpa_xai/probe.py` |
| tar/tgz import | community packs | `load_candidates` |
| 403 soft quarantine + retest | acpa_watchdog | `cpa_xai/quarantine.py` + `scripts/retest_quarantine.py` |
| 429 quota_exhausted quarantine (recover_after 6h) | acpa_watchdog | `import_cpa_with_probe.py` |
| proxy pool rotation / sticky rewrite | archive `cpa_export.py` | `cpa_xai/proxyutil.py` |

## Reviewed, **not** wholesale-copied

| Module | Why skip / defer |
|--------|------------------|
| `browser_warmchat.py` | Pre-mint browser chat; register machine is **off/low-freq** by policy |
| `warmup.py` | Post-mint random chat state machine; overlaps quota burn on free accounts |
| `credential_pool.py` | Separate ranking state; we already have pool_policy + soft 403 recover |
| OSS Docker clearance stack | User forbade Clash/host network churn; optional later as isolated doc only |
| OSS `AdmissionGate` CSP | Register-side concurrency; not needed while register is disabled |
| archive `cpa_export` proxy sticky pool | Larger rewrite; sticky already partially in mint/egress |

## Policy alignment

- Chat 200 = hard live. JWT bot flag = advisory only.
- 403 permission-denied ≠ dead; soft retry / soft hold, do not RT-kill.
- `invalid_grant` still goes through raceguard before any dead move.
