#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K12 pool health monitor — sample-probe account survival rate.

Periodically sends a test request through the gateway to check if the pool
is still alive. Logs survival rate and alerts when it drops below threshold.

Two check modes:
  1. Gateway stats: GET /api/accounts?status=normal → count healthy accounts
  2. Active probe: POST /v1/chat/completions with a trivial prompt → verify
     the pool can actually serve traffic

Usage:
  python scripts/k12_pool_monitor.py                # one-shot check
  python scripts/k12_pool_monitor.py --watch        # continuous (every 5 min)
  python scripts/k12_pool_monitor.py --watch --interval 300
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT / "logs" / "k12_pool_monitor.log"
GATEWAY = "http://127.0.0.1:8124"
AUTH_KEY = "k12-pool-local"

# Alert thresholds
ALIVE_RATIO_WARN = 0.5    # warn if <50% accounts alive
ALIVE_RATIO_CRIT = 0.2    # critical if <20% alive


def log(msg: str) -> None:
    ts = datetime.now(tz=__import__("datetime").timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def gateway_stats() -> dict:
    """Get account counts by status from gateway."""
    result = {"total": 0, "normal": 0, "abnormal": 0, "limited": 0, "disabled": 0}
    try:
        req = urllib.request.Request(
            f"{GATEWAY}/api/accounts?page=1&page_size=1",
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        result["total"] = data.get("total", 0)
    except Exception as exc:
        result["error"] = str(exc)[:80]
        return result

    for status in ("normal", "abnormal", "limited", "disabled"):
        try:
            req = urllib.request.Request(
                f"{GATEWAY}/api/accounts?page=1&page_size=1&status={status}",
                headers={"Authorization": f"Bearer {AUTH_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.loads(resp.read())
            result[status] = d.get("total", 0)
        except Exception:
            pass
    return result


def active_probe() -> dict:
    """Send a trivial chat request to verify the pool can serve traffic."""
    try:
        payload = json.dumps({
            "model": "gpt-5-mini",
            "messages": [{"role": "user", "content": "1"}],
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{GATEWAY}/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {AUTH_KEY}",
            },
            method="POST",
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        elapsed = time.time() - t0
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {
            "ok": True,
            "latency_s": round(elapsed, 1),
            "response": content[:50],
        }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:80]}


def check_once() -> dict:
    """Run one full health check cycle."""
    log("--- Health check ---")

    # 1. Gateway stats
    stats = gateway_stats()
    if "error" in stats:
        log(f"FATAL: Gateway unreachable: {stats['error']}")
        return {"status": "fatal", "stats": stats}

    total = stats.get("total", 0)
    normal = stats.get("normal", 0)
    alive_ratio = normal / total if total > 0 else 0

    log(f"Pool: {normal}/{total} normal ({alive_ratio:.1%}), "
        f"abnormal={stats.get('abnormal', 0)}, "
        f"limited={stats.get('limited', 0)}")

    # 2. Active probe (only if there are normal accounts)
    probe = {"ok": False, "skipped": True}
    if normal > 0:
        probe = active_probe()
        if probe.get("ok"):
            log(f"Probe: OK ({probe['latency_s']}s) -> {probe.get('response', '?')[:30]}")
        else:
            log(f"Probe: FAILED ({probe.get('error', '?')})")

    # 3. Assess
    if alive_ratio < ALIVE_RATIO_CRIT:
        level = "CRITICAL"
        log(f"!!! CRITICAL: alive ratio {alive_ratio:.1%} < {ALIVE_RATIO_CRIT:.0%}")
    elif alive_ratio < ALIVE_RATIO_WARN:
        level = "WARN"
        log(f"!! WARN: alive ratio {alive_ratio:.1%} < {ALIVE_RATIO_WARN:.0%}")
    elif not probe.get("ok") and not probe.get("skipped"):
        level = "WARN"
        log("!! WARN: accounts exist but probe failed")
    else:
        level = "OK"

    return {"status": level, "stats": stats, "probe": probe, "alive_ratio": alive_ratio}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K12 pool health monitor")
    p.add_argument("--watch", action="store_true", help="continuous monitoring")
    p.add_argument("--interval", type=int, default=300, help="check interval in seconds (default: 300)")
    args = p.parse_args(argv)

    if not args.watch:
        result = check_once()
        return 0 if result["status"] != "fatal" else 1

    log(f"Monitor started (interval={args.interval}s)")
    while True:
        try:
            check_once()
        except KeyboardInterrupt:
            log("Monitor stopped by user")
            return 0
        except Exception as exc:
            log(f"Monitor error: {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
