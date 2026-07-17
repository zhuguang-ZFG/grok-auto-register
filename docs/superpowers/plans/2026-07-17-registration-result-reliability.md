# Registration Result Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every account that reaches an SSO token is durably saved or queued for idempotent recovery before optional post-processing runs.

**Architecture:** Add a focused `account_outputs.py` persistence boundary using cross-process file locks, fsync, and atomic pending rewrites. Adapt both GUI and CLI success paths in `grok_register_ttk.py` to persist first, then run NSFW/CPA/token post-processing as isolated warnings. Add a browser-free `retry-pending` command.

**Tech Stack:** Python 3.13, standard library, existing `filelock`, `unittest`.

## Global Constraints

- Do not rewrite the current GUI/CLI orchestration architecture.
- Do not change Clash or system networking.
- Do not add slow community upstreams to the main `grok-4.5` alias.
- Do not commit or push without explicit user authorization.
- Modified Python files must pass `python -m py_compile`.

---

### Task 1: Durable account output boundary

**Files:**
- Create: `account_outputs.py`
- Create: `tests/test_account_outputs.py`

**Interfaces:**
- Produces: `append_account_line(path, email, password, sso) -> bool`
- Produces: `queue_unsaved_account(path, payload, error) -> str`
- Produces: `retry_pending_file(pending_path, output_path=None, log_callback=None) -> dict`

- [ ] Write failing tests for append de-duplication, pending write, idempotent recovery, malformed-line retention, same-path rejection, and target/pending lock ordering.
- [ ] Run `python -m unittest tests.test_account_outputs -v`; expect import/function failures.
- [ ] Implement cross-process `FileLock` around every append/read-modify-write operation. Flush and fsync before releasing locks.
- [ ] Implement pending recovery with sorted lock paths and `tempfile.mkstemp` + `os.replace`.
- [ ] Run `python -m unittest tests.test_account_outputs -v`; expect all tests to pass.

### Task 2: Persist-first registration flow

**Files:**
- Modify: `grok_register_ttk.py` GUI path around `_run_single_account`
- Modify: `grok_register_ttk.py` CLI path around `_run_single_account_cli`
- Create: `tests/test_registration_result_reliability.py`

**Interfaces:**
- Consumes: Task 1 persistence functions.
- Produces: `_persist_registered_account(output_path, email, password, sso, profile, log_callback) -> dict` with `saved`, `pending_saved`, and `error`.

- [ ] Write failing unit tests that mock main-file failure and verify pending fallback; mock both failures and verify no false success; verify post-processing exceptions do not erase persisted results.
- [ ] Run the focused tests and confirm failures.
- [ ] Add `_persist_registered_account()` as the only main-account persistence entry point.
- [ ] In both GUI and CLI paths, call persistence immediately after `wait_for_sso_cookie()` and before CPA/NSFW/token actions.
- [ ] Count success only when `saved` or `pending_saved` is true; on double failure log a full recovery record and raise a persistence error.
- [ ] Wrap NSFW, CPA enqueue, and post-register pipeline independently and collect warnings.
- [ ] Run focused tests until green.

### Task 3: Idempotent retry CLI

**Files:**
- Modify: `grok_register_ttk.py` argument dispatch near `main()`
- Test: `tests/test_registration_result_reliability.py`

**Interfaces:**
- Consumes: `account_outputs.retry_pending_file()`.
- Produces command: `python grok_register_ttk.py retry-pending <pending> [output]`.

- [ ] Write a failing test proving the command dispatches before GUI/browser startup.
- [ ] Add argument parsing that prints restored/remaining/output summary and exits.
- [ ] Run command-level test and a temporary-file smoke test.

### Task 4: Structured post-processing isolation

**Files:**
- Modify: `grok_register_ttk.py:run_post_register_pipeline`
- Test: `tests/test_registration_result_reliability.py`

**Interfaces:**
- Produces: `run_post_register_pipeline(...) -> {token_file, grok2api_pools, local_grok, warnings}`.

- [ ] Write failing tests for each post-processing component raising independently.
- [ ] Refactor pipeline so every component catches its own exception and appends a warning string.
- [ ] Ensure callers only log warnings and never alter registered/saved status.
- [ ] Run focused tests.

### Task 5: Verification

**Files:**
- Verify: `account_outputs.py`, `grok_register_ttk.py`, related tests, `D:/cli-proxy-api/config.yaml`.

- [ ] Run `python -m py_compile account_outputs.py grok_register_ttk.py`.
- [ ] Run focused unit tests.
- [ ] Run `python -m unittest discover -s tests -p "test_*.py"` and record any unrelated pre-existing failures separately.
- [ ] POST a non-streaming `grok-4.5` request through `http://127.0.0.1:8317/v1/chat/completions`; expect HTTP 200.
- [ ] Verify `remote-susu-grok` is available only by its debug alias and not mapped into the main alias.

No git commit is included because repository policy requires explicit per-action authorization.
