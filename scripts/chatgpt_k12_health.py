#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Health check for chatgpt_auths/ pool.

Checks each account's plan_type and status via the chatgpt2api gateway API
(or direct backend-api probe). Reports K12/free/dead counts.

Usage:
  python scripts/chatgpt_k12_health.py
  python scripts/chatgpt_k12_health.py --probe  # direct OpenAI API (slower)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTH_DIR = ROOT / "chatgpt_auths"
DEFAULT_GATEWAY = "http://127.0.0.1:8124"
DEFAULT_AUTH_KEY = "k12-pool-local"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ChatGPT K12 pool health check")
    p.add_argument("--gateway", default=DEFAULT_GATEWAY)
    p.add_argument("--auth-key", default=DEFAULT_AUTH_KEY)
    p.add_argument("--probe", action="store_true", help="direct OpenAI API probe (slower)")
    p.add_argument("--proxy", default="")
    args = p.parse_args(argv)

    if args.probe:
        return probe_direct(args.proxy)
    return check_via_gateway(args.gateway, args.auth_key)


def check_via_gateway(gateway: str, auth_key: str) -> int:
    """Use chatgpt2api admin API to get account stats."""
    try:
        req = urllib.request.Request(
            f"{gateway.rstrip('/')}/api/accounts?page=1&page_size=1",
            headers={"Authorization": f"Bearer {auth_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        total = data.get("total", 0)
        print(f"Gateway pool: {total} accounts")

        # Get status breakdown
        for status_filter in ("normal", "limited", "abnormal", "disabled"):
            try:
                req2 = urllib.request.Request(
                    f"{gateway.rstrip('/')}/api/accounts?page=1&page_size=1&status={status_filter}",
                    headers={"Authorization": f"Bearer {auth_key}"},
                )
                with urllib.request.urlopen(req2, timeout=10) as resp2:
                    d = json.loads(resp2.read())
                print(f"  {status_filter}: {d.get('total', 0)}")
            except Exception:
                print(f"  {status_filter}: ?")
        return 0
    except Exception as exc:
        print(f"Gateway not available: {exc}")
        return 1


def probe_direct(proxy: str) -> int:
    """Probe each token directly against OpenAI backend-api."""
    sys.path.insert(0, str(ROOT))
    from chatgpt_k12.token_check import check_account, is_k12

    files = sorted(AUTH_DIR.glob("chatgpt-*.json"))
    if not files:
        print("No accounts to probe.")
        return 0

    k12 = free = err = 0
    for f in files:
        record = json.loads(f.read_text(encoding="utf-8"))
        email = record.get("email", "?")
        token = record.get("access_token", "")
        if not token:
            err += 1
            continue
        try:
            result = check_account(token, proxy_url=proxy)
            plan = result.get("plan_type", "?")
            if is_k12(result):
                k12 += 1
                print(f"  {email}: K12 ✓")
            else:
                free += 1
                print(f"  {email}: {plan}")
        except Exception as exc:
            err += 1
            print(f"  {email}: ERROR {str(exc)[:60]}")
        time.sleep(0.5)

    print(f"\nK12: {k12}, Free: {free}, Error: {err}, Total: {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
