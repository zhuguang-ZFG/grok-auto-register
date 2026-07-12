#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch-refresh CPA access tokens that are expired or expiring soon.

Uses cpa_xai.oauth_device.refresh_access_token. Dead refresh_tokens can be
moved to cpa_auths_dead/ with --purge-dead.

Usage:
  python refresh_pool.py --within-hours 3 --max 300
  python refresh_pool.py --domain lsw666.dpdns.org --within-hours 6
  python refresh_pool.py --expired-only --purge-dead
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def jwt_exp(token: str) -> int:
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return int(json.loads(base64.urlsafe_b64decode(seg)).get("exp") or 0)
    except Exception:
        return 0


def load_cfg() -> dict[str, Any]:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def needs_refresh(
    path: Path,
    *,
    within_sec: float,
    expired_only: bool,
    include_disabled: bool = False,
) -> bool:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if d.get("disabled") and not include_disabled and not expired_only:
        # skip disabled by default (silent pre-expiry only touches live pool)
        return False
    qs = d.get("quota_state") or {}
    if str(qs.get("reason") or "") in ("refresh_revoked", "missing_refresh_token", "bad_json"):
        return False
    at = str(d.get("access_token") or "")
    exp = jwt_exp(at)
    now = time.time()
    if not exp:
        return True
    if expired_only:
        return exp <= now
    return exp <= now + within_sec


def refresh_one(
    path: Path,
    *,
    proxy: str | None,
    purge_dead: bool,
    dead_dir: Path,
    soft_disable_dead: bool = True,
) -> dict[str, Any]:
    """Refresh one CPA file. Never probes chat; optional soft-disable on dead RT.

    Community (sticky/cache): avoid hard MOVE/unlink — CLIProxy treats
    os.replace churn as REMOVE and breaks session-affinity.
    """
    from cpa_xai.oauth_device import OAuthDeviceError, refresh_access_token
    from cpa_xai.schema import expired_from_access_token

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"file": path.name, "ok": False, "error": f"read:{exc}"}
    # Terminal dead — do not re-hit token endpoint every cycle
    qs = payload.get("quota_state") or {}
    if payload.get("disabled") and str(qs.get("reason") or "") in (
        "refresh_revoked",
        "missing_refresh_token",
        "bad_json",
    ):
        return {"file": path.name, "ok": False, "error": "terminal_disabled", "skipped": True}
    rt = str(payload.get("refresh_token") or "").strip()
    if not rt:
        return {"file": path.name, "ok": False, "error": "no_refresh_token", "dead": True}
    try:
        result = refresh_access_token(rt, proxy=proxy, timeout=20.0, retries=1)
        payload["access_token"] = result.access_token
        payload["refresh_token"] = result.refresh_token or rt
        payload["expires_in"] = getattr(result, "expires_in", payload.get("expires_in", 21600))
        if getattr(result, "id_token", None):
            payload["id_token"] = result.id_token
        try:
            exp_s, _, _ = expired_from_access_token(result.access_token)
            payload["expired"] = exp_s
        except Exception:
            exp = jwt_exp(result.access_token)
            if exp:
                payload["expired"] = datetime.fromtimestamp(exp, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
        payload["last_refresh"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # re-enable if was only soft-disabled without quota? leave disabled as-is
        atomic_write(path, payload)
        return {
            "file": path.name,
            "ok": True,
            "email": payload.get("email"),
            "expired": payload.get("expired"),
        }
    except OAuthDeviceError as exc:
        msg = str(exc)
        dead = "invalid_grant" in msg.lower() or "400" in msg or "401" in msg
        if dead and soft_disable_dead:
            # Prefer soft-disable over MOVE (sticky-safe)
            payload["disabled"] = True
            payload["quota_state"] = {
                **(payload.get("quota_state") or {}),
                "reason": "refresh_revoked",
                "recover_after": time.time() + 24 * 3600,
                "marked_at": time.time(),
            }
            try:
                atomic_write(path, payload)
            except Exception:
                pass
        elif dead and purge_dead:
            dead_dir.mkdir(parents=True, exist_ok=True)
            dest = dead_dir / path.name
            if dest.exists():
                dest = dead_dir / f"{path.stem}.{int(time.time())}{path.suffix}"
            try:
                path.replace(dest)
            except Exception:
                pass
        return {"file": path.name, "ok": False, "error": msg[:200], "dead": dead}
    except Exception as exc:
        return {"file": path.name, "ok": False, "error": str(exc)[:200]}


def silent_refresh_pool(
    *,
    auth_dir: Path | None = None,
    within_hours: float = 2.0,
    max_files: int = 40,
    workers: int = 2,
    proxy: str | None = None,
    include_disabled: bool = False,
    log: Any = None,
) -> dict[str, Any]:
    """Silent pre-expiry refresh for sticky-friendly pools (no probe, no hard purge).

    Community practice: renew access_token before JWT dies mid-session so
    CLIProxy session-affinity does not reselect on 401.
    """
    def _log(msg: str) -> None:
        if callable(log):
            log(msg)

    cfg = load_cfg()
    d = auth_dir or Path(str(cfg.get("cpa_auth_dir") or "cpa_auths"))
    if not d.is_absolute():
        d = (ROOT / d).resolve()
    if not d.is_dir():
        return {"ok": False, "error": "no_auth_dir", "refreshed": 0, "failed": 0, "skipped": 0}

    proxy = proxy if proxy is not None else (
        str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None
    )
    within_sec = max(0.0, float(within_hours) * 3600.0)
    candidates: list[Path] = []
    # Prefer soonest-to-expire first (sticky-friendly: save sessions about to die)
    scored: list[tuple[int, Path]] = []
    for p in d.glob("xai-*.json"):
        if needs_refresh(
            p,
            within_sec=within_sec,
            expired_only=False,
            include_disabled=include_disabled,
        ):
            try:
                at = str(json.loads(p.read_text(encoding="utf-8")).get("access_token") or "")
                exp = jwt_exp(at) or 0
            except Exception:
                exp = 0
            scored.append((exp or 0, p))
    scored.sort(key=lambda x: x[0])  # earliest exp first
    candidates = [p for _, p in scored]
    if max_files and len(candidates) > max_files:
        candidates = candidates[:max_files]

    stats = {"refreshed": 0, "failed": 0, "skipped": 0, "dead": 0, "candidates": len(candidates)}
    if not candidates:
        return {"ok": True, **stats}

    dead_dir = d.parent / "cpa_auths_dead"
    workers = max(1, min(int(workers or 1), 4))

    def _job(path: Path) -> dict[str, Any]:
        return refresh_one(
            path,
            proxy=proxy,
            purge_dead=False,
            dead_dir=dead_dir,
            soft_disable_dead=True,
        )

    results: list[dict[str, Any]] = []
    if workers == 1:
        results = [_job(p) for p in candidates]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_job, p): p for p in candidates}
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    results.append({"ok": False, "error": str(exc)[:120]})

    for r in results:
        if r.get("skipped"):
            stats["skipped"] += 1
        elif r.get("ok"):
            stats["refreshed"] += 1
        else:
            stats["failed"] += 1
            if r.get("dead"):
                stats["dead"] += 1

    _log(
        f"[pool-refresh] candidates={stats['candidates']} refreshed={stats['refreshed']} "
        f"failed={stats['failed']} dead={stats['dead']} skipped={stats['skipped']} "
        f"within_h={within_hours}"
    )
    return {"ok": True, **stats}


def main(argv: list[str] | None = None) -> int:
    try:
        import stdio_utf8  # noqa: F401
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Batch refresh CPA pool tokens")
    ap.add_argument("--auth-dir", default="cpa_auths")
    ap.add_argument("--within-hours", type=float, default=3.0, help="refresh if expiring within N hours")
    ap.add_argument("--expired-only", action="store_true")
    ap.add_argument("--domain", default="", help="only emails on this domain")
    ap.add_argument("--max", type=int, default=0, help="max files to refresh (0=all matching)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--purge-dead", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-disabled", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_cfg()
    auth_dir = Path(args.auth_dir)
    if not auth_dir.is_absolute():
        auth_dir = (ROOT / auth_dir).resolve()
    dead_dir = auth_dir.parent / "cpa_auths_dead"
    proxy = str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None
    within_sec = max(0.0, float(args.within_hours) * 3600.0)
    domain = (args.domain or "").strip().lower()

    candidates: list[Path] = []
    for p in sorted(auth_dir.glob("xai-*.json")):
        if domain and domain not in p.name.lower():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("disabled") and not args.include_disabled:
            continue
        if needs_refresh(p, within_sec=within_sec, expired_only=bool(args.expired_only)):
            candidates.append(p)

    if args.max and args.max > 0:
        candidates = candidates[: int(args.max)]

    print(
        f"[*] candidates={len(candidates)} within_h={args.within_hours} "
        f"domain={domain or '*'} workers={args.workers} dry_run={args.dry_run}"
    )
    if args.dry_run:
        for p in candidates[:20]:
            print("  would refresh", p.name)
        if len(candidates) > 20:
            print(f"  ... and {len(candidates)-20} more")
        return 0

    ok = fail = dead = 0
    results = []
    workers = max(1, min(int(args.workers or 1), 8))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(
                refresh_one,
                p,
                proxy=proxy,
                purge_dead=bool(args.purge_dead),
                dead_dir=dead_dir,
            ): p
            for p in candidates
        }
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            if r.get("ok"):
                ok += 1
                print(f"[+] {r.get('file')} -> {r.get('expired')}")
            else:
                fail += 1
                if r.get("dead"):
                    dead += 1
                print(f"[-] {r.get('file')}: {r.get('error')}")

    report = {
        "candidates": len(candidates),
        "ok": ok,
        "fail": fail,
        "dead": dead,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    rep = ROOT / "logs" / "_refresh_pool_report.json"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(json.dumps({"summary": report, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    print(f"[*] report: {rep}")
    return 0 if fail == 0 or ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
