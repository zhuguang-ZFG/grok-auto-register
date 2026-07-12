#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Restore disabled flag on CPA files that CLIProxy auto-refresh may have wiped.

CLIProxy's internal auto-refresh writes token JSON back to disk. If it doesn't
preserve our custom ``disabled`` / ``quota_state`` fields, a free-usage-exhausted
account can silently re-enter the ready pool — causing repeated quota errors.

This script reconciles: if quota_state says exhausted but disabled was cleared,
restore disabled=true.

Run after CLIProxy restart or periodically.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOLD_REASONS = ("free-usage-exhausted", "quota_exhausted", "rate_limited", "prefer_buffer")


def main() -> int:
    auth = ROOT / "cpa_auths"
    if not auth.is_dir():
        return 0
    restored = 0
    scanned = 0
    for f in auth.glob("xai-*.json"):
        scanned += 1
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        qs = d.get("quota_state") or {}
        reason = str(qs.get("reason") or d.get("hold_reason") or "")
        ra = float(qs.get("recover_after") or 0)
        # If reason says hold but disabled was cleared → restore
        if reason and any(r in reason.lower() for r in ("exhaust", "hold", "prefer_buffer")):
            if not d.get("disabled"):
                # Check recover_after: if past, allow re-enable
                if ra and ra <= time.time():
                    d["disabled"] = False
                    qs["reenabled_at"] = time.time()
                    d["quota_state"] = qs
                    from pool_policy import atomic_write_json
                    atomic_write_json(f, d)
                    continue
                # Still cooling → restore disabled
                d["disabled"] = True
                atomic_write_json(f, d)
                restored += 1
    print(
        json.dumps(
            {"scanned": scanned, "restored_disabled": restored},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
