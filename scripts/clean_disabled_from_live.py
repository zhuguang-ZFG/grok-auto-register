#!/usr/bin/env python3
"""Clean cpa_auths: move terminal disabled out, quarantine soft holds."""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpa_xai.quarantine import discard_auth, quarantine_auth

LIVE = ROOT / "cpa_auths"
DEAD = ROOT / "cpa_auths_dead"


def main() -> int:
    DEAD.mkdir(parents=True, exist_ok=True)
    ctr: Counter[str] = Counter()
    for p in sorted(LIVE.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            ctr["bad_json"] += 1
            continue
        if data.get("disabled") is not True:
            ctr["keep_enabled"] += 1
            continue
        qs = data.get("quota_state") or {}
        reason = str(qs.get("reason") or "disabled")
        if reason in ("refresh_revoked", "invalid_grant", "missing_refresh_token"):
            # terminal: discard (quarantine/_discarded) + remove from live
            discard_auth(data, root=ROOT, reason=reason)
            p.unlink(missing_ok=True)
            ctr[f"discard:{reason}"] += 1
            continue
        if reason in ("free-usage-exhausted", "quota_exhausted", "permission-denied", "permission_denied"):
            recover = 6 * 3600.0 if "quota" in reason or "exhausted" in reason else 24 * 3600.0
            mapped = "quota_exhausted" if "exhausted" in reason or "quota" in reason else "permission_denied"
            # prefer existing recover_after if still in future
            ra = qs.get("recover_after")
            if isinstance(ra, (int, float)):
                import time

                left = float(ra) - time.time()
                if left > 60:
                    recover = left
            quarantine_auth(data, root=ROOT, reason=mapped, recover_after_sec=recover)
            p.unlink(missing_ok=True)
            ctr[f"quarantine:{mapped}"] += 1
            continue
        # unknown disabled → dead dir for audit
        dest = DEAD / p.name
        if dest.exists():
            dest = DEAD / f"{p.stem}-{int(os.path.getmtime(p))}{p.suffix}"
        shutil.move(str(p), str(dest))
        ctr[f"dead:{reason}"] += 1

    print(dict(ctr))
    print("live_remaining", len(list(LIVE.glob('*.json'))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
