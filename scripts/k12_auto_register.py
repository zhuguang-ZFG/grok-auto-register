#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configure and drive chatgpt2api built-in register for pool replenishment.

Uses local hotmail_pool.txt (email----password----client_id----refresh_token)
as outlook_token provider, Clash proxy for OpenAI egress, and the gateway
register API:

  GET  /api/register
  POST /api/register          # update config
  POST /api/register/start
  POST /api/register/stop
  POST /api/register/reset
  POST /api/register/outlook-pool/reset

Modes:
  status   — show register config + stats
  prepare  — slice N free hotmail into register outlook pool + set proxy
  start    — start registration job (total accounts)
  maintain — loop: if normal accounts < min, start until target or max_batch
  stop     — stop running job

Examples:
  python scripts/k12_auto_register.py status
  python scripts/k12_auto_register.py prepare --n 50
  python scripts/k12_auto_register.py start --total 5 --threads 2
  python scripts/k12_auto_register.py maintain --min 1000 --target 2000 --batch 20
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = "http://127.0.0.1:8124"
AUTH_KEY = "k12-pool-local"
HOTMAIL = ROOT / "data" / "hotmail_pool.txt"
HOTMAIL_USED = ROOT / "data" / "hotmail_pool.used.txt"
HOTMAIL_DEAD = ROOT / "data" / "hotmail_pool.dead.txt"
K12_USED = ROOT / "data" / "chatgpt_k12_mail_used.txt"
REGISTER_USED = ROOT / "data" / "chatgpt_register_mail_used.txt"
DEFAULT_PROXY = "http://127.0.0.1:7897"


def log(msg: str) -> None:
    print(msg, flush=True)


def http_json(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 60.0,
) -> tuple[int, Any]:
    data = None
    headers = {"Authorization": f"Bearer {AUTH_KEY}"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{GATEWAY.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
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


def load_skip_emails() -> set[str]:
    skip: set[str] = set()
    for path in (HOTMAIL_USED, HOTMAIL_DEAD, K12_USED, REGISTER_USED):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            em = line.split("----", 1)[0].strip().lower()
            if "@" in em:
                skip.add(em)
    return skip


def slice_hotmail(n: int) -> list[str]:
    """Return n free hotmail lines with refresh_token."""
    if not HOTMAIL.is_file():
        raise SystemExit(f"missing hotmail pool: {HOTMAIL}")
    skip = load_skip_emails()
    picked: list[str] = []
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
        if not parts[3].strip():
            continue
        picked.append(line)
        if len(picked) >= n:
            break
    if len(picked) < n:
        raise SystemExit(f"not enough free hotmail: need {n}, got {len(picked)}")
    return picked


def mark_register_used(lines: list[str]) -> None:
    REGISTER_USED.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTER_USED, "a", encoding="utf-8") as f:
        for line in lines:
            em = line.split("----", 1)[0].strip()
            if em:
                f.write(em + "\n")


def get_register() -> dict[str, Any]:
    code, body = http_json("GET", "/api/register")
    if code != 200 or not isinstance(body, dict):
        raise SystemExit(f"GET /api/register failed: {code} {body}")
    return body.get("register") if isinstance(body.get("register"), dict) else body


def update_register(payload: dict[str, Any]) -> dict[str, Any]:
    code, body = http_json("POST", "/api/register", payload)
    if code != 200 or not isinstance(body, dict):
        raise SystemExit(f"POST /api/register failed: {code} {body}")
    return body.get("register") if isinstance(body.get("register"), dict) else body


def start_register() -> dict[str, Any]:
    code, body = http_json("POST", "/api/register/start")
    if code != 200 or not isinstance(body, dict):
        raise SystemExit(f"POST /api/register/start failed: {code} {body}")
    return body.get("register") if isinstance(body.get("register"), dict) else body


def stop_register() -> dict[str, Any]:
    code, body = http_json("POST", "/api/register/stop")
    if code != 200 or not isinstance(body, dict):
        raise SystemExit(f"POST /api/register/stop failed: {code} {body}")
    return body.get("register") if isinstance(body.get("register"), dict) else body


def gateway_normal_count() -> int:
    code, body = http_json("GET", "/api/accounts?page=1&page_size=1&status=normal")
    if code != 200 or not isinstance(body, dict):
        return -1
    return int(body.get("total") or 0)


def cmd_status(_: argparse.Namespace) -> int:
    reg = get_register()
    stats = reg.get("stats") if isinstance(reg.get("stats"), dict) else {}
    mail = reg.get("mail") if isinstance(reg.get("mail"), dict) else {}
    providers = mail.get("providers") if isinstance(mail.get("providers"), list) else []
    log("=== Register status ===")
    log(f"enabled:  {reg.get('enabled')}")
    log(f"mode:     {reg.get('mode')}")
    log(f"total:    {reg.get('total')}")
    log(f"threads:  {reg.get('threads')}")
    log(f"proxy:    {reg.get('proxy') or '(empty)'}")
    log(f"providers:{len(providers)}")
    for p in providers:
        if not isinstance(p, dict):
            continue
        log(
            f"  - type={p.get('type')} label={p.get('label')} "
            f"count={p.get('mailboxes_count', '?')} "
            f"stats={p.get('mailboxes_stats')}"
        )
    log(
        f"stats: success={stats.get('success')} fail={stats.get('fail')} "
        f"done={stats.get('done')} running={stats.get('running')} "
        f"rate={stats.get('success_rate')}"
    )
    normal = gateway_normal_count()
    log(f"gateway normal accounts: {normal}")
    logs = reg.get("logs") if isinstance(reg.get("logs"), list) else []
    if logs:
        log("--- recent logs ---")
        for item in logs[-8:]:
            if isinstance(item, dict):
                log(f"  {item.get('time','')} {item.get('text','')}")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    n = max(1, int(args.n))
    lines = slice_hotmail(n)
    proxy = args.proxy or DEFAULT_PROXY
    mailboxes = "\n".join(lines)
    payload = {
        "proxy": proxy,
        "total": int(args.total) if args.total else n,
        "threads": int(args.threads),
        "mode": "total",
        "mail": {
            "request_timeout": 30,
            "wait_timeout": 120,
            "wait_interval": 3,
            "api_use_register_proxy": True,
            "providers": [
                {
                    "type": "outlook_token",
                    "enable": True,
                    "label": "local-hotmail",
                    "mode": "auto",
                    "mailboxes": mailboxes,
                }
            ],
        },
    }
    reg = update_register(payload)
    mark_register_used(lines)
    mail = reg.get("mail") if isinstance(reg.get("mail"), dict) else {}
    providers = mail.get("providers") if isinstance(mail.get("providers"), list) else []
    count = 0
    for p in providers:
        if isinstance(p, dict) and p.get("type") == "outlook_token":
            count = int(p.get("mailboxes_count") or 0)
    log(f"Prepared {n} hotmail lines into register pool (reported count={count})")
    log(f"proxy={proxy} total={payload['total']} threads={payload['threads']}")
    log(f"marked used in {REGISTER_USED}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    # optional prepare first
    if args.n:
        args.total = args.total or args.n
        cmd_prepare(args)
    elif args.total or args.threads or args.proxy:
        payload: dict[str, Any] = {}
        if args.total:
            payload["total"] = int(args.total)
        if args.threads:
            payload["threads"] = int(args.threads)
        if args.proxy:
            payload["proxy"] = args.proxy
        if payload:
            update_register(payload)

    reg = start_register()
    stats = reg.get("stats") if isinstance(reg.get("stats"), dict) else {}
    log(f"Register STARTED enabled={reg.get('enabled')} job={stats.get('job_id')}")
    log(
        f"success={stats.get('success')} fail={stats.get('fail')} "
        f"done={stats.get('done')} running={stats.get('running')}"
    )
    if args.wait:
        return wait_job(timeout=int(args.wait))
    return 0


def wait_job(timeout: int = 600) -> int:
    t0 = time.time()
    while time.time() - t0 < timeout:
        reg = get_register()
        stats = reg.get("stats") if isinstance(reg.get("stats"), dict) else {}
        enabled = bool(reg.get("enabled"))
        running = int(stats.get("running") or 0)
        done = int(stats.get("done") or 0)
        success = int(stats.get("success") or 0)
        fail = int(stats.get("fail") or 0)
        log(f"  wait: enabled={enabled} running={running} done={done} ok={success} fail={fail}")
        if not enabled and running == 0:
            log("Job finished")
            return 0 if success > 0 or done == 0 else 1
        time.sleep(5)
    log("Wait timeout")
    return 1


def cmd_stop(_: argparse.Namespace) -> int:
    reg = stop_register()
    log(f"Register STOP requested enabled={reg.get('enabled')}")
    return 0


def cmd_maintain(args: argparse.Namespace) -> int:
    """Keep normal account count above min by starting register batches."""
    min_n = int(args.min)
    target = int(args.target or min_n)
    batch = int(args.batch)
    threads = int(args.threads)
    proxy = args.proxy or DEFAULT_PROXY
    interval = int(args.interval)

    log(f"Maintain loop: min={min_n} target={target} batch={batch} interval={interval}s")
    while True:
        normal = gateway_normal_count()
        log(f"normal accounts = {normal}")
        if normal < 0:
            log("gateway unreachable, sleep")
            time.sleep(interval)
            continue
        if normal >= min_n:
            log(f"above min ({min_n}), sleep {interval}s")
            time.sleep(interval)
            continue

        need = max(batch, min(target - normal, batch * 2))
        log(f"below min, preparing {need} mailboxes and starting register")
        try:
            ns = argparse.Namespace(
                n=need,
                total=need,
                threads=threads,
                proxy=proxy,
                wait=0,
            )
            cmd_prepare(ns)
            start_register()
            wait_job(timeout=max(300, need * 90))
        except Exception as exc:
            log(f"maintain error: {exc}")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="chatgpt2api auto-register driver")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show register status")

    p_prep = sub.add_parser("prepare", help="load hotmail into register pool")
    p_prep.add_argument("--n", type=int, default=20, help="mailboxes to load")
    p_prep.add_argument("--total", type=int, default=0, help="register total (default=n)")
    p_prep.add_argument("--threads", type=int, default=2)
    p_prep.add_argument("--proxy", default=DEFAULT_PROXY)

    p_start = sub.add_parser("start", help="start register job")
    p_start.add_argument("--n", type=int, default=0, help="also prepare N mailboxes first")
    p_start.add_argument("--total", type=int, default=0)
    p_start.add_argument("--threads", type=int, default=2)
    p_start.add_argument("--proxy", default=DEFAULT_PROXY)
    p_start.add_argument("--wait", type=int, default=0, help="wait seconds for job finish")

    sub.add_parser("stop", help="stop register job")

    p_m = sub.add_parser("maintain", help="auto-replenish when pool low")
    p_m.add_argument("--min", type=int, default=1000, help="start refill when normal < min")
    p_m.add_argument("--target", type=int, default=0, help="aim for this many normal accounts")
    p_m.add_argument("--batch", type=int, default=20, help="accounts per register batch")
    p_m.add_argument("--threads", type=int, default=2)
    p_m.add_argument("--proxy", default=DEFAULT_PROXY)
    p_m.add_argument("--interval", type=int, default=300, help="check interval seconds")

    args = p.parse_args(argv)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "prepare":
        return cmd_prepare(args)
    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    if args.cmd == "maintain":
        return cmd_maintain(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
