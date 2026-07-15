#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remove stale per-auth proxy fields from CPA files.

Some imported CPA auths carry `proxy: http://127.0.0.1:18478` (dead port),
which overrides CLIProxy's global `proxy-url` and causes token-refresh failures.

Usage:
  python scripts/clean_cpa_proxy.py [--dry-run] [--keep 7897]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 白名单端口：全局 proxy 7897 + per-auth 出口绑定 7911-7914
ALLOW_PORTS = {"7897", "7911", "7912", "7913", "7914"}
KEEP_PORT = "7897"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean stale proxy from CPA files")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep", default=KEEP_PORT, help=f"port to keep (default {KEEP_PORT})")
    parser.add_argument("--dir", default=str(ROOT / "cpa_auths"))
    args = parser.parse_args(argv)

    auth_dir = Path(args.dir)
    if not auth_dir.is_dir():
        print(f"[clean-proxy] dir not found: {auth_dir}")
        return 1

    keep = str(args.keep)
    allow = ALLOW_PORTS | {keep}
    checked = cleaned = 0
    for f in auth_dir.glob("xai-*.json"):
        checked += 1
        try:
            raw = f.read_text(encoding="utf-8")
            d = json.loads(raw)
        except Exception:
            continue
        changed = False
        for key in ("proxy", "proxy_url", "proxy-url"):
            if key in d:
                val = str(d[key])
                if not any(p in val for p in allow):
                    if not args.dry_run:
                        del d[key]
                        tmp = f.with_suffix(f.suffix + ".tmp")
                        tmp.write_text(
                            json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                        )
                        tmp.replace(f)
                    changed = True
        if changed:
            cleaned += 1
    mode = "DRY-RUN" if args.dry_run else "CLEANED"
    print(f"[clean-proxy] {mode}: checked={checked} cleaned={cleaned} allow_ports={sorted(ALLOW_PORTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
