#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI for dahl_pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

from .pipeline import run_e2e
from .proxy_server import run_forever


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dahl_pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("e2e", help="mint + models + chat fully automated")
    e.add_argument("--proxy", default="http://127.0.0.1:7897")
    e.add_argument("--model", default="")
    e.add_argument("--prompt", default="Reply with exactly: dahl-ok")
    e.add_argument("--headless", action="store_true")

    pr = sub.add_parser("proxy", help="OpenAI-compatible local proxy via browser CF")
    pr.add_argument("--port", type=int, default=8330)
    pr.add_argument("--proxy", default="http://127.0.0.1:7897")
    pr.add_argument("--api-key", default="sk-local-dahl")
    pr.add_argument("--headless", action="store_true", help="true headless (may hit CF harder)")
    pr.add_argument(
        "--show-window",
        action="store_true",
        help="do not park browser off-screen",
    )
    pr.add_argument("--no-watchdog", action="store_true")
    pr.add_argument(
        "--remint-max-per-day",
        type=int,
        default=5,
        help="max auto remints per UTC day when key/quota fails (0=disable)",
    )
    pr.add_argument(
        "--remint-low-threshold",
        type=int,
        default=50000,
        help="proactive remint when available_tokens estimate below this",
    )

    args = p.parse_args(argv)
    if args.cmd == "e2e":
        rep = run_e2e(
            proxy=args.proxy,
            model=args.model or None,
            prompt=args.prompt,
            headless=args.headless,
        )
        print(json.dumps({k: v for k, v in rep.items() if k != "_session"}, ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 1
    if args.cmd == "proxy":
        run_forever(
            port=args.port,
            proxy=args.proxy,
            api_key=args.api_key,
            headless=args.headless,
            hide_window=not args.show_window,
            watchdog=not args.no_watchdog,
            remint_max_per_day=args.remint_max_per_day,
            remint_low_threshold=args.remint_low_threshold,
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
