#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""True-refresh CPA files; move only terminal-dead into cpa_auths_dead/.

Soft holds (free-usage-exhausted) stay in live so quota_watch can re-enable.
Default scope is **buffer** (shared imports) to limit token-endpoint load;
use --scope all for a full sweep.

Usage:
  python scripts/hard_purge_pool.py
  python scripts/hard_purge_pool.py --scope buffer --max 400 --workers 12
  python scripts/hard_purge_pool.py --scope all
"""
from __future__ import annotations

import argparse
import concurrent.futures
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

HOLD_REASONS = frozenset(
    {
        "free-usage-exhausted",
        "quota_exhausted",
        "rate_limited",
        "temporary",
        "prefer_buffer",
    }
)
TERMINAL_REASONS = frozenset(
    {
        "refresh_revoked",
        "missing_refresh_token",
        "bad_json",
        "terminal_disabled",
        "invalid_grant",
    }
)


def load_cfg() -> dict[str, Any]:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_proxy(cfg: dict[str, Any]) -> str | None:
    return str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None


def opener_for(proxy: str | None):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    return urllib.request.build_opener()


def _quota_reason(d: dict[str, Any]) -> str:
    qs = d.get("quota_state") or {}
    return str(qs.get("reason") or d.get("disable_reason") or d.get("hold_reason") or "").strip()


def classify_disabled(d: dict[str, Any]) -> str:
    """hold_quota | terminal | probe (disabled+RT but no terminal/hold reason)."""
    reason = _quota_reason(d)
    if reason in HOLD_REASONS or "exhausted" in reason.lower():
        return "hold_quota"
    if reason in TERMINAL_REASONS or "revok" in reason.lower():
        return "terminal"
    if d.get("refresh_token"):
        # Unknown disable — must probe RT before keeping forever as hold
        return "probe"
    return "terminal"


def select_files(scope: str, cfg: dict[str, Any], max_files: int) -> list[Path]:
    files = sorted(LIVE.glob("xai-*.json"), key=lambda p: p.stat().st_mtime)
    if scope == "all":
        chosen = files
    else:
        try:
            from pool_policy import is_own_path

            if scope == "own":
                chosen = [p for p in files if is_own_path(p, cfg)]
            else:  # buffer
                chosen = [p for p in files if not is_own_path(p, cfg)]
        except Exception:
            chosen = files
    if max_files and max_files > 0:
        # Prefer older mtime first within cap (stale buffer first)
        chosen = chosen[: max_files]
    return chosen


def refresh_write(path: Path, d: dict[str, Any], opener) -> tuple[str, str]:
    rt = d.get("refresh_token")
    if not rt:
        return "no_rt", ""
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
            return "empty", ""
        # Never clear a soft quota hold here — only refresh enabled or probe-unknown
        was_hold = classify_disabled(d) == "hold_quota" if d.get("disabled") else False
        d["access_token"] = tok["access_token"]
        if tok.get("refresh_token"):
            d["refresh_token"] = tok["refresh_token"]
        if tok.get("expires_in"):
            exp = datetime.now(timezone.utc) + timedelta(seconds=int(tok["expires_in"]))
            d["expired"] = exp.strftime("%Y-%m-%dT%H:%M:%SZ")
            d["expires_in"] = int(tok["expires_in"])
        d["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not was_hold:
            d["disabled"] = False
            # clear terminal stamps if any
            qs = d.get("quota_state")
            if isinstance(qs, dict) and str(qs.get("reason") or "") in TERMINAL_REASONS:
                d.pop("quota_state", None)
        from pool_policy import atomic_write_json
        atomic_write_json(path, d)
        return "ok", ""
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", "ignore")
        if "revoked" in b or "invalid_grant" in b:
            try:
                d["disabled"] = True
                d["quota_state"] = {
                    **(d.get("quota_state") or {}),
                    "reason": "refresh_revoked",
                    "purged_at": time.time(),
                }
                atomic_write_json(path, d)
            except Exception:
                pass
            return "revoked", b[:120]
        return "http", f"{e.code}:{b[:80]}"
    except Exception as e:
        return "net", str(e)[:100]


def one(path: Path, opener) -> tuple[str, str, str]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return path.name, "bad_json", str(e)[:80]

    if d.get("disabled"):
        kind = classify_disabled(d)
        if kind == "hold_quota":
            return path.name, "hold_quota", _quota_reason(d) or "soft_disabled"
        if kind == "terminal":
            return path.name, "terminal_disabled", _quota_reason(d) or "disabled"
        # probe unknown disabled
        st, msg = refresh_write(path, d, opener)
        if st == "ok":
            return path.name, "ok_probe_reenabled", msg
        if st == "revoked":
            return path.name, "revoked", msg
        if st == "no_rt":
            return path.name, "no_rt", msg
        # network/http on unknown disable → keep as hold (don't thrash)
        return path.name, "hold_quota", f"probe_{st}"

    st, msg = refresh_write(path, d, opener)
    if st == "ok":
        return path.name, "ok", msg
    if st == "revoked":
        return path.name, "revoked", msg
    if st == "no_rt":
        return path.name, "no_rt", msg
    return path.name, st, msg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--scope",
        choices=("buffer", "own", "all"),
        default="",
        help="default: config pool_hard_purge_scope or buffer",
    )
    ap.add_argument("--max", type=int, default=0, help="max files (0=all in scope)")
    ap.add_argument("--workers", type=int, default=0)
    args = ap.parse_args(argv)

    cfg = load_cfg()
    scope = (args.scope or str(cfg.get("pool_hard_purge_scope") or "buffer")).strip().lower()
    if scope not in ("buffer", "own", "all"):
        scope = "buffer"
    max_files = int(args.max or cfg.get("pool_hard_purge_max") or 0)
    workers = int(args.workers or cfg.get("pool_hard_purge_workers") or 12)
    workers = max(1, min(workers, 24))

    DEAD.mkdir(parents=True, exist_ok=True)
    proxy = load_proxy(cfg)
    opener = opener_for(proxy)
    files = select_files(scope, cfg, max_files)
    print(
        f"[*] hard_purge scope={scope} files={len(files)} workers={workers} proxy={proxy}",
        flush=True,
    )
    if not files:
        summary = {
            "stats": {},
            "moved_terminal": 0,
            "scope": scope,
            "files": 0,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        out = ROOT / "logs" / "_hard_purge_report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0

    stats: Counter = Counter()
    move_names: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, p, opener) for p in files]
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            name, st, _msg = fut.result()
            stats[st] += 1
            done += 1
            if st in ("revoked", "no_rt", "bad_json", "terminal_disabled"):
                move_names.append(name)
            if done % 100 == 0:
                print(f"[*] progress {done}/{len(files)} stats={dict(stats)}", flush=True)

    moved = 0
    for name in move_names:
        src = LIVE / name
        if not src.is_file():
            continue
        dest = DEAD / name
        if dest.exists():
            dest = DEAD / f"{src.stem}.{int(time.time())}{src.suffix}"
        try:
            shutil.move(str(src), str(dest))
            moved += 1
        except Exception:
            pass

    try:
        from pool_policy import is_own_path

        live_own = sum(
            1
            for f in LIVE.glob("xai-*.json")
            if is_own_path(f, cfg)
            and not json.loads(f.read_text(encoding="utf-8")).get("disabled")
        )
    except Exception:
        live_own = -1

    summary = {
        "stats": dict(stats),
        "moved_terminal": moved,
        "scope": scope,
        "files_scanned": len(files),
        "live_left": len(list(LIVE.glob("xai-*.json"))),
        "dead_total": len(list(DEAD.glob("xai-*.json"))),
        "live_own_enabled_est": live_own,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": "hold free-usage; probe unknown disabled; default scope=buffer",
    }
    out = ROOT / "logs" / "_hard_purge_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # stamp for maintain interval
    stamp = ROOT / "logs" / "_hard_purge_last.json"
    stamp.write_text(
        json.dumps({"ts": time.time(), "iso": summary["ts"], "scope": scope}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
