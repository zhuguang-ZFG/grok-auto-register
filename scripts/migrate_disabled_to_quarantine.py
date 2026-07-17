#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Migrate legacy in-place disabled auths into cpa_auths_quarantine/.

Old pool_health.py / hard_purge.py marked auths disabled=True inside cpa_auths/
with a ``quota_state`` block. This script normalizes them into the quarantine
format so ``scripts/retest_quarantine.py`` (and the GrokQuarantineRetest task)
picks them up when ``recover_after`` expires.

Usage:
  python scripts/migrate_disabled_to_quarantine.py --dry-run
  python scripts/migrate_disabled_to_quarantine.py --max 500
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cpa_xai.quarantine import DEFAULT_RECOVER_AFTER_SEC, quarantine_auth

REASON_MAP: dict[str, tuple[str, float]] = {
    "permission_denied": ("permission_denied", 24 * 3600.0),
    "free-usage-exhausted": ("quota_exhausted", 6 * 3600.0),
    "quota_exhausted": ("quota_exhausted", 6 * 3600.0),
    "refresh_revoked": ("refresh_revoked", 24 * 3600.0),
    "probe_or_refresh_fail": ("probe_or_refresh_fail", 6 * 3600.0),
}


def _recover_from_quota_state(qs: dict[str, Any]) -> float:
    """Prefer existing recover_after timestamp; fall back to defaults."""
    ra = qs.get("recover_after")
    if isinstance(ra, (int, float)):
        return float(ra)
    return 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT), help="project root")
    ap.add_argument("--max", type=int, default=0, help="migrate at most N files (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="print but do not move")
    args = ap.parse_args(argv)

    root = Path(args.root)
    live_dir = root / "cpa_auths"
    files = sorted(live_dir.glob("xai-*.json"))

    candidates: list[tuple[Path, dict[str, Any], str, float]] = []
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("_quarantine"):
            continue
        if data.get("disabled") is not True:
            continue
        qs = data.get("quota_state") or {}
        reason = qs.get("reason") or "probe_or_refresh_fail"
        mapped_reason, default_recover = REASON_MAP.get(reason, ("probe_or_refresh_fail", 6 * 3600.0))
        recover_ts = _recover_from_quota_state(qs)
        if recover_ts <= 0.0:
            recover_after_sec = default_recover
        else:
            import time
            recover_after_sec = max(0.0, recover_ts - time.time())
            if recover_after_sec <= 0:
                recover_after_sec = default_recover
        candidates.append((p, data, mapped_reason, recover_after_sec))

    if args.max > 0:
        candidates = candidates[: args.max]

    print(f"[*] found {len(candidates)} legacy disabled auths to migrate (dry_run={args.dry_run})")
    moved = 0
    for p, data, reason, recover_after_sec in candidates:
        print(f"[migrate] {p.name}: reason={reason} recover_after_sec={int(recover_after_sec)}")
        if args.dry_run:
            continue
        try:
            quarantine_auth(
                data,
                root=root,
                reason=reason,
                recover_after_sec=recover_after_sec,
            )
            p.unlink(missing_ok=True)
            moved += 1
        except Exception as exc:
            print(f"[!] failed to migrate {p.name}: {exc}", file=sys.stderr)

    print(f"[*] migrated={moved} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
