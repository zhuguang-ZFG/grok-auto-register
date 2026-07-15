#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recover falsely-killed xAI accounts via a SINGLE clean RT refresh.

The earlier "RT revoked" verdict was measured DURING a refresh-rotation race
(concurrent keepalive + hard_purge hitting the same account), which makes every
loser see invalid_grant. The storm is now stopped, so a single clean refresh is
an accurate liveness test:
  - RT valid  -> new AT + new RT (rotation) -> write back -> move to live (FULLY recovered, sustainable)
  - invalid_grant -> RT truly dead -> leave in dead
  - transient -> skip (retry next pass)

SAFETY: on success the new RT MUST be written back immediately, because the old
RT is consumed by the refresh. Files are deduped and each refreshed at most once.
"""
from __future__ import annotations

import io
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpa_xai.oauth_device import OAuthDeviceError, refresh_access_token  # noqa: E402
from cpa_xai.schema import expired_from_access_token  # noqa: E402

DEAD_DIR = "cpa_auths_dead"
LIVE_DIR = "cpa_auths"
PROXY = "http://127.0.0.1:7897"
CLEAR_KEYS = ("disabled", "quota_state", "_keepalive_fail_streak", "_last_keepalive")


def list_dead() -> list[str]:
    return [f for f in os.listdir(DEAD_DIR) if f.endswith(".json")]


def recover_one(fname: str) -> dict:
    src = os.path.join(DEAD_DIR, fname)
    try:
        with io.open(src, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"file": fname, "result": "parse_err", "err": str(e)[:80]}
    rt = (data.get("refresh_token") or "").strip()
    if not rt:
        return {"file": fname, "result": "no_rt"}
    try:
        tr = refresh_access_token(rt, timeout=25.0, proxy=PROXY, retries=1)
    except OAuthDeviceError as e:
        msg = str(e)
        if "invalid/expired" in msg or "invalid_grant" in msg:
            return {"file": fname, "result": "rt_dead"}
        return {"file": fname, "result": "transient", "err": msg[:80]}
    except Exception as e:  # noqa: BLE001
        return {"file": fname, "result": "transient", "err": str(e)[:80]}

    # success — RT valid. MUST persist the rotated tokens immediately (the old
    # RT is consumed by this refresh; not saving would destroy a live account).
    data["access_token"] = tr.access_token
    data["refresh_token"] = tr.refresh_token
    if tr.id_token:
        data["id_token"] = tr.id_token
    data["token_type"] = tr.token_type or "Bearer"
    data["expires_in"] = int(tr.expires_in or 21600)
    try:
        exp_s, exp_in, _ = expired_from_access_token(tr.access_token)
        data["expired"] = exp_s
        data["expires_in"] = exp_in or data["expires_in"]
    except Exception:
        pass
    data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for k in CLEAR_KEYS:
        data.pop(k, None)
    data["disabled"] = False

    dst = os.path.join(LIVE_DIR, fname)
    tmp = dst + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, dst)
    os.remove(src)
    return {"file": fname, "result": "recovered"}


def main() -> None:
    args = sys.argv[1:]
    sample_n = 0
    workers = 8
    for a in args:
        if a.startswith("--sample="):
            sample_n = int(a.split("=", 1)[1])
        if a.startswith("--workers="):
            workers = int(a.split("=", 1)[1])
        if a == "--all":
            sample_n = -1

    files = list_dead()
    if sample_n == -1:
        targets = files
    elif sample_n > 0:
        random.seed(2026)
        targets = random.sample(files, min(sample_n, len(files)))
    else:
        targets = random.sample(files, min(100, len(files)))

    print(f"[recover] dead_total={len(files)}  targets={len(targets)}  workers={workers}")

    from collections import Counter
    stats: Counter = Counter()
    errs = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(recover_one, f): f for f in targets}
        done = 0
        for fut in as_completed(futs):
            r = fut.result()
            stats[r["result"]] += 1
            if r["result"] == "transient" and len(errs) < 5:
                errs.append(r.get("err", ""))
            done += 1
            if done % 200 == 0:
                print(f"  ...{done}/{len(targets)}  {dict(stats)}")

    print(f"[result] {dict(stats)}")
    if errs:
        print("[transient samples]", errs)
    tested = stats.get("recovered", 0) + stats.get("rt_dead", 0)
    if tested:
        rate = stats.get("recovered", 0) / tested
        print(f"[rate] rt_alive={rate*100:.1f}% over {tested} definitive  -> projected recoverable of {len(files)}: ~{int(rate*len(files))}")


if __name__ == "__main__":
    main()
