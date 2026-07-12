#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot ops dashboard: pool + routing + processes + local auth.

  python status.py
  python status.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    try:
        import stdio_utf8  # noqa: F401
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Grok auto-register ops status")
    parser.add_argument("--json", action="store_true", help="JSON snapshot with processes")
    args = parser.parse_args(argv)

    from pool_status import collect_snapshot, print_human

    snap = collect_snapshot(include_procs=True)
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0
    print_human(snap)
    print("[*] tips: python set_cliproxy_routing.py cache|pool")
    print("[*] tips: python pool_status.py --json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
