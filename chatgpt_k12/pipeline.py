#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pipeline orchestrator — ChatGPT K12 registration → workspace join → export → import.

Modes (mirrors kiro_side_pool_pipeline.py):
  dry-run (default): check deps, proxy, slice N emails, print plan — no execution
  --live: run full pipeline (register → join → check → export → import)
  --probe: only verify existing tokens in chatgpt_auths/ for plan_type=k12
  --import-only <file>: skip registration, import an existing bundle JSON

Usage:
  python chatgpt_k12/pipeline.py                    # dry-run, n=1
  python chatgpt_k12/pipeline.py --n 5              # dry-run, plan 5
  python chatgpt_k12/pipeline.py --live --n 1       # register 1 account
  python chatgpt_k12/pipeline.py --probe            # check existing tokens
  python chatgpt_k12/pipeline.py --import-only data/chatgpt_k12_bundle.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PKG = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from register import ChatGPTRegistrar
from workspace_joiner import join_workspace
from token_check import check_account, is_k12
from export import build_account_record, save_account, export_bundle, import_to_gateway

HOTMAIL_POOL = ROOT / "data" / "hotmail_pool.txt"
HOTMAIL_USED = ROOT / "data" / "hotmail_pool.used.txt"
HOTMAIL_DEAD = ROOT / "data" / "hotmail_pool.dead.txt"
K12_USED = ROOT / "data" / "chatgpt_k12_mail_used.txt"
AUTH_DIR = ROOT / "chatgpt_auths"

DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_GATEWAY = "http://127.0.0.1:8124"
DEFAULT_AUTH_KEY = "k12-pool-local"


# -- Config loading ----------------------------------------------------------

def load_config() -> dict[str, Any]:
    """Load config.yaml with optional config.local.yaml override."""
    try:
        import yaml
    except ImportError:
        print("ERROR: pyyaml not installed. pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    base = PKG / "config.yaml"
    local = PKG / "config.local.yaml"
    cfg: dict[str, Any] = {}
    if base.is_file():
        cfg = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    if local.is_file():
        deep_merge(cfg, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    return cfg


def deep_merge(base: dict, overlay: dict) -> dict:
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


# -- Hotmail slicing (mirrors kiro_side_pool_pipeline pattern) ---------------

def load_skip_emails() -> set[str]:
    skip: set[str] = set()
    for path in (HOTMAIL_USED, HOTMAIL_DEAD, K12_USED):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            email = line.split("----", 1)[0].strip().lower()
            if "@" in email:
                skip.add(email)
    return skip


def slice_hotmail(n: int, dest: Path | None = None) -> list[dict[str, str]]:
    """Pick first N free hotmail rows. Returns list of parsed rows."""
    import hotmail_pool

    if not HOTMAIL_POOL.is_file():
        raise SystemExit(f"missing hotmail pool: {HOTMAIL_POOL}")

    skip = load_skip_emails()
    rows = hotmail_pool.load_pool(HOTMAIL_POOL)
    picked: list[dict[str, str]] = []
    for row in rows:
        if row["email"] in skip:
            continue
        if not row.get("refresh_token"):
            continue
        picked.append(row)
        if len(picked) >= n:
            break

    if len(picked) < n:
        raise SystemExit(f"not enough free hotmail with refresh_token: need {n}, got {len(picked)}")

    if dest:
        dest.parent.mkdir(parents=True, exist_ok=True)
        lines = [r["raw"] for r in picked]
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return picked


def mark_email_used(email: str) -> None:
    K12_USED.parent.mkdir(parents=True, exist_ok=True)
    with open(K12_USED, "a", encoding="utf-8") as f:
        f.write(email + "\n")


# -- Pre-flight checks -------------------------------------------------------

def check_proxy(proxy_url: str) -> bool:
    m = re.match(r"https?://([^:/]+):(\d+)", proxy_url.strip())
    if not m:
        return False
    host, port = m.group(1), int(m.group(2))
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def check_curl_cffi() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def check_gateway(base_url: str) -> dict[str, Any]:
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/v1/models",
            headers={"Authorization": f"Bearer {DEFAULT_AUTH_KEY}"},
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        models = len(data.get("data", [])) if isinstance(data, dict) else 0
        return {"ok": True, "models": models}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:80]}


# -- Probe mode: verify existing tokens -------------------------------------

def run_probe(proxy_url: str, cfg: dict[str, Any]) -> int:
    """Check all chatgpt_auths/*.json for plan_type=k12."""
    chatgpt_api = cfg.get("oauth", {}).get("chatgpt_api", "https://chatgpt.com/backend-api")
    files = sorted(AUTH_DIR.glob("chatgpt-*.json"))
    if not files:
        print("No accounts in chatgpt_auths/ to probe.")
        return 0

    print(f"Probing {len(files)} accounts...")
    k12_count = 0
    free_count = 0
    error_count = 0

    for f in files:
        record = json.loads(f.read_text(encoding="utf-8"))
        email = record.get("email", "?")
        token = record.get("access_token", "")
        if not token:
            print(f"  {email}: no token, skip")
            error_count += 1
            continue
        try:
            result = check_account(token, chatgpt_api=chatgpt_api, proxy_url=proxy_url)
            plan = result.get("plan_type", "?")
            if is_k12(result):
                k12_count += 1
                print(f"  {email}: K12 ✓")
            else:
                free_count += 1
                print(f"  {email}: {plan} (not K12)")
        except Exception as exc:
            error_count += 1
            print(f"  {email}: ERROR {str(exc)[:60]}")

    print(f"\n=== PROBE RESULT ===")
    print(f"K12:    {k12_count}")
    print(f"Free:   {free_count}")
    print(f"Error:  {error_count}")
    return 0


# -- Live mode: full registration pipeline ----------------------------------

def run_live(n: int, proxy_url: str, cfg: dict[str, Any], log: Any = None) -> int:
    log = log or print
    ws_cfg = cfg.get("workspace", {})
    oauth_cfg = cfg.get("oauth", {})
    gw_cfg = cfg.get("gateway", {})
    chatgpt_api = oauth_cfg.get("chatgpt_api", "https://chatgpt.com/backend-api")

    workspace_ids = ws_cfg.get("ids", [])
    ws_enabled = ws_cfg.get("enabled", True) and bool(workspace_ids)

    if ws_enabled:
        for wid in workspace_ids:
            if "PLACEHOLDER" in str(wid) or not wid:
                log("ERROR: workspace.ids contains placeholder. Fill in real K12 workspace UUID.")
                return 1

    # Slice emails
    log(f"\nSlicing {n} hotmail accounts...")
    mail_rows = slice_hotmail(n)
    work_csv = ROOT / "data" / "chatgpt_k12_work.csv"
    slice_hotmail(n, work_csv)
    log(f"  Work file: {work_csv}")

    registrar = ChatGPTRegistrar(cfg)
    results: list[dict[str, Any]] = []
    fp_module = None
    try:
        import anti_detect
        fp_module = anti_detect
    except ImportError:
        log("  (anti_detect not available, using default fingerprint)")

    for i, mail_row in enumerate(mail_rows, 1):
        email = mail_row["email"]
        log(f"\n=== [{i}/{n}] Registering {email} ===")

        # Pick fingerprint
        fingerprint = None
        if fp_module:
            fp = fp_module.pick_fingerprint()
            fingerprint = {
                "user_agent": fp.user_agent,
                "platform": fp.platform,
                "sec_ch_ua": fp.sec_ch_ua,
                "accept_language": fp.accept_language,
            }

        # Rotate proxy (optional — use clash_proxy if available)
        current_proxy = proxy_url
        try:
            import clash_proxy
            if clash_proxy.is_available(
                api=cfg.get("proxy", {}).get("clash_api", "http://127.0.0.1:9097"),
                secret=cfg.get("proxy", {}).get("clash_secret", ""),
            ):
                node = clash_proxy.rotate_node(
                    api=cfg.get("proxy", {}).get("clash_api", "http://127.0.0.1:9097"),
                    secret=cfg.get("proxy", {}).get("clash_secret", ""),
                )
                if node:
                    log(f"  Rotated to clash node: {node}")
        except Exception:
            pass

        # Step 1: Register
        try:
            reg_result = registrar.register(
                email=email,
                proxy_url=current_proxy,
                fingerprint=fingerprint,
                mail_row=mail_row,
                otp_timeout=cfg.get("mail", {}).get("wait_timeout", 120),
                log=log,
            )
        except Exception as exc:
            log(f"  ✗ Registration failed: {exc}")
            mark_email_used(email)
            continue

        mark_email_used(email)

        # Step 2: Join workspace
        check_result = None
        if ws_enabled:
            for wid in workspace_ids:
                log(f"\n  Joining workspace {wid[:8]}...")
                join_result = join_workspace(
                    reg_result["access_token"],
                    wid,
                    chatgpt_api=chatgpt_api,
                    route=ws_cfg.get("route", "request"),
                    proxy_url=current_proxy,
                    max_retries=ws_cfg.get("max_retries", 3),
                    retry_backoff_ms=ws_cfg.get("retry_backoff_ms", 5000),
                    log=log,
                )
                if join_result.get("status") != "ok":
                    log(f"  ✗ Join failed for {wid[:8]}")

            # Step 3: Check plan_type
            try:
                log(f"\n  Checking plan_type...")
                check_result = check_account(
                    reg_result["access_token"],
                    chatgpt_api=chatgpt_api,
                    proxy_url=current_proxy,
                )
                plan = check_result.get("plan_type", "?")
                log(f"  plan_type = {plan}")
                if not is_k12(check_result):
                    log(f"  ⚠ Not K12 — token saved but flagged as {plan}")
            except Exception as exc:
                log(f"  ⚠ Check failed: {exc}")

        # Step 4: Export
        record = build_account_record(reg_result, check_result)
        save_account(record, AUTH_DIR)
        results.append(record)
        log(f"  ✓ Saved to chatgpt_auths/")

    # Bundle export
    if results:
        bundle_path = export_bundle(results)
        log(f"\n=== EXPORT ===")
        log(f"Bundle: {bundle_path} ({len(results)} accounts)")

        # Import to gateway if available
        gw_base = gw_cfg.get("base_url", DEFAULT_GATEWAY)
        gw_key = gw_cfg.get("auth_key", DEFAULT_AUTH_KEY)
        gw_status = check_gateway(gw_base)
        if gw_status["ok"]:
            log(f"\nImporting to gateway {gw_base}...")
            import_result = import_to_gateway(
                results, base_url=gw_base, auth_key=gw_key, log=log
            )
            log(f"Imported: +{import_result['added']} added, {import_result['skipped']} skipped")
        else:
            log(f"\nGateway not available at {gw_base} ({gw_status.get('error','?')})")
            log(f"Run import later: --import-only {bundle_path}")

    log(f"\n=== PIPELINE COMPLETE ===")
    log(f"Registered: {len(results)}/{n}")
    return 0


# -- Import-only mode --------------------------------------------------------

def run_import_only(filepath: str, cfg: dict[str, Any], log: Any = None) -> int:
    log = log or print
    gw_cfg = cfg.get("gateway", {})
    gw_base = gw_cfg.get("base_url", DEFAULT_GATEWAY)
    gw_key = gw_cfg.get("auth_key", DEFAULT_AUTH_KEY)

    path = Path(filepath)
    if not path.is_file():
        print(f"File not found: {path}")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("accounts", data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        print("Invalid format: expected {accounts: [...]} or [...]")
        return 1

    log(f"Importing {len(records)} accounts to {gw_base}...")
    result = import_to_gateway(records, base_url=gw_base, auth_key=gw_key, log=log)
    log(f"\nDone: +{result['added']} added, {result['skipped']} skipped, {len(result['errors'])} errors")
    return 0 if not result["errors"] else 1


# -- Dry-run ----------------------------------------------------------------

def run_dry_run(n: int, proxy_url: str, cfg: dict[str, Any]) -> int:
    print("=" * 60)
    print("ChatGPT K12 Pipeline — DRY RUN")
    print("=" * 60)

    # Check deps
    print("\n--- Dependencies ---")
    cffi_ok = check_curl_cffi()
    print(f"  curl_cffi: {'OK' if cffi_ok else 'MISSING'}")

    # Check proxy
    print("\n--- Proxy ---")
    proxy_ok = check_proxy(proxy_url)
    print(f"  {proxy_url}: {'OK reachable' if proxy_ok else 'UNREACHABLE'}")

    # Check gateway
    gw_base = cfg.get("gateway", {}).get("base_url", DEFAULT_GATEWAY)
    print("\n--- Gateway ---")
    gw = check_gateway(gw_base)
    if gw["ok"]:
        print(f"  {gw_base}: OK ({gw['models']} models)")
    else:
        print(f"  {gw_base}: UNREACHABLE ({gw.get('error','?')})")

    # Check hotmail pool
    print("\n--- Hotmail Pool ---")
    if HOTMAIL_POOL.is_file():
        import hotmail_pool
        st = hotmail_pool.status(HOTMAIL_POOL)
        skip = load_skip_emails()
        free = st["unique"] - len(skip)
        print(f"  Total: {st['unique']}, Used/Dead: {len(skip)}, Free: {free}")
        print(f"  With refresh_token: {st['with_refresh_token']}")
        if n > free:
            print(f"  WARNING: Requested {n} but only {free} free!")
    else:
        print(f"  ✗ Missing: {HOTMAIL_POOL}")

    # Check workspace
    print("\n--- K12 Workspace ---")
    ws_ids = cfg.get("workspace", {}).get("ids", [])
    if not ws_ids:
        print("  WARN: No workspace IDs configured (accounts will be free tier)")
    elif any("PLACEHOLDER" in str(w) for w in ws_ids):
        print(f"  WARN: Placeholder workspace ID -- fill in real UUID in config")
        print(f"  Configured IDs: {ws_ids}")
    else:
        print(f"  IDs: {ws_ids}")

    # Slice preview
    print(f"\n--- Plan: register {n} account(s) ---")
    try:
        rows = slice_hotmail(n)
        for r in rows:
            print(f"  {r['email']} (rt={'Y' if r.get('refresh_token') else 'N'})")
    except SystemExit as exc:
        print(f"  ✗ {exc}")
        return 1

    print(f"\nDry run complete. Use --live to execute.")
    return 0


# -- Main --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="ChatGPT K12 registration pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--n", type=int, default=1, help="number of accounts (default: 1)")
    p.add_argument("--live", action="store_true", help="execute registration (default: dry-run)")
    p.add_argument("--probe", action="store_true", help="verify existing tokens for plan_type")
    p.add_argument("--import-only", metavar="FILE", help="import existing bundle JSON to gateway")
    p.add_argument("--proxy", default=DEFAULT_PROXY, help=f"proxy URL (default: {DEFAULT_PROXY})")
    args = p.parse_args(argv)

    cfg = load_config()

    if args.probe:
        return run_probe(args.proxy, cfg)
    if args.import_only:
        return run_import_only(args.import_only, cfg)
    if args.live:
        return run_live(args.n, args.proxy, cfg)
    return run_dry_run(args.n, args.proxy, cfg)


if __name__ == "__main__":
    sys.exit(main())
