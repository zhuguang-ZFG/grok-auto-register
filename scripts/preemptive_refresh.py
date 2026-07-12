#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-emptive token refresh: refresh CPA files before access_token expires.

Runs as a scheduled task (every 1h) so CLIProxy never sees a pool of
expired-but-still-valid-RT files. CLIProxy's own auto-refresh covers
in-flight expiry; this covers the gap when CLIProxy restarts or when
its refresh worker is overwhelmed.

Usage:
  python scripts/preemptive_refresh.py          # refresh within 2h of expiry
  python scripts/preemptive_refresh.py --within-hours 3 --workers 8
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from cpa_xai.schema import CLIENT_ID as CLIENT
except Exception:  # pragma: no cover
    CLIENT = "b1a00492-073a-47ea-816f-4c329264a828"


def load_cfg() -> dict:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def jwt_exp(token: str) -> float:
    """Extract exp from JWT without external deps."""
    import base64

    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0
        seg = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(seg))
        return float(claims.get("exp") or 0)
    except Exception:
        return 0


def needs_refresh(path: Path, within_sec: float) -> bool:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if d.get("disabled"):
        return False
    if not d.get("refresh_token"):
        return False
    at = str(d.get("access_token") or "")
    exp = jwt_exp(at)
    if not exp:
        # Can't parse JWT exp → refresh to be safe
        return True
    return exp <= time.time() + within_sec


def refresh_one(path: Path, proxy: str | None) -> tuple[str, str]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return path.name, f"bad_json:{e}"
    rt = d.get("refresh_token")
    if not rt:
        return path.name, "no_rt"
    body = f"grant_type=refresh_token&refresh_token={rt}&client_id={CLIENT}".encode()
    req = urllib.request.Request(
        "https://auth.x.ai/oauth2/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    ) if proxy else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=20) as r:
            tok = json.loads(r.read())
        if not tok.get("access_token"):
            return path.name, "empty_response"
        d["access_token"] = tok["access_token"]
        if tok.get("refresh_token"):
            d["refresh_token"] = tok["refresh_token"]
        if tok.get("expires_in"):
            exp = datetime.now(timezone.utc) + timedelta(seconds=int(tok["expires_in"]))
            d["expired"] = exp.strftime("%Y-%m-%dT%H:%M:%SZ")
            d["expires_in"] = int(tok["expires_in"])
        d["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Do NOT blindly clear disabled — if quota_watch set it for free-usage,
        # we must respect that until recover_after. Only re-enable if the file
        # was NOT a soft hold (no quota_state.reason / hold_reason).
        qs = d.get("quota_state") or {}
        reason = str(qs.get("reason") or d.get("hold_reason") or "")
        if not reason or reason in ("refresh_revoked", "missing_refresh_token", "bad_json"):
            # Was marked terminal by us but RT actually works → genuine recovery
            d["disabled"] = False
            if "quota_state" in d:
                d.pop("quota_state", None)
        # else: keep disabled=true (quota cooling / prefer_buffer hold)
        from pool_policy import atomic_write_json
        atomic_write_json(path, d)
        return path.name, "ok"
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", "ignore")
        if "revoked" in b or "invalid_grant" in b:
            # Mark disabled so CLIProxy skips it (hard_purge will move later)
            d["disabled"] = True
            qs = d.get("quota_state") or {}
            qs["reason"] = "refresh_revoked"
            d["quota_state"] = qs
            try:
                atomic_write_json(path, d)
            except Exception:
                pass
            return path.name, "revoked"
        return path.name, f"http_{e.code}"
    except Exception as e:
        return path.name, f"net:{str(e)[:60]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-dir", default=str(ROOT / "cpa_auths"))
    ap.add_argument("--within-hours", type=float, default=2.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max", type=int, default=0, help="0=all needing refresh")
    args = ap.parse_args(argv)

    cfg = load_cfg()
    proxy = str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None
    auth = Path(args.auth_dir)
    if not auth.is_absolute():
        auth = (ROOT / auth).resolve()

    within_sec = float(args.within_hours) * 3600
    candidates = [p for p in auth.glob("xai-*.json") if needs_refresh(p, within_sec)]
    if args.max and len(candidates) > args.max:
        candidates = candidates[: args.max]

    print(
        f"[*] preemptive_refresh within={args.within_hours}h "
        f"candidates={len(candidates)} workers={args.workers} proxy={proxy}",
        flush=True,
    )
    if not candidates:
        print("[*] nothing to refresh")
        return 0

    workers = max(1, min(int(args.workers), 16))
    ok = fail = revoked = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(refresh_one, p, proxy): p for p in candidates}
        done = 0
        for fut in as_completed(futs):
            _name, status = fut.result()
            done += 1
            if status == "ok":
                ok += 1
            elif status == "revoked":
                revoked += 1
            else:
                fail += 1
            if done % 100 == 0:
                print(f"[*] {done}/{len(candidates)} ok={ok} revoked={revoked} fail={fail}", flush=True)

    summary = {
        "candidates": len(candidates),
        "ok": ok,
        "revoked": revoked,
        "fail": fail,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    out = ROOT / "logs" / "_preemptive_refresh_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    from pool_policy import atomic_write_json
    atomic_write_json(out, summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
