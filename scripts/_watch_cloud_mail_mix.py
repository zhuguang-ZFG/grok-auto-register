#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Force a few cloud_mail create_inbox calls and report domain success/fail."""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cloud_mail_otp as cm  # noqa: E402


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    print("domains", cfg.get("cloud_mail_domains"))
    print("mode", cfg.get("cloud_mail_domain_mode"))
    ok = Counter()
    fail = Counter()
    for i in range(n):
        t0 = time.time()
        try:
            email, tok = cm.create_inbox(cfg, root=ROOT)
            data = json.loads(tok)
            dom = data.get("domain") or (email.split("@")[-1] if "@" in email else "?")
            ok[dom] += 1
            print(f"[{i+1}/{n}] OK {email} accountId={data.get('accountId')} {time.time()-t0:.1f}s")
        except Exception as exc:
            msg = str(exc)[:180]
            # try extract domain from error
            dom = "?"
            for d in cfg.get("cloud_mail_domains") or []:
                if d in msg:
                    dom = d
                    break
            fail[dom] += 1
            print(f"[{i+1}/{n}] FAIL {time.time()-t0:.1f}s {msg}")
        time.sleep(1)
    print("OK_BY_DOMAIN", dict(ok))
    print("FAIL_HINT", dict(fail))
    return 0 if sum(ok.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
