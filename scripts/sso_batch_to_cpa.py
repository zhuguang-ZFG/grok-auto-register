#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch convert SSO tokens to CPA (xai OAuth) format.

Reads SSO tokens from:
  - output.zip (email----password----sso_token lines)
  - directory of .txt files
  - single .txt file

Converts each SSO → access_token/refresh_token via protocol_mint,
writes to cpa_auths/ like the register pipeline.

Usage:
  python scripts/sso_batch_to_cpa.py "D:/Downloads/output.zip" --sample 20
  python scripts/sso_batch_to_cpa.py "D:/Downloads/output.zip" --concurrency 3
  python scripts/sso_batch_to_cpa.py "D:/Downloads/output.zip" --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEST = ROOT / "cpa_auths"
LOG = ROOT / "logs" / "sso_batch_cpa.log"
PROXY = os.environ.get("SSO_BATCH_PROXY", "http://127.0.0.1:7897")


def log(msg: str) -> None:
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def parse_sso_lines(text: str) -> list[dict[str, str]]:
    """Parse email----password----sso_token format."""
    results: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("----")
        if len(parts) >= 3:
            email = parts[0].strip()
            password = parts[1].strip()
            sso = parts[-1].strip()
            if sso.startswith("eyJ") and len(sso) > 40:
                results.append({"email": email, "password": password, "sso": sso})
        elif len(parts) == 1 and parts[0].startswith("eyJ") and len(parts[0]) > 40:
            results.append({"email": "", "password": "", "sso": parts[0]})
    return results


def load_sso(path: Path) -> list[dict[str, str]]:
    """Load SSO tokens from zip/dir/file."""
    all_tokens: list[dict[str, str]] = []
    seen: set[str] = set()

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if not n.endswith(".txt"):
                    continue
                text = z.read(n).decode("utf-8", errors="replace")
                for item in parse_sso_lines(text):
                    if item["sso"] not in seen:
                        seen.add(item["sso"])
                        all_tokens.append(item)
    elif path.is_dir():
        for f in sorted(path.rglob("*.txt")):
            text = f.read_text(encoding="utf-8", errors="replace")
            for item in parse_sso_lines(text):
                if item["sso"] not in seen:
                    seen.add(item["sso"])
                    all_tokens.append(item)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        for item in parse_sso_lines(text):
            if item["sso"] not in seen:
                seen.add(item["sso"])
                all_tokens.append(item)

    return all_tokens


def mint_one(item: dict[str, str], timeout: float = 120) -> dict[str, Any]:
    """Convert one SSO → CPA via protocol_mint."""
    from cpa_xai.protocol_mint import mint_with_sso_protocol, ProtocolMintError

    sso = item["sso"]
    email = item.get("email", "")
    try:
        tokens = mint_with_sso_protocol(
            sso_cookie=sso,
            email=email,
            proxy=PROXY,
            timeout=30.0,
            poll_timeout_sec=90.0,
        )
        access = str(tokens.get("access_token") or "").strip()
        refresh = str(tokens.get("refresh_token") or "").strip()
        if not access:
            return {"ok": False, "email": email, "error": "no access_token", "sso": sso[:20]}

        # Write CPA file like register pipeline
        from cpa_xai.schema import CLIENT_ID
        from datetime import datetime, timezone, timedelta

        expires_in = int(tokens.get("expires_in") or 21600)
        now = datetime.now(timezone.utc)
        expired = now + timedelta(seconds=expires_in)

        cpa = {
            "type": "xai",
            "auth_kind": "oauth",
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "expired": expired.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_refresh": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "email": email,
            "sub": str(tokens.get("sub") or ""),
            "base_url": "https://cli-chat-proxy.grok.com/v1",
            "token_endpoint": "https://auth.x.ai/oauth2/token",
            "redirect_uri": "http://127.0.0.1:56121/callback",
            "disabled": False,
            "headers": {
                "x-grok-client-version": "0.2.93",
                "x-xai-token-auth": "xai-grok-cli",
                "x-authenticateresponse": "authenticate-response",
                "x-grok-client-identifier": "grok-shell",
                "User-Agent": "grok-shell/0.2.93 (linux; x86_64)",
            },
            "id_token": str(tokens.get("id_token") or ""),
        }

        # Dedupe by access_token
        fname = f"xai-{email}" if email else f"xai-{access[:16]}"
        out = DEST / f"{fname}.json"
        if out.exists():
            return {"ok": True, "email": email, "action": "skip_dup", "path": str(out)}

        DEST.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cpa, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "email": email, "action": "minted", "path": str(out)}

    except ProtocolMintError as e:
        return {"ok": False, "email": email, "error": f"protocol: {e}", "sso": sso[:20]}
    except Exception as e:
        return {"ok": False, "email": email, "error": str(e)[:200], "sso": sso[:20]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Batch SSO → CPA converter")
    p.add_argument("path", help="zip/dir/file with SSO tokens")
    p.add_argument("--sample", type=int, default=0, help="only process N tokens (0=all)")
    p.add_argument("--concurrency", type=int, default=3, help="parallel workers")
    p.add_argument("--dry-run", action="store_true", help="only parse, don't mint")
    p.add_argument("--timeout", type=float, default=120, help="per-token timeout")
    args = p.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"not found: {path}")
        return 1

    tokens = load_sso(path)
    log(f"loaded {len(tokens)} unique SSO tokens from {path}")

    if args.sample > 0:
        tokens = tokens[: args.sample]
        log(f"sample mode: processing {len(tokens)}")

    if args.dry_run:
        for t in tokens[:10]:
            log(f"  email={t['email']} sso={t['sso'][:30]}...")
        log(f"dry-run: {len(tokens)} tokens would be processed")
        return 0

    ok = fail = skip = 0
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(mint_one, t, args.timeout): t for t in tokens}
        for i, fut in enumerate(as_completed(futures), 1):
            item = futures[fut]
            try:
                result = fut.result(timeout=args.timeout + 30)
            except Exception as e:
                result = {"ok": False, "email": item.get("email", ""), "error": str(e)[:200]}

            if result.get("ok"):
                if result.get("action") == "skip_dup":
                    skip += 1
                else:
                    ok += 1
            else:
                fail += 1
                errors.append(f"{result.get('email','?')}: {result.get('error','?')}")

            if i % 50 == 0 or i == len(tokens):
                log(f"progress {i}/{len(tokens)} ok={ok} fail={fail} skip={skip}")

    log(f"done: total={len(tokens)} ok={ok} fail={fail} skip={skip}")
    if errors:
        log(f"errors (first 10):")
        for e in errors[:10]:
            log(f"  {e}")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
