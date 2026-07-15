#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Restore falsely-killed own-domain accounts from cpa_auths_dead -> cpa_auths.

These accounts' ACCESS_TOKEN is still valid (verified 100% via correct URL).
They were moved to dead by a refresh-token rotation race (concurrent refresh
consumers -> loser sees invalid_grant -> false-disabled). Restore clears the
false-dead flags and moves the file back to the live dir.

Flags cleared: disabled, quota_state, _keepalive_fail_streak, _last_keepalive
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpa_xai.schema import jwt_payload  # noqa: E402

DEAD_DIR = "cpa_auths_dead"
LIVE_DIR = "cpa_auths"
OWN_DOMAINS = ("@baoxia.top", "@lima.cc.cd", "@zhuguang.ccwu.cc", "@zhuguang.de5.net", "@hotmail.com")
CLEAR_KEYS = ("disabled", "quota_state", "_keepalive_fail_streak", "_last_keepalive")


def own_domain(fname: str) -> bool:
    return any(d in fname for d in OWN_DOMAINS)


def main() -> None:
    dry = "--apply" not in sys.argv
    all_domains = "--all-domains" in sys.argv
    min_ttl = 1800.0
    for a in sys.argv[1:]:
        if a.startswith("--min-ttl="):
            min_ttl = float(a.split("=", 1)[1])

    now = time.time()
    restore = skip_ttl = skip_parse = 0
    for fname in os.listdir(DEAD_DIR):
        if not fname.endswith(".json"):
            continue
        if not all_domains and not own_domain(fname):
            continue
        src = os.path.join(DEAD_DIR, fname)
        try:
            with io.open(src, encoding="utf-8") as f:
                data = json.load(f)
            at = (data.get("access_token") or "").strip()
            if not at:
                skip_parse += 1
                continue
            exp = float(jwt_payload(at)["exp"])
        except Exception:
            skip_parse += 1
            continue
        if exp - now <= min_ttl:
            skip_ttl += 1
            continue
        # eligible
        restore += 1
        if dry:
            continue
        changed = False
        for k in CLEAR_KEYS:
            if k in data:
                data.pop(k, None)
                changed = True
        # ensure not disabled
        if data.get("disabled"):
            data["disabled"] = False
            changed = True
        dst = os.path.join(LIVE_DIR, fname)
        with io.open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.remove(src)

    mode = "DRY-RUN" if dry else "APPLIED"
    print(f"[{mode}] restore_candidates={restore}  skip_ttl<{int(min_ttl)}s={skip_ttl}  skip_parse={skip_parse}")
    if dry:
        print("[dry-run] re-run with --apply to move files back to", LIVE_DIR)


if __name__ == "__main__":
    main()
