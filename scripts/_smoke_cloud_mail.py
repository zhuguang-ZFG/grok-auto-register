#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live smoke: login + list; optional --create to mint one inbox (costs CapSolver)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cloud_mail_otp as cm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true", help="also create one sub-inbox")
    args = ap.parse_args()

    cfg_path = ROOT / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}

    jwt, cred = cm.login(cfg, root=ROOT)
    print("login ok", "base", cred.get("base"), "master", cred.get("email"), "jwt_len", len(jwt))

    if args.create:
        email, tok = cm.create_inbox(cfg, root=ROOT)
        print("created", email)
        data = json.loads(tok)
        print("accountId", data.get("accountId"))
        msgs = cm.list_messages(tok, cfg=cfg)
        print("list count", len(msgs))
    else:
        # list master account if accountId known
        aid = cred.get("accountId") or 6
        blob = json.dumps(
            {
                "provider": "cloud_mail",
                "base": cred.get("base"),
                "jwt": jwt,
                "accountId": int(aid),
                "email": cred.get("email"),
            }
        )
        msgs = cm.list_messages(blob, cfg=cfg)
        print("list master accountId", aid, "count", len(msgs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
