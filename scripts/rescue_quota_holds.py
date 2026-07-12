#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rescue soft-hold accounts mis-moved into cpa_auths_dead/.

Moves free-usage-exhausted (and similar) files with a refresh_token back to
cpa_auths/, keeping disabled=true so CLIProxy skips them until recover_after.
Optionally re-enables when recover_after has passed and RT still refreshes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "cpa_auths"
DEAD = ROOT / "cpa_auths_dead"
CLIENT = "b1a00492-073a-47ea-816f-4c329264a828"
OWN = {"zhuguang.ccwu.cc", "lima.cc.cd", "zhuguang.de5.net", "baoxia.top"}
HOLD_MARKERS = ("exhausted", "free-usage", "rate_limit", "temporary")


def load_proxy() -> str | None:
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None
    except Exception:
        return "http://127.0.0.1:7897"


def is_hold(d: dict[str, Any]) -> bool:
    qs = d.get("quota_state") or {}
    reason = str(qs.get("reason") or d.get("disable_reason") or "").lower()
    if any(m in reason for m in HOLD_MARKERS):
        return True
    # disabled + has RT + not stamped revoked → treat as soft hold candidate
    if d.get("disabled") and d.get("refresh_token"):
        if "revok" in reason or reason in ("refresh_revoked", "invalid_grant", "missing_refresh_token"):
            return False
        if reason:
            return "exhaust" in reason or "quota" in reason
    return False


def domain_of(d: dict[str, Any], name: str) -> str:
    em = d.get("email") or ""
    if "@" in em:
        return em.split("@")[-1].lower()
    if "@" in name:
        return name.split("@", 1)[-1].replace(".json", "").split(".")[0:3]
    # filename xai-user@domain.json or with timestamp suffix
    import re

    m = re.search(r"@([^/\\]+?)(?:\.\d+)?\.json$", name)
    return (m.group(1) if m else "?").lower()


def try_refresh(d: dict[str, Any], opener) -> bool:
    rt = d.get("refresh_token")
    if not rt:
        return False
    body = (
        f"grant_type=refresh_token&refresh_token={rt}&client_id={CLIENT}"
    ).encode()
    req = urllib.request.Request(
        "https://auth.x.ai/oauth2/token",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "grok-shell/0.2.93",
        },
    )
    try:
        with opener.open(req, timeout=20) as r:
            tok = json.loads(r.read())
        if not tok.get("access_token"):
            return False
        d["access_token"] = tok["access_token"]
        if tok.get("refresh_token"):
            d["refresh_token"] = tok["refresh_token"]
        if tok.get("expires_in"):
            exp = datetime.now(timezone.utc) + timedelta(seconds=int(tok["expires_in"]))
            d["expired"] = exp.strftime("%Y-%m-%dT%H:%M:%SZ")
            d["expires_in"] = int(tok["expires_in"])
        d["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rescue soft quota holds from dead dir")
    ap.add_argument("--own-only", action="store_true", help="only own 4 domains")
    ap.add_argument(
        "--reenable-ready",
        action="store_true",
        help="if recover_after passed and RT ok, clear disabled",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=0)
    args = ap.parse_args(argv)

    LIVE.mkdir(parents=True, exist_ok=True)
    DEAD.mkdir(parents=True, exist_ok=True)
    proxy = load_proxy()
    opener = (
        urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        if proxy
        else urllib.request.build_opener()
    )
    now = time.time()
    stats: Counter = Counter()
    rescued = 0
    reenabled = 0

    files = sorted(DEAD.glob("xai-*.json"))
    for f in files:
        if args.max and rescued + stats["skip"] >= args.max:
            # still count only actions; simpler: break after max rescues
            pass
        if args.max and rescued >= args.max:
            break
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            stats["bad_json"] += 1
            continue
        if not d.get("refresh_token"):
            stats["no_rt"] += 1
            continue
        if not is_hold(d):
            stats["not_hold"] += 1
            continue
        dm = domain_of(d, f.name)
        # normalize domain from email
        em = d.get("email") or ""
        if "@" in em:
            dm = em.split("@")[-1].lower()
        if args.own_only and dm not in OWN:
            stats["skip_buffer"] += 1
            continue

        dest = LIVE / f.name
        # strip timestamp suffix duplicates: xai-foo@bar.com.1783.json → prefer clean name
        if dest.exists() or ".1" in f.stem:
            # if live already has same email, skip
            live_hit = False
            email = (d.get("email") or "").lower()
            if email:
                for lf in LIVE.glob("xai-*.json"):
                    try:
                        ld = json.loads(lf.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if (ld.get("email") or "").lower() == email:
                        live_hit = True
                        break
            if live_hit:
                stats["dup_live"] += 1
                continue
            if dest.exists():
                dest = LIVE / f"{f.stem}.rescued{f.suffix}"

        # keep soft hold unless reenable
        d["disabled"] = True
        qs = dict(d.get("quota_state") or {})
        if not qs.get("reason"):
            qs["reason"] = "free-usage-exhausted"
        qs["rescued_from_dead_at"] = now
        d["quota_state"] = qs

        do_reenable = False
        if args.reenable_ready:
            ra = float(qs.get("recover_after") or 0)
            if ra and ra <= now:
                if try_refresh(d, opener):
                    d["disabled"] = False
                    qs["reenabled_at"] = now
                    d["quota_state"] = qs
                    do_reenable = True
                else:
                    stats["reenable_rt_fail"] += 1

        if args.dry_run:
            stats["would_rescue"] += 1
            if do_reenable:
                stats["would_reenable"] += 1
            continue

        dest.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            f.unlink()
        except Exception:
            # leave dead copy if unlink fails
            stats["unlink_fail"] += 1
        rescued += 1
        if do_reenable:
            reenabled += 1
        stats["rescued"] += 1
        if rescued % 50 == 0:
            print(f"[*] rescued={rescued} reenabled={reenabled}", flush=True)

    summary = {
        "rescued": rescued,
        "reenabled": reenabled,
        "stats": dict(stats),
        "live": len(list(LIVE.glob("xai-*.json"))),
        "dead": len(list(DEAD.glob("xai-*.json"))),
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "own_only": bool(args.own_only),
        "dry_run": bool(args.dry_run),
    }
    out = ROOT / "logs" / "_rescue_quota_holds.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
