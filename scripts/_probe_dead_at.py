#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-probe 'dead' own-domain accounts by ACCESS_TOKEN with CORRECT URLs.

grok4.5's earlier AT probe had a /v1/v1 double-join bug and returned on the
first HTTPError, so AT was never really tested. This script:
  - scans cpa_auths_dead for own-domain files
  - keeps only those whose access_token JWT exp is still in the future
  - samples N and hits {base}/models with correct normalization
  - tries BOTH cli-chat-proxy and api.x.ai before judging dead
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

from cpa_xai.probe import probe_models  # noqa: E402
from cpa_xai.schema import jwt_payload  # noqa: E402

DEAD_DIR = "cpa_auths_dead"
OWN_DOMAINS = ("@baoxia.top", "@lima.cc.cd", "@zhuguang.ccwu.cc", "@zhuguang.de5.net", "@hotmail.com")
PROXY = "http://127.0.0.1:7897"
URLS = ["https://cli-chat-proxy.grok.com/v1", "https://api.x.ai/v1"]


def own_domain(fname: str) -> bool:
    return any(d in fname for d in OWN_DOMAINS)


def load_at(path: str) -> tuple[str, float]:
    """Return (access_token, at_exp_epoch). Raises on bad file."""
    with io.open(path, encoding="utf-8") as f:
        data = json.load(f)
    at = (data.get("access_token") or "").strip()
    if not at:
        raise ValueError("no access_token")
    exp = float(jwt_payload(at)["exp"])
    return at, exp


def probe_one(at: str) -> dict:
    """Try both URLs; report first non-404/200 signal honestly."""
    results = []
    for base in URLS:
        r = probe_models(at, base_url=base, timeout=25.0, proxy=PROXY)
        results.append((base, r.get("ok"), r.get("status"), (r.get("error") or "")[:120]))
        if r.get("ok"):
            return {"alive": True, "via": base, "status": r.get("status"), "detail": results}
    return {"alive": False, "via": None, "status": results[-1][2], "detail": results}


def main() -> None:
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    min_ttl = float(sys.argv[2]) if len(sys.argv) > 2 else 1800.0  # default >30min

    now = time.time()
    candidates: list[tuple[str, str, float]] = []  # (path, at, exp)
    scanned = own = 0
    for fname in os.listdir(DEAD_DIR):
        if not fname.endswith(".json") or not own_domain(fname):
            continue
        own += 1
        path = os.path.join(DEAD_DIR, fname)
        try:
            at, exp = load_at(path)
        except Exception:
            continue
        scanned += 1
        if exp - now > min_ttl:
            candidates.append((path, at, exp))

    print(f"[scan] own-domain files={own}  parseable={scanned}  AT_ttl>{int(min_ttl)}s={len(candidates)}")
    if not candidates:
        print("[result] NO candidates with live AT — nothing to probe")
        return

    random.seed(42)
    sample = random.sample(candidates, min(sample_n, len(candidates)))
    print(f"[probe] sampling {len(sample)} of {len(candidates)} ...")

    alive = dead = 0
    status_hist: dict = {}
    via_hist: dict = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(probe_one, at): (path, exp) for path, at, exp in sample}
        for fut in as_completed(futs):
            path, exp = futs[fut]
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001
                r = {"alive": False, "via": None, "status": -1, "detail": str(e)}
            st = r.get("status")
            status_hist[st] = status_hist.get(st, 0) + 1
            if r.get("alive"):
                alive += 1
                via_hist[r.get("via")] = via_hist.get(r.get("via"), 0) + 1
            else:
                dead += 1

    print(f"[result] alive={alive}  dead={dead}  of {len(sample)}")
    print(f"[result] alive_rate={alive/len(sample)*100:.1f}%")
    print(f"[result] status_hist={status_hist}")
    print(f"[result] alive_via={via_hist}")


if __name__ == "__main__":
    main()
