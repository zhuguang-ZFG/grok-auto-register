#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K12 pool ops (A): status / sample-probe / disable dead / purge abnormal.

Community pattern (chatgpt2api + chat2api):
  - poll pool health
  - on auth failure mark/disable/remove
  - keep gateway serving only live accounts

Uses chatgpt2api admin APIs:
  GET  /api/accounts
  POST /api/accounts/batch-update   status=禁用|异常|正常
  DELETE /api/accounts              tokens=[...]
  POST /api/accounts/refresh

Examples:
  python scripts/k12_pool_ops.py status
  python scripts/k12_pool_ops.py sample-probe --n 20
  python scripts/k12_pool_ops.py disable-dead --n 50 --dry-run
  python scripts/k12_pool_ops.py purge-abnormal --max 200 --dry-run
  python scripts/k12_pool_ops.py watch --interval 300 --probe-n 10
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = "http://127.0.0.1:8124"
AUTH_KEY = "k12-pool-local"
LOG = ROOT / "logs" / "k12_pool_ops.log"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


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


def status_counts() -> dict[str, int]:
    out = {"total": 0, "normal": 0, "limited": 0, "abnormal": 0, "disabled": 0}
    code, body = http_json("GET", "/api/accounts?page=1&page_size=1")
    if code == 200 and isinstance(body, dict):
        out["total"] = int(body.get("total") or 0)
    for st in ("normal", "limited", "abnormal", "disabled"):
        code, body = http_json("GET", f"/api/accounts?page=1&page_size=1&status={st}")
        if code == 200 and isinstance(body, dict):
            out[st] = int(body.get("total") or 0)
    return out


def list_accounts(
    *,
    status: str = "normal",
    page: int = 1,
    page_size: int = 50,
    keyword: str = "",
) -> tuple[int, list[dict[str, Any]]]:
    q = f"/api/accounts?page={page}&page_size={page_size}&status={status}"
    if keyword:
        q += f"&keyword={urllib.request.quote(keyword)}"
    code, body = http_json("GET", q)
    if code != 200 or not isinstance(body, dict):
        return 0, []
    items = body.get("items") or body.get("accounts") or []
    if not isinstance(items, list):
        items = []
    return int(body.get("total") or 0), [x for x in items if isinstance(x, dict)]


def active_chat_probe(model: str = "gpt-5-mini") -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "1"}],
        "stream": False,
    }
    t0 = time.time()
    code, body = http_json("POST", "/v1/chat/completions", payload, timeout=90)
    elapsed = round(time.time() - t0, 1)
    if code == 200 and isinstance(body, dict) and body.get("choices"):
        content = body["choices"][0].get("message", {}).get("content", "")
        return {"ok": True, "latency_s": elapsed, "response": str(content)[:60]}
    err = body if not isinstance(body, dict) else body.get("error") or body
    return {"ok": False, "latency_s": elapsed, "error": str(err)[:200], "http": code}


def sample_tokens(n: int, status: str = "normal") -> list[dict[str, Any]]:
    total, first = list_accounts(status=status, page=1, page_size=min(100, max(n, 20)))
    if total <= 0 or not first:
        return []
    # random pages for better coverage on large pools
    pages = max(1, min(50, (total + 99) // 100))
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    attempts = 0
    while len(picked) < n and attempts < n * 4:
        attempts += 1
        page = random.randint(1, pages)
        _, items = list_accounts(status=status, page=page, page_size=100)
        random.shuffle(items)
        for a in items:
            tok = str(a.get("access_token") or "").strip()
            if not tok or tok in seen:
                continue
            seen.add(tok)
            picked.append(a)
            if len(picked) >= n:
                break
    return picked


def batch_update_status(tokens: list[str], status: str) -> dict[str, Any]:
    if not tokens:
        return {"updated": 0}
    code, body = http_json(
        "POST",
        "/api/accounts/batch-update",
        {"access_tokens": tokens, "status": status},
        timeout=120,
    )
    if code != 200:
        return {"error": f"{code} {body}", "updated": 0}
    if isinstance(body, dict):
        return body
    return {"raw": body}


def delete_tokens(tokens: list[str]) -> dict[str, Any]:
    if not tokens:
        return {"removed": 0}
    code, body = http_json(
        "DELETE",
        "/api/accounts",
        {"tokens": tokens},
        timeout=120,
    )
    if code != 200:
        return {"error": f"{code} {body}", "removed": 0}
    return body if isinstance(body, dict) else {"raw": body}


def probe_account_token(
    access_token: str,
    proxy: str = "http://127.0.0.1:7897",
    account_id: str = "",
) -> dict[str, Any]:
    """Direct backend-api check for a single token (auth liveness).

    Workspace/K12 tokens often need Chatgpt-Account-Id header; without it
    some accounts return 401/403 even when still usable via the gateway.
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return {"ok": False, "error": "curl_cffi missing"}

    s = cffi_requests.Session(impersonate="chrome")
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "oai-device-id": str(__import__("uuid").uuid4()),
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    }
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id
        headers["chatgpt-account-id"] = account_id
    try:
        r = s.get(
            "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
            headers=headers,
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            plan = "unknown"
            accounts = data.get("accounts") or {}
            if isinstance(accounts, dict) and accounts:
                # prefer non-default workspace entries
                keys = [k for k in accounts.keys() if k != "default"] or list(accounts.keys())
                k = keys[0]
                acc = accounts[k].get("account") or accounts[k]
                plan = acc.get("plan_type") or "unknown"
            return {"ok": True, "http": 200, "plan_type": plan}
        return {"ok": False, "http": r.status_code, "error": r.text[:160]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def cmd_status(_: argparse.Namespace) -> int:
    st = status_counts()
    log(
        f"pool total={st['total']} normal={st['normal']} limited={st['limited']} "
        f"abnormal={st['abnormal']} disabled={st['disabled']}"
    )
    probe = active_chat_probe()
    if probe.get("ok"):
        log(f"chat probe OK {probe['latency_s']}s -> {probe.get('response')}")
    else:
        log(f"chat probe FAIL http={probe.get('http')} {probe.get('error')}")
    return 0 if probe.get("ok") else 1


def cmd_sample_probe(args: argparse.Namespace) -> int:
    """Probe strategy for shared K12 snapshots.

    IMPORTANT:
      Many shared K12 access_tokens are accepted by chatgpt2api conversation
      path but return 401 on bare /accounts/check. Direct check is therefore
      only informational unless --trust-direct-check is set.

    Default safe mode:
      - run gateway chat probe (authoritative for serving health)
      - optionally sample direct checks without disabling
    """
    n = max(1, int(args.n))
    chat = active_chat_probe()
    if chat.get("ok"):
        log(f"gateway chat probe OK {chat['latency_s']}s")
    else:
        log(f"gateway chat probe FAIL {chat}")

    accounts = sample_tokens(n, status=args.status)
    log(f"sample-probe n={len(accounts)} status={args.status} trust_direct={bool(args.trust_direct_check)}")
    ok = fail = 0
    dead_tokens: list[str] = []
    plan_counter: dict[str, int] = {}
    for i, a in enumerate(accounts, 1):
        tok = a.get("access_token") or ""
        email = a.get("email") or "?"
        acct_id = str(
            a.get("account_id")
            or a.get("chatgpt_account_id")
            or a.get("workspace_id")
            or ""
        )
        res = probe_account_token(tok, proxy=args.proxy, account_id=acct_id)
        if not res.get("ok") and acct_id:
            res2 = probe_account_token(tok, proxy=args.proxy, account_id="")
            if res2.get("ok"):
                res = res2
        if res.get("ok"):
            ok += 1
            plan = str(res.get("plan_type") or "?")
            plan_counter[plan] = plan_counter.get(plan, 0) + 1
            log(f"  [{i}/{len(accounts)}] OK {email} plan={plan}")
        else:
            fail += 1
            dead_tokens.append(tok)
            log(f"  [{i}/{len(accounts)}] DIRECT_FAIL {email} {res}")
        time.sleep(float(args.sleep))
    ratio = ok / len(accounts) if accounts else 0
    log(f"direct-check ok={ok} fail={fail} ratio={ratio:.1%} plans={plan_counter}")
    log("note: DIRECT_FAIL does not prove gateway-dead for shared K12 snapshots")

    if args.disable_dead and dead_tokens:
        if not args.trust_direct_check:
            log("refusing to disable on direct-check alone; pass --trust-direct-check to override")
        elif args.dry_run:
            log(f"dry-run would disable {len(dead_tokens)} tokens")
        else:
            r = batch_update_status(dead_tokens, "禁用")
            log(f"disabled dead: {r}")

    # serving health is based on gateway chat probe
    return 0 if chat.get("ok") else 1


def cmd_disable_dead(args: argparse.Namespace) -> int:
    """Sample normal accounts, disable those failing auth check.

    For shared K12 snapshots this is OFF by default unless
    --trust-direct-check is provided (direct /accounts/check is unreliable).
    """
    args.disable_dead = True
    if not getattr(args, "trust_direct_check", False):
        log("disable-dead requires --trust-direct-check for shared K12 snapshots")
        log("fallback: use gateway auto_remove_invalid_accounts + chat probe")
        return cmd_sample_probe(args)
    return cmd_sample_probe(args)


def cmd_purge_abnormal(args: argparse.Namespace) -> int:
    max_n = max(1, int(args.max))
    total, items = list_accounts(status="abnormal", page=1, page_size=min(200, max_n))
    tokens = [str(a.get("access_token") or "") for a in items if a.get("access_token")]
    tokens = tokens[:max_n]
    log(f"abnormal total={total}, purging up to {len(tokens)}")
    if not tokens:
        return 0
    if args.dry_run:
        log(f"dry-run would delete {len(tokens)} abnormal tokens")
        return 0
    r = delete_tokens(tokens)
    log(f"purge result: {r}")
    return 0 if "error" not in r else 1


def cmd_watch(args: argparse.Namespace) -> int:
    interval = max(60, int(args.interval))
    probe_n = max(0, int(args.probe_n))
    log(f"watch start interval={interval}s probe_n={probe_n}")
    while True:
        try:
            st = status_counts()
            alive = st["normal"] / st["total"] if st["total"] else 0
            log(
                f"watch total={st['total']} normal={st['normal']} "
                f"abnormal={st['abnormal']} disabled={st['disabled']} alive={alive:.1%}"
            )
            chat = active_chat_probe()
            if chat.get("ok"):
                log(f"watch chat OK {chat['latency_s']}s")
            else:
                log(f"watch chat FAIL {chat}")
            if probe_n > 0:
                ns = argparse.Namespace(
                    n=probe_n,
                    status="normal",
                    proxy=args.proxy,
                    sleep=args.sleep,
                    disable_dead=False,  # never auto-disable from direct-check in watch
                    trust_direct_check=False,
                    dry_run=False,
                )
                cmd_sample_probe(ns)
            if st["abnormal"] > 0 and args.auto_purge_abnormal:
                cmd_purge_abnormal(argparse.Namespace(max=min(200, st["abnormal"]), dry_run=False))
        except KeyboardInterrupt:
            log("watch stopped")
            return 0
        except Exception as e:
            log(f"watch error: {e}")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K12 pool ops")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    p_s = sub.add_parser("sample-probe")
    p_s.add_argument("--n", type=int, default=20)
    p_s.add_argument("--status", default="normal")
    p_s.add_argument("--proxy", default="http://127.0.0.1:7897")
    p_s.add_argument("--sleep", type=float, default=0.3)
    p_s.add_argument("--disable-dead", action="store_true")
    p_s.add_argument("--trust-direct-check", action="store_true",
                     help="allow disable based on direct /accounts/check (unsafe for shared K12 snapshots)")
    p_s.add_argument("--dry-run", action="store_true")

    p_d = sub.add_parser("disable-dead")
    p_d.add_argument("--n", type=int, default=30)
    p_d.add_argument("--status", default="normal")
    p_d.add_argument("--proxy", default="http://127.0.0.1:7897")
    p_d.add_argument("--sleep", type=float, default=0.3)
    p_d.add_argument("--trust-direct-check", action="store_true")
    p_d.add_argument("--dry-run", action="store_true")

    p_p = sub.add_parser("purge-abnormal")
    p_p.add_argument("--max", type=int, default=200)
    p_p.add_argument("--dry-run", action="store_true")

    p_w = sub.add_parser("watch")
    p_w.add_argument("--interval", type=int, default=300)
    p_w.add_argument("--probe-n", type=int, default=10)
    p_w.add_argument("--proxy", default="http://127.0.0.1:7897")
    p_w.add_argument("--sleep", type=float, default=0.2)
    p_w.add_argument("--auto-purge-abnormal", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "sample-probe":
        return cmd_sample_probe(args)
    if args.cmd == "disable-dead":
        return cmd_disable_dead(args)
    if args.cmd == "purge-abnormal":
        return cmd_purge_abnormal(args)
    if args.cmd == "watch":
        return cmd_watch(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
