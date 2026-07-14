#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import real K12 accounts from sub2api zip into chatgpt2api gateway.

Reads all part JSON files from the zip, flattens credentials to top-level
(what POST /api/accounts expects), and batch-imports via HTTP.

Usage:
  python chatgpt_k12/import_real_k12.py D:/Downloads/sub2api...zip
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

GATEWAY = "http://127.0.0.1:8124"
AUTH_KEY = "k12-pool-local"
BATCH_SIZE = 500


def flatten_account(acct: dict) -> dict:
    """sub2api format → flat dict for POST /api/accounts."""
    creds = acct.get("credentials", {})
    if not isinstance(creds, dict):
        creds = {}
    return {
        "access_token": creds.get("access_token", ""),
        "email": creds.get("email", ""),
        "user_id": creds.get("chatgpt_user_id", ""),
        "account_id": creds.get("chatgpt_account_id", ""),
        "chatgpt_account_id": creds.get("chatgpt_account_id", ""),
        "type": creds.get("plan_type", "k12"),
        "plan_type": creds.get("plan_type", "k12"),
        "source_type": "sub2api",
        "model_mapping": creds.get("model_mapping", {}),
        "id_token": creds.get("id_token", ""),
        "expires_at": creds.get("expires_at"),
        "status": "正常",
    }


def post_batch(accounts: list[dict]) -> dict:
    url = f"{GATEWAY}/api/accounts"
    payload = json.dumps({
        "accounts": accounts,
        "refresh": False,
        "return_items": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AUTH_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return {"error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"error": str(e)}


def main() -> int:
    zip_path = sys.argv[1] if len(sys.argv) > 1 else \
        "D:/Downloads/sub2api_20260714_115553_80500_front500_split+(2).zip"

    print(f"Loading {zip_path} ...")
    z = zipfile.ZipFile(zip_path)
    names = sorted([n for n in z.namelist() if n.endswith(".json") and not n.startswith("__MACOSX")])
    print(f"Parts: {len(names)}")

    all_flat: list[dict] = []
    for n in names:
        data = json.loads(z.read(n).decode("utf-8"))
        for acct in data.get("accounts", []):
            f = flatten_account(acct)
            if f.get("access_token"):
                all_flat.append(f)

    print(f"Total real accounts to import: {len(all_flat)}")

    batches = [all_flat[i:i + BATCH_SIZE] for i in range(0, len(all_flat), BATCH_SIZE)]
    total_added = 0
    total_skipped = 0
    errors: list[str] = []
    t0 = time.time()

    for i, batch in enumerate(batches, 1):
        result = post_batch(batch)
        if "error" in result:
            errors.append(f"Batch {i}: {result['error']}")
            print(f"  Batch {i}/{len(batches)}: ERROR {result['error'][:80]}")
        else:
            added = result.get("added", 0)
            skipped = result.get("skipped", 0)
            total_added += added
            total_skipped += skipped
            elapsed = time.time() - t0
            rate = total_added / elapsed if elapsed > 0 else 0
            print(f"  Batch {i}/{len(batches)}: +{added} skip={skipped} "
                  f"| cum {total_added} added, {total_skipped} skipped "
                  f"| {rate:.0f}/s | {elapsed:.0f}s")

    elapsed = time.time() - t0
    print(f"\n=== IMPORT COMPLETE ===")
    print(f"Added:   {total_added}")
    print(f"Skipped: {total_skipped}")
    print(f"Errors:  {len(errors)}")
    for e in errors[:5]:
        print(f"  {e}")
    print(f"Elapsed: {elapsed:.1f}s")

    # Verify
    try:
        req = urllib.request.Request(
            f"{GATEWAY}/api/accounts?page=1&page_size=1",
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            verify = json.loads(resp.read())
        print(f"\nGateway total accounts: {verify.get('total', '?')}")
    except Exception as e:
        print(f"Verify failed: {e}")

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
