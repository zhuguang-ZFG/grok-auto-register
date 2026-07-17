#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe all live Grok CPA auths and quarantine/discard degraded ones.

Community absorb (archive credential_pool scoring + acpa_watchdog):
  - chat probe is the hard gate
  - 403 permission-denied -> quarantine (recover_after default 24h)
  - 429 quota_exhausted -> quarantine (recover_after default 6h)
  - 401 / anti-bot -> discard
  - bot_flag_source recorded as advisory metadata

Usage:
  python scripts/pool_health_probe.py --workers 8 --sample 0
  python scripts/pool_health_probe.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cpa_xai.probe import probe_account_health
from cpa_xai.proxyutil import next_proxy_from_pool, proxy_log_label
from cpa_xai.quarantine import discard_auth, move_to_live, quarantine_auth


def load_cfg() -> dict[str, Any]:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def classify_action(result: dict[str, Any]) -> tuple[str, float, str]:
    """Return (action, recover_after_sec, reason).

    Hard discard only on auth failure or confirmed chat anti-bot.
    models-fail / network / bot-flag advisory -> quarantine for retest.
    """
    if result.get("ok"):
        return "keep", 0.0, "chat_ok"
    tags = set(str(t).lower() for t in (result.get("tags") or []))
    if "auth" in tags or "unauthorized" in tags:
        return "discard", 0.0, "unauthorized"
    # Only discard anti-bot when chat itself failed with anti-bot (not just JWT flag).
    if "anti-bot" in tags and "models-fail" not in tags and "network" not in tags:
        return "discard", 0.0, "anti_bot"
    if "permission-denied" in tags or "forbidden" in tags:
        return "quarantine", 24 * 3600.0, "permission_denied"
    if "quota-exhausted" in tags or "rate-limit" in tags:
        return "quarantine", 6 * 3600.0, "quota_exhausted"
    if "network" in tags:
        return "quarantine", 2 * 3600.0, "network_error"
    return "quarantine", 24 * 3600.0, "probe_error"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT), help="project root")
    ap.add_argument("--workers", type=int, default=8, help="parallel probe workers")
    ap.add_argument("--sample", type=int, default=0, help="probe only N random (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="do not move files")
    ap.add_argument("--probe-timeout", type=float, default=15.0)
    args = ap.parse_args(argv)

    root = Path(args.root)
    cfg = load_cfg()
    proxy = next_proxy_from_pool(cfg)
    print(f"[*] pool health probe proxy={proxy_log_label(proxy)} dry_run={args.dry_run}")

    live_dir = root / "cpa_auths"
    files = sorted(live_dir.glob("xai-*.json"))
    if args.sample > 0 and args.sample < len(files):
        import random
        random.seed(42)
        files = random.sample(files, args.sample)
    print(f"[*] probing {len(files)} live auths")

    import concurrent.futures
    import threading

    lock = threading.Lock()
    progress = {"n": 0}

    def _probe_one(p: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        try:
            auth = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return p, {"ok": False, "error": str(e), "tags": ["read_error"]}, {}
        at = str(auth.get("access_token") or "").strip()
        if not at:
            return p, {"ok": False, "error": "no access_token", "tags": ["auth"]}, auth
        res = probe_account_health(
            at,
            base_url=str(auth.get("base_url") or "https://cli-chat-proxy.grok.com/v1"),
            proxy=proxy or None,
            models_timeout=min(10.0, args.probe_timeout),
            chat_timeout=args.probe_timeout,
        )
        with lock:
            progress["n"] += 1
            n = progress["n"]
            if n % 20 == 0 or n == len(files):
                print(f"[*] progress {n}/{len(files)} ({100*n//len(files)}%)", flush=True)
        return p, res, auth

    stats: Counter = Counter()
    actions: list[tuple[Path, dict[str, Any], dict[str, Any], str, float, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for p, res, auth in ex.map(_probe_one, files):
            action, recover, reason = classify_action(res)
            stats[action] += 1
            if action != "keep":
                actions.append((p, res, auth, action, recover, reason))

    print(f"[*] stats: {dict(stats)}")
    for p, res, auth, action, recover, reason in actions:
        print(
            f"[{action}] {p.name}: {reason} chat_ok={res.get('chat_ok')} "
            f"tags={sorted(set(str(t).lower() for t in (res.get('tags') or [])))}"
        )
        if args.dry_run:
            continue
        try:
            if action == "quarantine":
                quarantine_auth(auth, root=root, reason=reason, recover_after_sec=recover)
                p.unlink(missing_ok=True)
            elif action == "discard":
                discard_auth(auth, root=root, reason=reason)
                p.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[!] failed to {action} {p.name}: {exc}", file=sys.stderr)

    report = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(files),
        "stats": dict(stats),
        "dry_run": args.dry_run,
    }
    out = root / "logs" / "_pool_health_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[*] report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
