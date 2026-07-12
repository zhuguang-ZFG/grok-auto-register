#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sample-refresh buffer-tier CPA files; report RT health without full hard_purge.

Community shared packs rot silently. Run daily/after big imports:
  python scripts/buffer_health_sample.py
  python scripts/buffer_health_sample.py --sample 40 --purge-dead-sample

Exit: 0 ok_rate>=min, 1 warn, 2 critical sample failure.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CLIENT = "b1a00492-073a-47ea-816f-4c329264a828"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-dir", default=str(ROOT / "cpa_auths"))
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--min-ok-rate", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--purge-dead-sample",
        action="store_true",
        help="move sample revoked files to cpa_auths_dead/",
    )
    args = ap.parse_args(argv)

    cfg_path = ROOT / "config.json"
    cfg = {}
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    proxy = str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None
    opener = (
        urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        if proxy
        else urllib.request.build_opener()
    )

    auth = Path(args.auth_dir)
    if not auth.is_absolute():
        auth = (ROOT / auth).resolve()
    try:
        from pool_policy import is_own_path
    except Exception:
        is_own_path = lambda p, c: True  # type: ignore

    buf = []
    for p in auth.glob("xai-*.json"):
        try:
            if is_own_path(p, cfg):
                continue
        except Exception:
            pass
        buf.append(p)
    random.seed(args.seed)
    random.shuffle(buf)
    sample = buf[: max(1, min(args.sample, len(buf)))]
    stats = Counter()
    dead_dir = auth.parent / "cpa_auths_dead"
    for p in sample:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            stats["bad_json"] += 1
            continue
        if d.get("disabled"):
            stats["disabled"] += 1
            continue
        rt = d.get("refresh_token")
        if not rt:
            stats["no_rt"] += 1
            continue
        body = f"grant_type=refresh_token&refresh_token={rt}&client_id={CLIENT}".encode()
        req = urllib.request.Request(
            "https://auth.x.ai/oauth2/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            opener.open(req, timeout=15)
            stats["ok"] += 1
        except urllib.error.HTTPError as e:
            b = e.read().decode("utf-8", "ignore")
            if "revoked" in b or "invalid_grant" in b:
                stats["revoked"] += 1
                if args.purge_dead_sample:
                    dead_dir.mkdir(parents=True, exist_ok=True)
                    dest = dead_dir / p.name
                    if not dest.exists():
                        p.replace(dest)
            else:
                stats["http"] += 1
        except Exception:
            stats["net"] += 1

    n = max(1, sum(stats.values()))
    ok = int(stats.get("ok") or 0)
    rate = ok / n
    report = {
        "buffer_total": len(buf),
        "sample": len(sample),
        "stats": dict(stats),
        "ok_rate": round(rate, 4),
        "min_ok_rate": args.min_ok_rate,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    out = ROOT / "logs" / "_buffer_health_sample.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if rate < float(args.min_ok_rate):
        print(f"[!] buffer sample ok_rate {rate:.0%} < {args.min_ok_rate:.0%}")
        return 1 if rate > 0 else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
