# Databricks Trial Pipeline Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Ship `databricks_pipeline` B0→B2: pool + probe + OpenAI proxy + email + DrissionPage Express signup + PAT mint + CLI.

**Architecture:** Isolated package (no `cpa_auths`). Credentials in `databricks_auths/`. Browser = **DrissionPage** (repo standard; not Playwright). Email reuses `cf_mail_debug` / `cloud_mail_otp`. Proxy is stdlib HTTP on `:8320`.

**Tech Stack:** Python 3.9+, requests, DrissionPage, stdlib http.server, pytest.

## Global Constraints

- Do not write into `cpa_auths/`
- `concurrent_count=1`, `max_per_day=5`, `min_interval_sec=120` defaults
- Phone/hard CAPTCHA → `needs_human`, no bypass SDKs
- No fake `live` without successful probe (≥1 model)
- Research/ToS disclaimer in operator doc

---

### Task 1: Schema + pool + config

**Files:** Create `databricks_pipeline/{__init__,config,schema,pool}.py`, `tests/test_databricks_pool.py`

- [x] Implement load_config, credential schema, day cap, list/save/disable
- [x] Unit tests for expiry + day cap

### Task 2: Probe + model catalog

**Files:** `models_catalog.yaml`, `probe.py`, `tests/test_databricks_probe.py`

- [x] Probe invocations + mark models; live if ≥1 ok

### Task 3: OpenAI-compatible proxy

**Files:** `proxy_server.py`, `tests/test_databricks_proxy.py`

- [x] `/v1/models`, `/v1/chat/completions` with RR + failover

### Task 4: Email bridge + identity

**Files:** `email_bridge.py`, `fake_identity.py`

- [x] CF + cloud_mail create + wait verify link/code

### Task 5: Browser signup + onboarding + PAT (DrissionPage)

**Files:** `browser_signup.py`, `onboarding.py`, `token_mint.py`, `selectors.yaml`, `pipeline.py`

- [x] End-to-end register phases with human gates

### Task 6: CLI + docs + config.example

**Files:** `__main__.py`, `cli.py`, `docs/DATABRICKS_PIPELINE.md`, `config.example.json`

- [x] `python -m databricks_pipeline {register,probe,list,proxy,disable}`

### Task 7: Verify

- [x] `pytest tests/test_databricks_*.py -q` → **8 passed**
- [x] package import + `python -m databricks_pipeline list`
