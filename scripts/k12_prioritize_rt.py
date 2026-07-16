#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prefer RT-capable accounts; soft-disable k12 snapshots without RT.

IMPORTANT: never edit accounts.db directly — the running gateway holds an
in-memory copy and its periodic flush will overwrite direct sqlite writes.
Always go through the gateway HTTP API (/api/accounts/batch-update).

Usage:
  python scripts/k12_prioritize_rt.py            # dry-run stats
  python scripts/k12_prioritize_rt.py --apply    # batch-update status=禁用
  python scripts/k12_prioritize_rt.py --apply --restore  # set 正常 again
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

DB = Path(r"D:/Users/grok-auto-register/chatgpt2api/data/accounts.db")
GATEWAY = "http://127.0.0.1:8124"
AUTH_KEY = "k12-pool-local"
BATCH = 200


def http_json(method: str, path: str, body: dict | None = None, timeout: float = 60.0):
    data = None
    headers = {"Authorization": f"Bearer {AUTH_KEY}"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{GATEWAY.rstrip('/')}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw[:200]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:200]
    except Exception as e:
        return 0, str(e)


def collect() -> tuple[dict[str, int], list[str]]:
    stats = {"k12_no_rt": 0, "k12_rt": 0, "go_rt": 0, "plus_rt": 0, "team": 0, "other": 0}
    k12_no_rt_tokens: list[str] = []
    c = sqlite3.connect(str(DB))
    for _, data in c.execute("SELECT id, data FROM accounts"):
        try:
            j = json.loads(data)
        except Exception:
            continue
        plan = str(j.get("plan_type") or j.get("type") or "").strip().lower()
        rt = bool(str(j.get("refresh_token") or "").strip())
        st = str(j.get("status") or "")
        if plan == "k12" and not rt:
            stats["k12_no_rt"] += 1
            if st in ("正常", "限流") and j.get("access_token"):
                k12_no_rt_tokens.append(str(j["access_token"]))
        elif plan == "k12" and rt:
            stats["k12_rt"] += 1
        elif plan == "go" and rt:
            stats["go_rt"] += 1
        elif plan == "plus" and rt:
            stats["plus_rt"] += 1
        elif plan == "team":
            stats["team"] += 1
        else:
            stats["other"] += 1
    c.close()
    return stats, k12_no_rt_tokens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="disable no-RT k12 via gateway API")
    ap.add_argument("--restore", action="store_true", help="set them back to 正常")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not DB.is_file():
        raise SystemExit(f"missing {DB}")
    stats, tokens = collect()
    print("stats", stats, "k12_no_rt_actionable", len(tokens))
    if not args.apply:
        print("dry-run only; pass --apply to batch-update via gateway API")
        return 0

    target_status = "正常" if args.restore else "禁用"
    # for restore, read currently-disabled k12-no-rt from DB
    if args.restore:
        c = sqlite3.connect(str(DB))
        tokens = []
        for _, data in c.execute("SELECT id, data FROM accounts"):
            try:
                j = json.loads(data)
            except Exception:
                continue
            plan = str(j.get("plan_type") or j.get("type") or "").strip().lower()
            rt = bool(str(j.get("refresh_token") or "").strip())
            if plan == "k12" and not rt and str(j.get("status") or "") == "禁用" and j.get("access_token"):
                tokens.append(str(j["access_token"]))
        c.close()
        print("restore candidates", len(tokens))

    if args.limit:
        tokens = tokens[: args.limit]
    done = errors = 0
    for i in range(0, len(tokens), BATCH):
        batch = tokens[i : i + BATCH]
        code, body = http_json(
            "POST", "/api/accounts/batch-update",
            {"access_tokens": batch, "status": target_status},
        )
        if code == 200:
            done += len(batch)
            print(f"batch {i // BATCH + 1}: +{len(batch)} -> {target_status}")
        else:
            errors += 1
            print(f"batch {i // BATCH + 1}: ERROR {code} {str(body)[:160]}")
        time.sleep(0.2)
    print(f"done={done} errors={errors} status={target_status}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
