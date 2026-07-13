#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kiro side-pool pipeline: hotmail slice → KiroX_Cli → import Kiro-Go.

Never writes ``cpa_auths/``. Grok pool stays on CLIProxy :8317.

Modes
-----
  dry-run (default): check binary, Kiro-Go, proxy, slice N mails to work CSV,
                     print plan; **no** registration, **no** import.
  --live: run KiroX_Cli once, import successful tokens into Kiro-Go admin API,
          probe chat.

Examples
--------
  python scripts/kiro_side_pool_pipeline.py
  python scripts/kiro_side_pool_pipeline.py --n 1 --live
  python scripts/kiro_side_pool_pipeline.py --n 3 --live --proxy http://127.0.0.1:7897
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SIDE = ROOT / "side_pools"
KIRO_GO = SIDE / "kiro-go"
KIROX = SIDE / "kirox-cli"
WORK = KIROX / "work"
OUT_DIR = KIROX / "output"
HOTMAIL = ROOT / "data" / "hotmail_pool.txt"
HOTMAIL_USED = ROOT / "data" / "hotmail_pool.used.txt"
HOTMAIL_DEAD = ROOT / "data" / "hotmail_pool.dead.txt"
KIRO_USED = WORK / "kiro_mail_used.txt"
DEFAULT_ADMIN = "local-kiro-side-pool"
DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_BASE = "http://127.0.0.1:8080"


def _redact_line(line: str) -> str:
    parts = line.strip().split("----")
    if len(parts) < 1:
        return "***"
    email = parts[0]
    return f"{email}----***REDACTED***"


def _load_skip_emails() -> set[str]:
    skip: set[str] = set()
    for path in (HOTMAIL_USED, HOTMAIL_DEAD, KIRO_USED):
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


def slice_outlook_csv(
    n: int,
    dest: Path,
    *,
    extra_skip: set[str] | None = None,
) -> list[str]:
    """Pick first N free hotmail lines → outlook.csv. Returns emails."""
    if not HOTMAIL.is_file():
        raise SystemExit(f"missing hotmail pool: {HOTMAIL}")
    skip = _load_skip_emails()
    if extra_skip:
        skip |= {e.lower() for e in extra_skip}
    # also skip recent TES fails this session (file)
    att = WORK / "kiro_mail_attempts.txt"
    if att.is_file():
        for line in att.read_text(encoding="utf-8", errors="ignore").splitlines():
            em = line.split("\t", 1)[0].strip().lower()
            if "@" in em:
                skip.add(em)
    picked: list[str] = []
    emails: list[str] = []
    for line in HOTMAIL.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        if len(parts) != 4:
            continue
        email = parts[0].strip().lower()
        if email in skip:
            continue
        picked.append(line)
        emails.append(parts[0].strip())
        if len(picked) >= n:
            break
    if len(picked) < n:
        raise SystemExit(f"not enough free hotmail: need {n}, got {len(picked)}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(picked) + "\n", encoding="utf-8")
    return emails


def admin_password() -> str:
    return (
        os.environ.get("ADMIN_PASSWORD")
        or os.environ.get("KIRO_GO_ADMIN_PASSWORD")
        or DEFAULT_ADMIN
    )


def http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> tuple[int, Any]:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def check_kiro_go(base: str) -> dict[str, Any]:
    code, body = http_json("GET", f"{base.rstrip('/')}/v1/models", timeout=10)
    ok = code == 200 and isinstance(body, dict) and isinstance(body.get("data"), list)
    return {"ok": ok, "http": code, "models": len(body.get("data") or []) if isinstance(body, dict) else 0}


def check_proxy(proxy: str) -> bool:
    # light TCP-ish via urllib through env would need proxy handler; just probe host:port
    m = re.match(r"https?://([^:/]+):(\d+)", proxy.strip())
    if not m:
        return False
    host, port = m.group(1), int(m.group(2))
    import socket

    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def extract_import_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """Map KiroX_Cli success JSON → Kiro-Go /admin/api/auth/credentials body.

    KiroX_Cli saveResults flattens success as:
      {refreshToken, clientId, clientSecret, region, email, provider}
    In-memory run map also has status/aws_token/client_id (not always on disk).
    """
    # Already-imported / disk success shape (no status field)
    rt = result.get("refreshToken") or result.get("refresh_token")
    cid = result.get("clientId") or result.get("client_id")
    csec = result.get("clientSecret") or result.get("client_secret")
    email = result.get("email")
    access = result.get("accessToken") or ""
    region = result.get("region") or "us-east-1"

    if result.get("status") and result.get("status") != "success":
        return None

    aws = result.get("aws_token")
    if isinstance(aws, dict):
        rt = rt or aws.get("refreshToken") or aws.get("refresh_token")
        access = access or aws.get("accessToken") or ""

    kt = result.get("kiro_tokens")
    if isinstance(kt, dict) and not rt:
        rt = kt.get("refreshToken")

    if not rt or not cid or not csec:
        return None
    return {
        "refreshToken": rt,
        "clientId": cid,
        "clientSecret": csec,
        "authMethod": "idc",
        "region": region,
        "accessToken": access,
        "_email": email,
    }


def import_to_kiro_go(base: str, password: str, payload: dict[str, Any]) -> tuple[int, Any]:
    body = {
        "refreshToken": payload["refreshToken"],
        "clientId": payload["clientId"],
        "clientSecret": payload["clientSecret"],
        "authMethod": payload.get("authMethod") or "idc",
        "region": payload.get("region") or "us-east-1",
    }
    if payload.get("accessToken"):
        body["accessToken"] = payload["accessToken"]
    url = f"{base.rstrip('/')}/admin/api/auth/credentials"
    return http_json(
        "POST",
        url,
        headers={"X-Admin-Password": password},
        body=body,
        timeout=90,
    )


def mark_mail_used(email: str) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    with KIRO_USED.open("a", encoding="utf-8") as f:
        f.write(email.strip() + "\n")
    # also mirror into hotmail used so Grok reg won't re-pick
    with HOTMAIL_USED.open("a", encoding="utf-8") as f:
        f.write(email.strip() + "\n")


def run_kirox(
    n: int,
    csv_path: Path,
    out_json: Path,
    proxy: str,
    concurrency: int,
    debug: bool,
    skip_verify: bool = True,
) -> int:
    exe = KIROX / "kirox-cli.exe"
    if not exe.is_file():
        raise SystemExit(f"missing {exe}")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(exe),
        "-outlook",
        "-outlook-csv",
        str(csv_path),
        "-n",
        str(n),
        "-j",
        str(max(1, concurrency)),
        "-o",
        str(out_json),
        "-d",
        "3",
    ]
    if proxy:
        cmd.extend(["-p", proxy])
    if debug:
        cmd.append("-debug")
    if skip_verify:
        cmd.append("-skip-verify")
    print("exec:", " ".join(cmd[:6]), "...", f"-n {n}", "skip_verify=", skip_verify)
    # cwd so relative paths inside binary stay under kirox-cli
    return subprocess.call(cmd, cwd=str(KIROX))


def load_results(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def probe_chat(base: str) -> tuple[int, str]:
    code, body = http_json(
        "POST",
        f"{base.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": "Bearer any"},
        body={
            "model": "claude-sonnet-4.5",
            "messages": [{"role": "user", "content": "reply with: pong"}],
            "max_tokens": 8,
            "stream": False,
        },
        timeout=120,
    )
    snippet = json.dumps(body, ensure_ascii=False)[:200] if not isinstance(body, str) else body[:200]
    return code, snippet


def main() -> int:
    ap = argparse.ArgumentParser(description="Kiro side-pool: dry-run or live register+import")
    ap.add_argument("--n", type=int, default=1, help="accounts to register (default 1)")
    ap.add_argument("--live", action="store_true", help="actually run KiroX_Cli + import (default dry-run)")
    ap.add_argument("--proxy", default=os.environ.get("KIRO_REG_PROXY") or DEFAULT_PROXY)
    ap.add_argument("--base-url", default=os.environ.get("KIRO_GO_BASE") or DEFAULT_BASE)
    ap.add_argument("-j", type=int, default=1, help="KiroX concurrency")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--skip-import", action="store_true", help="live register only, no Kiro-Go import")
    ap.add_argument("--import-only", default="", help="import existing results.json path only")
    ap.add_argument(
        "--retries",
        type=int,
        default=1,
        help="live attempts with clash node rotate between tries (default 1)",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="force kirox-cli post-reg ListModels verify (default: skip-verify)",
    )
    ap.add_argument(
        "--rotate-clash",
        action="store_true",
        help="before each live attempt, rotate Clash selector via clash_proxy",
    )
    args = ap.parse_args()

    print("=== kiro side-pool pipeline ===")
    print(f"mode={'LIVE' if args.live or args.import_only else 'DRY-RUN'}")
    print(f"root={ROOT}")
    print(f"kirox_exe={ (KIROX / 'kirox-cli.exe').is_file() }")
    print(f"kiro_go_exe={ (KIRO_GO / 'kiro-go.exe').is_file() }")
    print(f"proxy={args.proxy} reachable={check_proxy(args.proxy)}")
    kg = check_kiro_go(args.base_url)
    print(f"kiro-go models ok={kg['ok']} http={kg['http']} models={kg['models']}")

    if args.import_only:
        results = load_results(Path(args.import_only))
        print(f"import-only results={len(results)}")
        pw = admin_password()
        ok_n = 0
        for r in results:
            payload = extract_import_payload(r)
            if not payload:
                print("  skip non-success or missing tokens", r.get("email"), r.get("status"), r.get("error", "")[:80])
                continue
            code, body = import_to_kiro_go(args.base_url, pw, payload)
            print(f"  import {payload.get('_email')} → http={code} {str(body)[:160]}")
            if code == 200:
                ok_n += 1
                if payload.get("_email"):
                    mark_mail_used(str(payload["_email"]))
        code, snip = probe_chat(args.base_url)
        print(f"chat probe http={code} {snip}")
        return 0 if ok_n else 1

    # prepare slice
    csv_path = WORK / "outlook.csv"
    emails = slice_outlook_csv(args.n, csv_path)
    print(f"sliced {len(emails)} mail(s) → {csv_path}")
    for e in emails:
        print(f"  {_redact_line(e)}")

    out_json = OUT_DIR / f"results_{time.strftime('%Y%m%d_%H%M%S')}.json"
    print(f"planned out={out_json}")
    print("import target: POST /admin/api/auth/credentials (idc + aws refreshToken)")
    print("isolation: never write cpa_auths/")

    if not args.live:
        print("\nDRY-RUN complete. Next: re-run with --live --n 1")
        print(
            f"  python scripts/kiro_side_pool_pipeline.py --live --n 1 --proxy {args.proxy}"
        )
        if not kg["ok"]:
            print("WARN: start Kiro-Go first: scripts/start_kiro_go_side_pool.ps1")
            return 2
        if not (KIROX / "kirox-cli.exe").is_file():
            print("WARN: rebuild kirox-cli")
            return 2
        if not check_proxy(args.proxy):
            print("WARN: proxy not listening; live reg likely fails")
            return 2
        return 0

    # LIVE
    if not kg["ok"]:
        print("FAIL: Kiro-Go not healthy")
        return 2
    if not check_proxy(args.proxy):
        print("FAIL: proxy down", args.proxy)
        return 2

    # Load clash helpers only when rotating (same module Grok reg uses)
    rotate_fn = None
    if args.rotate_clash or args.retries > 1:
        try:
            sys.path.insert(0, str(ROOT))
            import clash_proxy as _cp  # type: ignore

            def _rotate() -> None:
                # Rule-mode only: rotate clash_selector group. Never force_global.
                cfg_path = ROOT / "config.json"
                cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
                api = cfg.get("clash_api") or "http://127.0.0.1:9097"
                secret = cfg.get("clash_secret") or ""
                selector = cfg.get("clash_selector") or "宝可梦"
                try:
                    info = _cp.rotate_node(
                        api=api,
                        secret=secret,
                        selector=selector,
                        close_conns=False,  # don't kill host TCP
                        force_global=False,
                    )
                    safe = repr(info).encode("unicode_escape").decode("ascii")
                    print(f"clash rotate selector={selector!r} -> {safe}")
                except TypeError:
                    info = _cp.rotate_node(api, secret)
                    safe = repr(info).encode("unicode_escape").decode("ascii")
                    print(f"clash rotate -> {safe}")
                except Exception as e:
                    print(f"clash rotate err: {e!r}")
                time.sleep(1.5)

            rotate_fn = _rotate
        except Exception as e:
            print(f"clash_proxy import failed: {e}")

    all_results: list[dict[str, Any]] = []
    last_out = out_json
    attempts = max(1, args.retries)
    for attempt in range(1, attempts + 1):
        if rotate_fn and (args.rotate_clash or attempt > 1):
            print(f"\n--- clash rotate before attempt {attempt}/{attempts} ---")
            rotate_fn()
        # re-slice: failed TES may leave mail in csv or removed; always refresh free mails
        emails = slice_outlook_csv(args.n, csv_path)
        print(f"attempt {attempt}: mails={[e for e in emails]}")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        last_out = OUT_DIR / f"results_{stamp}_a{attempt}.json"
        skip_v = not bool(getattr(args, "verify", False))
        rc = run_kirox(args.n, csv_path, last_out, args.proxy, args.j, args.debug, skip_verify=skip_v)
        print(f"kirox-cli exit={rc}")
        batch = load_results(last_out)
        # disk success items may lack status==success; treat refreshToken as success
        if not batch:
            alt = KIROX / "output" / "results.json"
            if alt.is_file():
                batch = load_results(alt)
                last_out = alt
        print(f"results_file={last_out} count={len(batch)}")
        for r in batch:
            print(
                f"  {r.get('email')} status={r.get('status')} "
                f"has_rt={bool(r.get('refreshToken') or (r.get('aws_token') or {}).get('refreshToken') if isinstance(r.get('aws_token'), dict) else False)} "
                f"err={(r.get('error') or '')[:80]}"
            )
        all_results.extend(batch)
        success_now = [r for r in batch if extract_import_payload(r)]
        if success_now:
            break
        # mark TES-burned mail lightly so we don't hammer same address
        for e in emails:
            # don't put in hotmail used yet (reg failed); track kiro attempts
            with (WORK / "kiro_mail_attempts.txt").open("a", encoding="utf-8") as f:
                f.write(f"{e}\tTES_or_fail\t{stamp}\n")

    success = [r for r in all_results if extract_import_payload(r)]
    print(f"\nsuccess_importable={len(success)} total_rows={len(all_results)}")

    if args.skip_import:
        print("skip-import: done")
        return 0 if success else 1

    pw = admin_password()
    imported = 0
    for r in success:
        payload = extract_import_payload(r)
        if not payload:
            continue
        code, body = import_to_kiro_go(args.base_url, pw, payload)
        print(f"  import {payload.get('_email')} → http={code} {str(body)[:200]}")
        if code == 200:
            imported += 1
            if payload.get("_email"):
                mark_mail_used(str(payload["_email"]))

    code, snip = probe_chat(args.base_url)
    print(f"\nchat probe http={code} {snip}")
    print(f"imported={imported}")
    return 0 if imported else (1 if all_results else 3)


if __name__ == "__main__":
    raise SystemExit(main())
