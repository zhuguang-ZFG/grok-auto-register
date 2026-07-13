# Databricks Trial Pipeline Design (B0→B2)

**Date:** 2026-07-13  
**Status:** Draft for user review (architecture approved in chat: full-auto B, implement B0→B2 in one pass)  
**Repo:** `zhuguang-ZFG/grok-auto-register`  
**Related thread:** linux.do post — Databricks ~$400 / 14-day trial, domain email, models `system.ai.*` / Foundation Model APIs

---

## 1. Problem

Community notes that Databricks free trial (Express) can unlock ~14 days and up to ~$400 usage, with domain email supported, and Foundation Model APIs exposing models such as Qwen3.5-122B, Gemma 3 12B, GPT-OSS-120B (and possibly GLM depending on region/catalog naming).

This repo already runs a mature **Grok** registration + CPA pool + CLIProxy path. The ask is to build a **separate, full-automation pipeline** for Databricks trial accounts: register → workspace → PAT → model probe → pool → OpenAI-compatible consume (Kimi).

## 2. Goals

### 2.1 In scope (B0→B2, single delivery)

| Stage | Deliverable |
|-------|-------------|
| **B0** | Package skeleton, credential schema, pool index, probe, OpenAI-compatible proxy, Kimi wiring docs |
| **B1** | Playwright Express signup + email OTP/link verification (unattended when no phone/hard CAPTCHA) |
| **B2** | Skip/finish onboarding, mint workspace PAT, end-to-end `register --count N` → live JSON with successful probe |

Operational defaults (user-confirmed):

- `concurrent_count = 1`
- `max_per_day = 5`
- `min_interval_sec = 120` (configurable)

### 2.2 Out of scope (explicit)

- Merging into `grok_register_ttk.py` / `cpa_auths` / Grok mint path
- SMS farms, captcha-solving services, payment/card binding bypass
- Account resale protocols
- Guaranteeing ToS-compliant bulk abuse or steady high success rates
- Free Edition vs Trial confusion: **target is Express free trial (~$400 / 14d path)**, not Free Edition hobby quotas
- B3 (multi-worker scale-out, advanced health daemon) — design-compatible later, not required for this pass

## 3. Non-goals / red lines

1. **Compliance:** Automated bulk signup and trial harvesting may violate [Databricks Terms](https://www.databricks.com/legal/terms) and anti-abuse rules. Implementation is for research/automation-engineering study aligned with this repo’s existing disclaimer. Operators own legal risk.
2. **Human gates:** On phone verification or hard CAPTCHA, set `status=needs_human`, screenshot, **stop that account** — no retry storms.
3. **Isolation:** Never write Databricks credentials into `cpa_auths/` or Grok pool indexes.
4. **Honesty:** Success is “pipeline produces probe-live credentials when the environment allows,” not “N stable $400 accounts/day.”

## 4. Background (facts used)

- Trial: Express signup with email; serverless workspace; credits valid 14 days after trial start ([free trial docs](https://docs.databricks.com/aws/en/getting-started/free-trial), [express setup](https://docs.databricks.com/aws/en/getting-started/express-setup)).
- Inference: OpenAI-compatible Model Serving / Foundation Model APIs; needs workspace host + Databricks token ([score foundation models](https://docs.databricks.com/aws/en/machine-learning/model-serving/score-foundation-models)).
- Example endpoint names (AWS docs, pay-per-token):  
  `databricks-qwen35-122b-a10b`, `databricks-gpt-oss-120b`, `databricks-gemma-3-12b`  
  ([supported models](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models)).
- Community UI names like `system.ai.qwen35-122b-a10b` are **aliases** in our catalog, mapped to serving endpoint names.
- PAT: workspace-level personal access tokens for API auth ([PAT docs](https://docs.databricks.com/aws/en/dev-tools/auth/pat)). Programmatic mint prefers UI automation or authenticated workspace REST from an already-logged-in browser session.

## 5. Architecture

```
email (CF / Cloud Mail / optional Hotmail mix)
        │
        ▼
egress (Clash / proxy from config) + isolated browser profile
        │
        ▼
Phase 1  browser_signup   Express form + email verify
        │
        ▼
Phase 2  workspace_ready  wait URL + dismiss onboarding
        │
        ▼
Phase 3  token_mint       create PAT (UI primary)
        │
        ▼
Phase 4  probe            chat/completions or invocations
        │
        ▼
Phase 5  pool             databricks_auths/*.json + index
        │
        ▼
Phase 6  proxy            :8320 OpenAI-compatible → Kimi
```

### 5.1 Package layout

```
databricks_pipeline/
  __init__.py
  __main__.py              # python -m databricks_pipeline ...
  cli.py
  config.py
  email_bridge.py          # thin adapter over existing mail modules
  browser_signup.py
  onboarding.py
  token_mint.py
  probe.py
  pool.py
  proxy_server.py          # stdlib/http.server or uvicorn-thin; keep deps light
  models_catalog.yaml
  selectors.yaml
  fake_identity.py         # name/password generators
tests/
  test_databricks_pool.py
  test_databricks_probe.py
  test_databricks_schema.py
  test_databricks_alias.py
docs/
  DATABRICKS_PIPELINE.md   # operator runbook + ToS note
```

Optional later: `scripts/databricks_daily_quota.py` — not required for B2.

### 5.2 Config (`config.json` fragment)

```json
{
  "databricks": {
    "enabled": true,
    "register_count": 1,
    "concurrent_count": 1,
    "max_per_day": 5,
    "min_interval_sec": 120,
    "signup_url": "https://www.databricks.com/try-databricks",
    "prefer_express": true,
    "cloud_preference": "aws",
    "email_provider": "cloudflare",
    "use_repo_email_settings": true,
    "human_gate_on_phone": true,
    "human_gate_on_captcha": true,
    "auth_dir": "databricks_auths",
    "dead_dir": "databricks_auths_dead",
    "screenshots_dir": "screenshots/databricks",
    "proxy_port": 8320,
    "proxy_api_key": "sk-local-databricks-pool",
    "probe_models": [
      "databricks-qwen35-122b-a10b",
      "databricks-gpt-oss-120b",
      "databricks-gemma-3-12b"
    ],
    "probe_timeout_sec": 60,
    "workspace_ready_timeout_sec": 600,
    "browser_headless": false,
    "selectors_file": "databricks_pipeline/selectors.yaml",
    "models_catalog_file": "databricks_pipeline/models_catalog.yaml"
  }
}
```

Email: when `use_repo_email_settings=true`, reuse top-level `email_provider`, Cloudflare, `cloud_mail_*`, proxy/Clash keys already in `config.json` rather than duplicating secrets.

### 5.3 Credential schema

File: `databricks_auths/dbx-<safe_email_or_id>.json`

```json
{
  "id": "dbx-uuid",
  "email": "user@domain",
  "password": "...",
  "host": "https://dbc-xxxx.cloud.databricks.com",
  "token": "dapi...",
  "cloud": "aws",
  "region": null,
  "trial_started_at": "2026-07-13T00:00:00+00:00",
  "trial_expires_at": "2026-07-27T00:00:00+00:00",
  "models": {
    "databricks-qwen35-122b-a10b": {
      "ok": true,
      "last_probe_at": "...",
      "last_error": null
    }
  },
  "aliases": {
    "system.ai.qwen35-122b-a10b": "databricks-qwen35-122b-a10b",
    "qwen35-122b-a10b": "databricks-qwen35-122b-a10b"
  },
  "status": "live",
  "disable_reason": null,
  "needs_human_detail": null,
  "created_at": "...",
  "updated_at": "..."
}
```

`status` enum: `live` | `soft_disabled` | `dead` | `needs_human` | `incomplete`.

Index: `databricks_auths/pool_index.json` — list of ids, status, email, expires, last_probe summary (no full token duplication required; may store path only).

### 5.4 Model catalog

`models_catalog.yaml` maps:

- community alias → endpoint name  
- probe payload shape (chat vs raw invocations)  
- skip-if-404 policy  

GLM: include optional aliases `system.ai.glm-5.2` → candidate endpoint patterns; if probe 404, mark model entry `ok=false` without failing whole account if at least one probe_models entry succeeds.

**Account live rule:** ≥1 configured `probe_models` returns success → `status=live`. All fail → `soft_disabled` + `probe_all_failed`.

### 5.5 Browser automation

- **Tooling:** Playwright (sync API), Chromium. Prefer adding `playwright` if not already pinned for this path; do not force DrissionPage coupling to Grok GUI.
- **Profile:** per-account temp user data dir under `.browser_profiles/databricks/<id>/`.
- **Selectors:** external `selectors.yaml` with version field; on failure save HTML snippet + PNG under `screenshots/databricks/`.
- **Phases:**  
  1. Open signup URL → Express path  
  2. Fill email/password/profile fields from `fake_identity`  
  3. Poll mailbox via `email_bridge` for verify link or code  
  4. Complete verification  
  5. Detect workspace host from URL or UI  
  6. `onboarding.py` click-through / skip  
  7. Navigate User Settings → Developer → Access tokens → generate PAT (comment: `grok-auto-register-dbx`)  
  8. Persist host+token; close browser  

Phone/CAPTCHA detectors: URL patterns, known iframe/text; trip human gate.

### 5.6 Email bridge

- Functions only: create address, wait for message matching Databricks senders/subjects, extract first `https://` verify link or 6-digit code.
- Reuse existing modules (`cloud_mail_otp`, Cloudflare client paths used by register flow) via import — **no copy-paste of full mail stacks**.
- Timeout and single resend policy aligned with Grok OTP timeouts where practical.

### 5.7 Probe

Primary (OpenAI-compatible style where supported):

```http
POST {host}/serving-endpoints/{endpoint}/invocations
Authorization: Bearer {token}
Content-Type: application/json
```

Body: chat-style messages minimal ping (`"ping"` / `"Say hi"`), `max_tokens` small.

Also try documented OpenAI client base paths if invocations shape fails (record which shape worked in credential `models.*.api_shape`).

Classify:

- 200 + choices/text → ok  
- 401/403 → auth dead  
- 402 / budget → soft_disabled `quota`  
- 404 endpoint → model unavailable (not whole account)  
- 429 → soft_disabled `rate` temporary (timestamp for later re-probe)

### 5.8 Pool operations

| CLI | Behavior |
|-----|----------|
| `register [--count N]` | Respect day cap file `databricks_auths/.daily_count-YYYYMMDD`; interval sleep; run pipeline |
| `probe [--all\|--id]` | Re-probe and update status |
| `list` | Table of status / expiry / models ok |
| `disable --id --reason` | soft_disable |
| `export-litellm` | optional snippet |
| `proxy` | run OpenAI-compatible server |

Day cap: if `register_count` would exceed `max_per_day`, stop with non-zero exit and clear message.

### 5.9 OpenAI-compatible proxy

- Listen `127.0.0.1:{proxy_port}`  
- Auth: `Authorization: Bearer {proxy_api_key}`  
- `GET /v1/models` — union of live aliases  
- `POST /v1/chat/completions` — resolve model alias → endpoint; pick live credential (round-robin); forward; on auth failure mark soft_disabled and retry once with next credential  
- No remote bind by default  

Kimi: add provider pointing at `http://127.0.0.1:8320/v1` with model aliases documented in `docs/DATABRICKS_PIPELINE.md`.

## 6. Error handling matrix

| Stage | Failure | Action |
|-------|---------|--------|
| Email create | error | retry ≤3 with domain fallback if multi-domain |
| Signup selectors | miss | screenshot, fail account `incomplete` |
| OTP timeout | no mail | one resend if UI allows, else fail |
| Phone / hard CAPTCHA | detected | `needs_human`, stop |
| Workspace timeout | >600s | fail `incomplete` |
| PAT mint | fail | fail (no fake live) |
| Probe all models | fail | `soft_disabled` |
| Proxy runtime 401 | | disable cred, failover |

## 7. Testing

- Unit: schema validation, expiry (`trial_expires_at` ≤ now → not selectable), day-cap math, alias resolve, RR picker  
- Probe: httpx/respx or unittest.mock for 200/401/402/404  
- Selectors: optional HTML fixtures if we capture one anonymized signup page later  
- E2E: `DATABRICKS_E2E=1` only; default tests never hit real Databricks  

## 8. Dependencies

- Prefer existing `requirements.txt` stack; add `playwright` if missing for this package only.  
- Proxy: prefer stdlib or already-present `flask`/`fastapi` — **choose lightest already in repo**; if none, use stdlib `http.server` + threading for MVP.  
- Do not add captcha-solver SDKs.

## 9. Documentation & disclaimer

`docs/DATABRICKS_PIPELINE.md` must include:

1. ToS / research-only disclaimer (same spirit as README)  
2. Express vs Free Edition  
3. Config keys  
4. CLI examples  
5. Kimi `config.toml` provider sample  
6. Human-gate behavior  
7. Day cap / concurrency defaults  

README: short pointer section only (no full duplicate).

## 10. Implementation order (single pass, but sequenced commits)

1. Schema + pool + config loader + tests  
2. Probe + catalog + tests  
3. Proxy + models list + manual token smoke path  
4. Email bridge  
5. Browser signup + verify  
6. Onboarding + PAT mint  
7. Wire CLI `register` end-to-end  
8. Operator doc + README blurb  
9. Full unit test run; optional single E2E if user env ready  

## 11. Success criteria (acceptance)

- [ ] `python -m databricks_pipeline list` works on empty pool  
- [ ] Manual-token path: drop JSON with host+token → `probe` can mark live  
- [ ] `proxy` serves `/v1/models` and forwards one chat when live cred exists  
- [ ] Automated path: `register --count 1` with clean email+egress and no phone wall produces `live` JSON **or** clean `needs_human`/`incomplete` with screenshot (no silent success)  
- [ ] Day cap and concurrent=1 enforced  
- [ ] No writes under `cpa_auths/`  
- [ ] Unit tests pass without network  

## 12. Risks

| Risk | Mitigation |
|------|------------|
| Signup DOM churn | selectors.yaml + screenshots |
| Trial offer geo/account variance | cloud_preference + soft fail |
| Endpoint naming drift | catalog + multi-shape probe |
| ToS enforcement / ban | low rate defaults; human gates |
| Long brittle E2E | phase logs; incomplete ≠ live |

## 13. Decisions log

| Decision | Choice |
|----------|--------|
| Automation level | B full-auto target |
| First delivery | B0→B2 one pass |
| Concurrency | 1 |
| Daily cap | 5 |
| Pool location | `databricks_auths/` |
| Grok coupling | none (email/proxy reuse only) |
| Phone/CAPTCHA | hard stop `needs_human` |

---

## Spec self-review

- No TBD placeholders left for MVP behavior.  
- Alias vs endpoint naming consistent.  
- Scope capped at B2; B3 deferred.  
- Ambiguity on Free Edition vs Trial resolved (Express trial).  
- Legal risk stated; no captcha-bypass scope creep.
