#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe / soft-disable terminal CPA files. Moving to dead is opt-in.

Policy (2026-07-15, after pool collapse incident):
  - Default **dirty_only**: only disabled / missing RT (never mass-refresh healthy).
  - Default **no move**: terminal → soft-disable in place (`disabled=true` + reason).
  - Soft holds (free-usage-exhausted / prefer_buffer) never terminal.
  - `--move-dead` required to shutil.move into cpa_auths_dead/ (manual only).
  - `--refresh-all` required to touch non-disabled files (dangerous).

Usage:
  python scripts/hard_purge_pool.py
  python scripts/hard_purge_pool.py --scope buffer --max 400 --workers 8
  python scripts/hard_purge_pool.py --scope all --move-dead   # explicit
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
try:
    from cpa_xai.schema import CLIENT_ID as CLIENT
except Exception:  # pragma: no cover
    CLIENT = "b1a00492-073a-47ea-816f-4c329264a828"

HOLD_REASONS = frozenset(
    {
        "free-usage-exhausted",
        "quota_exhausted",
        "rate_limited",
        "temporary",
        "prefer_buffer",
        # soft hold: retried on a 24h window by usage.reenable_recovered_accounts;
        # skip here so we don't burn an RT rotation on accounts whose chat gate
        # has not lifted yet (refresh cannot clear a chat-side 403).
        "permission-denied",
        "permission_denied",
    }
)
TERMINAL_REASONS = frozenset(
    {
        "refresh_revoked",
        "missing_refresh_token",
        "bad_json",
        "terminal_disabled",
        "invalid_grant",
        # permission-denied is NOT terminal: the new-account chat gate
        # self-heals over days (verified 2026-07-16, see cpa_xai/usage.py).
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
    rl = reason.lower()
    if reason in HOLD_REASONS or "exhausted" in rl:
        return "hold_quota"
    # domain_dead:* / revoked — never recover by refresh.
    # permission-denied deliberately excluded: soft-disabled, retried on a
    # 24h window (self-heals); sending it through "probe" lets a successful
    # RT refresh + re-enable pick it back up.
    if (
        reason in TERMINAL_REASONS
        or "revok" in rl
        or "domain_dead" in rl
        or "invalid_grant" in rl
    ):
        return "terminal"
    if d.get("refresh_token"):
        # Unknown disable — must probe RT before keeping forever as hold
        return "probe"
    return "terminal"


def select_files(
    scope: str,
    cfg: dict[str, Any],
    max_files: int,
    *,
    dirty_only: bool = True,
) -> list[Path]:
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
    if dirty_only:
        # Default: only disabled / missing RT. Never mass-refresh healthy pool
        # (full refresh storms cause rate-limit + false revoked + pool collapse).
        dirty: list[Path] = []
        for p in chosen:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                dirty.append(p)
                continue
            if d.get("disabled") or not d.get("refresh_token"):
                dirty.append(p)
        chosen = dirty
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
            # Rotation-race guard: another refresher may have just rotated this
            # RT. Re-read the file; if the on-disk RT changed, account is alive.
            from cpa_xai.raceguard import rt_rotated_by_other

            if rt_rotated_by_other(path, rt):
                return "rotated_skip", ""
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
    ap.add_argument(
        "--refresh-all",
        action="store_true",
        help="also refresh non-disabled files (dangerous; can mass-revoke under rate limit)",
    )
    ap.add_argument(
        "--move-dead",
        action="store_true",
        help="physically move terminal files to cpa_auths_dead/ (OFF by default)",
    )
    args = ap.parse_args(argv)

    cfg = load_cfg()
    scope = (args.scope or str(cfg.get("pool_hard_purge_scope") or "buffer")).strip().lower()
    if scope not in ("buffer", "own", "all"):
        scope = "buffer"
    # argparse 0 is valid "unlimited"; do not use `or` (0 is falsy)
    if args.max is not None and args.max > 0:
        max_files = int(args.max)
    else:
        max_files = int(cfg.get("pool_hard_purge_max") or 0)
    workers = int(args.workers or cfg.get("pool_hard_purge_workers") or 12)
    workers = max(1, min(workers, 24))
    dirty_only = not bool(args.refresh_all)
    # Config can force move off even if CLI passes --move-dead? Prefer CLI OR config opt-in.
    move_dead = bool(args.move_dead) or bool(cfg.get("pool_hard_purge_move_dead"))

    DEAD.mkdir(parents=True, exist_ok=True)
    proxy = load_proxy(cfg)
    opener = opener_for(proxy)
    files = select_files(scope, cfg, max_files, dirty_only=dirty_only)
    print(
        f"[*] hard_purge scope={scope} dirty_only={dirty_only} move_dead={move_dead} "
        f"files={len(files)} workers={workers} proxy={proxy}",
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
    terminal_names: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, p, opener) for p in files]
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            name, st, _msg = fut.result()
            stats[st] += 1
            done += 1
            if st in ("revoked", "no_rt", "bad_json", "terminal_disabled"):
                terminal_names.append(name)
            if done % 100 == 0:
                print(f"[*] progress {done}/{len(files)} stats={dict(stats)}", flush=True)

    # Soft-disable terminal in place (always). Physical move only if move_dead.
    soft_disabled = 0
    for name in terminal_names:
        src = LIVE / name
        if not src.is_file():
            continue
        try:
            d = json.loads(src.read_text(encoding="utf-8"))
            d["disabled"] = True
            qs = d.get("quota_state") if isinstance(d.get("quota_state"), dict) else {}
            if not qs.get("reason"):
                qs = {
                    **qs,
                    "reason": "terminal_disabled",
                    "soft_purged_at": time.time(),
                }
            d["quota_state"] = qs
            from pool_policy import atomic_write_json

            atomic_write_json(src, d)
            soft_disabled += 1
        except Exception:
            pass

    moved = 0
    if move_dead:
        for name in terminal_names:
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
    else:
        stats["soft_disable_only"] = soft_disabled

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
        "soft_disabled": soft_disabled,
        "move_dead": move_dead,
        "dirty_only": dirty_only,
        "scope": scope,
        "files_scanned": len(files),
        "live_left": len(list(LIVE.glob("xai-*.json"))),
        "dead_total": len(list(DEAD.glob("xai-*.json"))),
        "live_own_enabled_est": live_own,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": "dirty_only; soft-disable terminal; move only with --move-dead",
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
