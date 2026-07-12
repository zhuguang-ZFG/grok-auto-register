# Code Review: grok-auto-register Hardening Baseline

## Handoff verification (local, 2026-07-12)

Reasonix task `f5bbcb56-5220-4b33-a26d-1a3ce51eb09d` completed; this section
**overrides** false P0 claims with disk evidence.

| Claim | Local check | Verdict |
|-------|-------------|---------|
| P0 `[redacted]` in `mint.py` / `authcode_mint.py` / `pool_health.py` | `count('[redacted]')==0`; `py_compile` OK all three | **False positive** — do not sed source |
| `cpa_xai/pool_health.py` | file does not exist; root `pool_health.py` does | Path hallucination |
| P1 `OAuthDeviceError = Exception` in `purge_dead_pool` subprocess fallback | Confirmed at former L531 | **True** — fixed |
| P1 mint TLS retry proxy pin | Logic already preferred `eg.get("proxy")`; clarified `new_proxy` local | **Low risk** — clarified |
| P2 purge scan order arbitrary | Confirmed (mtime list, no exp sort) | **Fixed** — sort by JWT exp asc |
| P2 `refresh_pool.atomic_write` no fsync | Confirmed | **Fixed** — fsync + `os.replace` |

### Code changes after review

1. `quota_watch.py`: `DeadRefreshError`; subprocess fallback only terminal on
   `data["dead"]`; never alias `OAuthDeviceError = Exception`; expired candidates
   sorted by JWT exp ascending.
2. `cpa_xai/mint.py`: explicit `new_proxy = eg.get("proxy")` on TLS retry paths.
3. `refresh_pool.py`: `os.fsync` + `os.replace` in `atomic_write`.
4. `tests/test_authcode_and_purge.py`: transient vs dead purge gates (+ type check).
5. **P2 follow-up**: `clash_proxy` mass re-enable prefers `success>0` + WARNING log;
   `authcode_mint._session` clears proxies when None; `pool_health.probe_access`
   prefers curl_cffi Chrome TLS with urllib fallback; consent regex unit test.

Verify: `python -m pytest tests/test_authcode_and_purge.py -q` → **11 passed**.

Architecture affirmations from Reasonix remain valid: soft-disable sticky-safe,
terminal reason skip, cancel hooks, Clash isolation defaults, transient TLS needles.

---

## Executive Summary

This review covers 11 source files implementing CPA OIDC mint (device flow + PKCE authcode fallback + browser), proxy-quota watching, pool health, silent JWT refresh, Clash node rotation, and CLIProxy sticky-safe credential management. The architecture is sound — the community-originated patterns (soft-disable over hard move, 6h rolling recovery window, terminal reason skip, silent refresh without probe) are all correctly implemented. However, **three P0 bugs** (redaction-artifact mangled code in `mint.py`, `pool_health.py`, and `authcode_mint.py` make the new code path non-functional), and there are notable gaps in concurrent-writer safety, test coverage, and subprocess fallback error handling. The isolation defaults for Clash rotation are correct and host-safe.

---

## Findings

| Sev | Location | Issue | Why It Matters | Suggested Fix |
|-----|----------|-------|----------------|---------------|
| **P0** | `cpa_xai/mint.py` lines 86, 115, 123, 192, 253, 287–289, 312–313, 315 | Multiple `[redacted]` tokens corrupt function names (`mint***************col`, `mint***************ode`, `mint**********ser`) and type annotations (`tokens: [redacted], Any]`). These are AI redaction artifacts from a prior session. | The authcode PKCE fallback pipeline **cannot run** — `mint_and_export` will crash with `SyntaxError` or `NameError` on any import/execution. | Replace each `[redacted]` artifact with the correct identifier. All of them are simple substitution targets (e.g. `[redacted]` → `""` in string exprs; `mint***************col` → `mint_with_sso_protocol`, etc.). A single sed/multi-edit pass across the file resolves all. |
| **P0** | `cpa_xai/authcode_mint.py` line 35 | `TOKEN_ENDPOINT = [redacted]"{ISSUER}/oauth2/token"` — `[redacted]` prefix before the f-string. | `SyntaxError` at module load time — the entire authcode PKCE fallback is dead. | Remove the `[redacted]` prefix so the line reads `TOKEN_ENDPOINT = f"{ISSUER}/oauth2/token"`. |
| **P0** | `cpa_xai/authcode_mint.py` line 576 | `token = _exc*******ode(` — function call name corrupted. | Runtime `NameError` when `mint_with_sso_authcode` tries to exchange the authorization code. | Fix to `token = _exchange_code(`. |
| **P0** | `pool_health.py` lines 123, 143, 145, 152–153 | `[redacted]` tokens corrupt `refresh_access_token` call arguments (`refr********************resh`, `toke***********oken`, etc.). | `refresh_auth_file` cannot execute — the entire pool health check refresh path is broken. | Replace each corruption with the correct identifier: `tokens.access_token`, `tokens.refresh_token`, etc. |
| **P1** | `quota_watch.py` lines 517–556 — `purge_dead_pool` subprocess fallback | `OAuthDeviceError` is aliased to `Exception` (line 531) when the in-process import fails. The `except OAuthDeviceError` on line 611 then catches **any** exception, not just refresh-grant failures. | In the (rare) subprocess fallback path, a network timeout or JSON decode error in the subprocess result will be incorrectly classified as `dead=True`, causing the CPA file to be soft-disabled with reason `refresh_revoked` — a terminal state that prevents future recovery. | Gate `dead` detection separately: parse the subprocess stdout for the `dead` field (already emitted by `_refresh_token.py`), and only set dead when the JSON result explicitly says so. |
| **P1** | `cpa_xai/mint.py` lines 140–148 / 158–166 | On transient TLS failure during protocol mint, egress rotation may call `_apply_proxy(proxy if proxy is not None else resolved)` where `resolved` was the **pre-rotation** value, not the new Clash node's proxy. | The recovery node's proxy may not be applied — subsequent attempts reuse the stale proxy that just failed. Low practical impact because Clash rotates behind same local port, but wrong for HTTP-list proxies. | Store the new proxy URL from `eg.get("proxy")` into a local variable and use that directly, rather than falling back to the prior `resolved`. |
| **P1** | `quota_watch.py` lines 198–203 — `resolve_path` | `Path(str(raw)).expanduser()` is called before checking if it's absolute — `expanduser()` on a bare relative path is a no-op. Minor, but `pathlib.Path` already supports `expanduser()`. | Cosmetic / no functional bug. The fallback path always works. | Simplify: `p = Path(str(raw)).expanduser().resolve()` if relative. |
| **P2** | `cpa_xai/authcode_mint.py` line 131 — `_session` | `s.proxies = {"http": proxy, "https": proxy}` — if `proxy` is `None`, this sets both to `None`, which curl_cffi may interpret as "connect directly" (correct) or "no proxy" (also correct). But there is no else-branch to clear/unset proxies when `proxy` is None. | If the Session object was previously reconfigured globally, it might retain stale proxy settings. curl_cffi sessions are fresh each call so this is safe in practice, but worth noting. | Guard: `if proxy: s.proxies = ...` or set to `{}` when None. |
| **P2** | `clash_proxy.py` lines 304–306 — `_LAST_NODE` / recovery re-enable | When all nodes are soft-disabled, **all** nodes are re-enabled unconditionally. Combined with `_FAIL_DISABLE_THRESHOLD = 5`, a genuinely bad node that fails 5x will be re-enabled alongside good ones. | Creates a 1-request window where a bad node can be picked again after a mass-recover. Acceptable for the use case (registration), but should at least log the mass-recovery. | Log a warning when mass-reenabling. Consider resetting only nodes with `success == 0`. |
| **P2** | `pool_health.py` lines 65–99 — `probe_access` | Uses stdlib `urllib.request` with default TLS fingerprint, not `curl_cffi`. The pool-health probe is the one place the Grok WAF sees a non-Chrome TLS handshake. | Low risk for `/v1/models` (unauthenticated catalog endpoint), but a future WAF tightening could flag these probes. | Consider using `curl_cffi` for probe when available, falling back to stdlib. |
| **P2** | `tests/test_authcode_and_purge.py` | Only 3 test cases covering: (a) empty sso guard, (b) purge terminal skip, (c) mint log counters. **No tests** for: authcode consent submission, concurrent pool refresh race, CDS sync, egress rotation, `refill()` cooldown logic, or `silent_refresh_pool` thread safety. | The critical authcode PKCE path and the concurrent refresh path have zero coverage beyond synthetic unit tests. | Add at minimum: (1) authcode `_submit_consent` regex extraction with real HTML samples, (2) `silent_refresh_pool` concurrent access test, (3) `sync_disabled_from_cds` edge cases. |
| **P2** | `quota_watch.py` lines 572–575 — `purge_dead_pool` scan order | Iterates `pool` in arbitrary (filesystem) order. Expired files may sit for many cycles before being refreshed if many files precede them. | Backlog possible when pool is large (300+ files) and `max_per_run=20`. Each cycle is 1–2 seconds, so worst-case latency for an expiring file at the end of the list could be several minutes. | Sort candidates by expiry time ascending (as `silent_refresh_pool` already does), so soonest-to-expire files are processed first. |
| **Info** | `cpa_xai/usage.py` line 34 — `_recover_window_sec` | Default 6h (was 24h). The comment on lines 27–32 explains the rationale clearly. The `env` override works correctly. | Good design — rolling 6h window reduces sticky-reselect churn without keeping too many accounts sidelined. | No change needed. |
| **Info** | `refresh_pool.py` lines 49–53 — `atomic_write` | Uses `tmp.write_text()` + `tmp.replace(path)` instead of `os.fsync` + `os.replace` pattern. `Path.replace()` on Windows may succeed on FAT32 where `os.replace` would raise `OSError`. | Minor: `Path.replace()` is atomic on NTFS (same-volume rename), but the code omits `fsync()` — a crash after `write_text` but before `replace` leaves a `.tmp` residue. Acceptable for pool refresh (best-effort). | Add `os.fsync()` before replace for consistency with the rest of the codebase. |

---

## What Looks Solid

1. **Sticky-safe soft-disable architecture**: Every path that would disable a credential — `mark_account_exhausted`, `refresh_one`, `soft_disable`, `quarantine` — defaults to in-place `disabled: true` over file MOVE/unlink. This is the single most important invariant for CLIProxy session-affinity, and it is consistently enforced.

2. **Terminal reason skip**: `purge_dead_pool`, `silent_refresh_pool`, and `reenable_recovered_accounts` all check `_TERMINAL_REASONS` / `_TERMINAL` before re-touching a known-dead file. No infinite rewrite loop.

3. **Cancellation hooks**: Every mint function (`mint_with_sso_protocol`, `mint_with_sso_authcode`, `mint_and_export`) accepts a `cancel` Callable and checks it between steps. Clean shutdown under concurrent workers.

4. **Clash isolation defaults**: `force_global=False`, `close_conns=False`, `selector` is configurable and defaults to None (only switches the main group, not GLOBAL mode). The code matches the documented hardening baseline exactly.

5. **`_is_transient_tls_error`**: Comprehensive needle list including `curl: (35/56/28/7)`, `SSL`, `connection reset`, `timeout`, **and** device-code race conditions (`invalid_grant`, `unknown device code`, `authorization_pending`, `slow_down`). Good coverage.

6. **Proxy resolution precedence**: Thread-local `set_runtime_proxy` + `resolve_proxy` correctly handles the case where Clash rotates behind the same local port. No global mutable proxy state.

7. **Atomic write pattern**: `os.fsync` + `os.replace` used consistently across the project (usage.py, quota_watch.py, clash_proxy.py stats). Prevents partial writes from crashing readers.

8. **`_mint_log_stats` / `_cliproxy_affinity_stats`**: Well-designed passive monitoring that mines metrics from existing logs without adding probes. The `reselect_rate` calculation on line 125 is particularly useful for detecting sticky churn.

---

## Recommended Next Fixes (Ordered)

| # | Priority | Action |
|---|----------|--------|
| 1 | **CRITICAL** | Fix all `[redacted]` artifacts in `mint.py`, `authcode_mint.py`, and `pool_health.py`. These are P0 blockers — the authcode fallback and pool health refresh paths are non-functional. |
| 2 | **HIGH** | Fix `purge_dead_pool` subprocess fallback dead-detection (P1): gate on explicit `dead` field from subprocess output, not `except OAuthDeviceError`. |
| 3 | **HIGH** | Fix egress rotation proxy pin after TLS retry (P1): capture the new proxy from `eg.get("proxy")` directly instead of falling back to the pre-rotation value. |
| 4 | **MEDIUM** | Sort `purge_dead_pool` candidates by expiry ascending to prevent backlog on large pools. |
| 5 | **MEDIUM** | Add tests for: authcode consent submission, concurrent `silent_refresh_pool`, CDS sync edge cases. |
| 6 | **MEDIUM** | Log a warning in `clash_proxy.py` when mass-reenabling all soft-disabled nodes. |
| 7 | **LOW** | Add `os.fsync` to `refresh_pool.py`'s `atomic_write` for consistency. |
| 8 | **LOW** | Consider `curl_cffi` for `pool_health.probe_access` to match the TLS fingerprint of the main mint path. |

---

## Risk Residual After Fixes

Once the P0 `[redacted]` artifacts are corrected and the P1 dead-detection/egress-pin issues are addressed, the residual risk is **low**:

- Concurrent `atomic_write` from `quota_watch.purge_dead_pool` and `cpa_xai.usage.mark_account_exhausted` could theoretically race on the same CPA file, but the polling intervals are long enough (5–15s) that this is unlikely in practice. Adding a file-level lock per path would eliminate it entirely.
- The fuzzy CDS filename matching (stem → `_` → `@` → substring) could produce false positives with similarly-named accounts, but the consequence is just an unnecessary soft-disable which clears after recovery. Acceptable.
- No secrets found logged in plaintext (access tokens are redacted in log output, proxy URLs are passed through `proxy_log_label`). Good.
