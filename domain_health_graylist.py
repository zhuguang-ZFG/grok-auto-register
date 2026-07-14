#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domain-level graylist for CPA pool rotation.

Inspired by Reasonix StormBreaker (signature-level detection) and Kiro-Go
account_failover (classify + cooldown), applied at domain granularity:

When accounts from a domain keep failing during keepalive or actual use,
the domain enters a graylist with a cooldown period. During rotation,
accounts from graylisted domains are skipped — avoiding the "dead domain
drag" where every request hits a dead account from a poisoned domain.

State file: cpa_auths/.domain_health.json
  {
    "updated_at": "...",
    "domains": {
      "binbim.locker": {"ok": 5, "fail": 50, "rate": 0.91, "graylist_until": 1784...},
      ...
    }
  }

Thresholds (configurable in config.json):
  domain_graylist_fail_rate: 0.7   # >70% fail rate → graylist
  domain_graylist_min_sample: 5    # need at least 5 samples to judge
  domain_graylist_cooldown_sec: 3600  # graylist lasts 1 hour
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
STATE_FILE = PROJECT_ROOT / "cpa_auths" / ".domain_health.json"

DEFAULT_FAIL_RATE = 0.7
DEFAULT_MIN_SAMPLE = 5
DEFAULT_COOLDOWN_SEC = 3600  # 1 hour


def _load_cfg() -> dict[str, Any]:
    p = PROJECT_ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _domain_of(email_or_name: str) -> str:
    """Extract domain from email or filename."""
    s = (email_or_name or "").strip().lower()
    if "@" in s:
        return s.rsplit("@", 1)[-1]
    # Try filename pattern xai-xxx@domain.json
    if "@" in s:
        return s.rsplit("@", 1)[-1].replace(".json", "")
    return "unknown"


def record_result(domain: str, ok: bool) -> None:
    """Record a keepalive/probe result for a domain. Call from cpa_keepalive."""
    cfg = _load_cfg()
    min_sample = int(cfg.get("domain_graylist_min_sample") or DEFAULT_MIN_SAMPLE)
    fail_rate_threshold = float(cfg.get("domain_graylist_fail_rate") or DEFAULT_FAIL_RATE)
    cooldown_sec = int(cfg.get("domain_graylist_cooldown_sec") or DEFAULT_COOLDOWN_SEC)

    state = load_state()
    domains = state.setdefault("domains", {})
    entry = domains.get(domain, {"ok": 0, "fail": 0, "rate": 0.0, "graylist_until": 0})

    if ok:
        entry["ok"] = entry.get("ok", 0) + 1
    else:
        entry["fail"] = entry.get("fail", 0) + 1

    total = entry["ok"] + entry["fail"]
    entry["rate"] = entry["fail"] / total if total > 0 else 0.0

    # Check graylist trigger
    now = int(time.time())
    already_graylisted = entry.get("graylist_until", 0) > now
    if (
        total >= min_sample
        and entry["rate"] >= fail_rate_threshold
        and not already_graylisted
    ):
        entry["graylist_until"] = now + cooldown_sec
    elif already_graylisted and entry["rate"] < fail_rate_threshold * 0.5:
        # Recovered: fail rate dropped below half threshold
        entry["graylist_until"] = 0

    entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    domains[domain] = entry
    state["updated_at"] = entry["updated_at"]
    save_state(state)


def is_domain_graylisted(domain: str) -> bool:
    """Check if a domain is currently graylisted (should be skipped during rotation)."""
    state = load_state()
    entry = state.get("domains", {}).get(domain)
    if not entry:
        return False
    until = entry.get("graylist_until", 0)
    return until > int(time.time())


def graylisted_domains() -> list[str]:
    """Return list of currently graylisted domains."""
    state = load_state()
    now = int(time.time())
    return [
        d for d, e in state.get("domains", {}).items()
        if e.get("graylist_until", 0) > now
    ]


def domain_health_summary() -> dict[str, Any]:
    """Return summary for dashboard/logging."""
    state = load_state()
    now = int(time.time())
    domains = state.get("domains", {})
    active_graylist = {
        d: e for d, e in domains.items()
        if e.get("graylist_until", 0) > now
    }
    return {
        "updated_at": state.get("updated_at"),
        "total_domains_tracked": len(domains),
        "graylisted": list(active_graylist.keys()),
        "graylist_count": len(active_graylist),
        "domain_details": {
            d: {
                "ok": e.get("ok", 0),
                "fail": e.get("fail", 0),
                "rate": round(e.get("rate", 0), 3),
                "graylisted": e.get("graylist_until", 0) > now,
                "cooldown_remaining_sec": max(0, e.get("graylist_until", 0) - now),
            }
            for d, e in sorted(domains.items(), key=lambda x: -x[1].get("rate", 0))
        },
    }


def load_state() -> dict[str, Any]:
    if not STATE_FILE.is_file():
        return {"updated_at": "", "domains": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"updated_at": "", "domains": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(state, ensure_ascii=False, indent=2)
    # Windows-safe: direct write (avoid tmp.replace permission issues)
    try:
        STATE_FILE.write_text(data, encoding="utf-8")
    except PermissionError:
        # Fallback: retry once after brief pause (file may be locked by reader)
        import time as _t
        _t.sleep(0.1)
        STATE_FILE.write_text(data, encoding="utf-8")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Domain health graylist for CPA pool")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="Show domain health summary")
    sub.add_parser("graylisted", help="List graylisted domains")
    ap.add_argument("--seed-keepalive", action="store_true",
                    help="Seed domain health from keepalive result files")
    args = ap.parse_args()

    if args.cmd == "status":
        print(json.dumps(domain_health_summary(), indent=2, ensure_ascii=False))
    elif args.cmd == "graylisted":
        domains = graylisted_domains()
        if domains:
            print(f"Graylisted domains ({len(domains)}):")
            for d in domains:
                print(f"  {d}")
        else:
            print("No graylisted domains")
    elif args.seed_keepalive:
        # Parse keepalive log to seed initial domain health
        print("Use cpa_keepalive.py to populate domain health automatically.")
    else:
        ap.print_help()
