#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Delayed recheck of Kiro-Go accounts (banStatus / chat).

Community: after Builder signup, avoid hammering ListAvailableModels immediately.
This script waits, then probes chat once per account via the gateway.

Usage:
  python scripts/kiro_recheck_accounts.py --wait-min 30
  python scripts/kiro_recheck_accounts.py --wait-min 0 --base-url http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "side_pools" / "kiro-go" / "data" / "config.json"


def http_json(method: str, url: str, headers: dict | None = None, body: dict | None = None, timeout: float = 60):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--wait-min", type=float, default=0, help="sleep minutes before probe")
    ap.add_argument("--model", default="claude-sonnet-4.5")
    args = ap.parse_args()

    if args.wait_min > 0:
        sec = int(args.wait_min * 60)
        print(f"sleep {sec}s before recheck...")
        time.sleep(sec)

    if CFG.is_file():
        cfg = json.loads(CFG.read_text(encoding="utf-8"))
        accs = cfg.get("accounts") or []
        print(f"accounts_on_disk={len(accs)}")
        for a in accs:
            print(
                f"  {a.get('email')} enabled={a.get('enabled')} "
                f"ban={a.get('banStatus')} method={a.get('authMethod')}"
            )
    else:
        print("no config yet")

    code, body = http_json(
        "POST",
        f"{args.base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": "Bearer any"},
        body={
            "model": args.model,
            "messages": [{"role": "user", "content": "reply with: pong"}],
            "max_tokens": 8,
            "stream": False,
        },
        timeout=90,
    )
    snippet = json.dumps(body, ensure_ascii=False)[:240] if not isinstance(body, str) else body[:240]
    print(f"chat http={code} {snippet}")
    if code == 200:
        print("RECHECK_OK: gateway chat usable")
        return 0
    if code == 503:
        print("empty pool or all disabled")
        return 2
    if "suspended" in snippet.lower() or code in (403, 500):
        print("still suspended / upstream 403 — need cleaner IP or social path")
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
