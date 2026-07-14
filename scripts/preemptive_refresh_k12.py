#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh all K12 tokens in chatgpt_auths/ using their refresh_tokens.

Tokens expire; this script refreshes them via POST /oauth/token before they
expire. Mirrors preemptive_refresh.py pattern from the Grok pool.

Usage:
  python scripts/preemptive_refresh_k12.py
  python scripts/preemptive_refresh_k12.py --proxy http://127.0.0.1:7897
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTH_DIR = ROOT / "chatgpt_auths"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Refresh K12 tokens")
    p.add_argument("--proxy", default="", help="proxy URL for OpenAI requests")
    p.add_argument("--dry-run", action="store_true", help="just report expiry status")
    args = p.parse_args(argv)

    from chatgpt_k12.register import ChatGPTRegistrar

    files = sorted(AUTH_DIR.glob("chatgpt-*.json"))
    if not files:
        print("No accounts to refresh.")
        return 0

    now = int(time.time())
    refreshed = 0
    skipped = 0
    failed = 0

    registrar = ChatGPTRegistrar({})

    for f in files:
        record = json.loads(f.read_text(encoding="utf-8"))
        email = record.get("email", "?")
        rt = record.get("refresh_token", "")
        exp = record.get("expires_at", 0)

        # Refresh if expiring within 2 hours or already expired
        needs_refresh = (exp - now) < 7200 if exp else True

        if args.dry_run:
            status = "EXPIRED" if exp < now else f"expires in {(exp - now) // 3600}h"
            print(f"  {email}: {status}")
            if needs_refresh:
                skipped += 1
            continue

        if not needs_refresh:
            continue

        if not rt:
            print(f"  {email}: no refresh_token, skip")
            failed += 1
            continue

        try:
            tokens = registrar.refresh_token(rt, proxy_url=args.proxy)
            record["access_token"] = tokens.get("access_token", record["access_token"])
            new_rt = tokens.get("refresh_token", "")
            if new_rt:
                record["refresh_token"] = new_rt
            if tokens.get("id_token"):
                record["id_token"] = tokens["id_token"]
            record["expires_at"] = tokens.get("expires_at", now + 864000)
            f.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            refreshed += 1
            print(f"  {email}: refreshed ✓")
        except Exception as exc:
            failed += 1
            print(f"  {email}: FAILED {str(exc)[:60]}")

        time.sleep(1)

    print(f"\n=== REFRESH COMPLETE ===")
    print(f"Refreshed: {refreshed}")
    print(f"Skipped:   {skipped}")
    print(f"Failed:    {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
