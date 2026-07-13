#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI for databricks_pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

from .config import get_databricks_section
from . import pool, probe, pipeline
from .proxy_server import run_forever


def cmd_list(args: argparse.Namespace) -> int:
    cfg = get_databricks_section()
    rows = pool.list_credentials(cfg)
    if not rows:
        print("(empty pool)")
        return 0
    for c in rows:
        models_ok = [
            k for k, v in (c.get("models") or {}).items() if isinstance(v, dict) and v.get("ok")
        ]
        print(
            f"{c.get('id')}\t{c.get('status')}\t{c.get('email')}\t"
            f"exp={c.get('trial_expires_at')}\tok_models={','.join(models_ok) or '-'}"
        )
    print(f"daily_count={pool.get_daily_count(cfg)}/{cfg.get('max_per_day')}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    cfg = get_databricks_section()
    if args.id:
        data = pool.get_by_id(args.id, cfg)
        if not data:
            print(f"not found: {args.id}", file=sys.stderr)
            return 1
        targets = [data]
    else:
        targets = pool.list_credentials(cfg)
    if not targets:
        print("no credentials")
        return 0
    for data in targets:
        updated = probe.probe_credential(data, cfg)
        pool.save_credential(updated, cfg)
        print(f"{updated.get('id')}\t{updated.get('status')}\t{updated.get('disable_reason')}")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    cfg = get_databricks_section()
    n = int(args.count or cfg.get("register_count") or 1)
    results = pipeline.register_many(n, cfg)
    print(json.dumps(
        [
            {
                "id": r.get("id"),
                "status": r.get("status"),
                "email": r.get("email"),
                "error": r.get("error"),
                "disable_reason": r.get("disable_reason"),
            }
            for r in results
            if isinstance(r, dict)
        ],
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    cfg = get_databricks_section()
    pool.soft_disable(args.id, args.reason or "manual", cfg)
    print("ok")
    return 0


def cmd_proxy(args: argparse.Namespace) -> int:
    run_forever(get_databricks_section())
    return 0


def cmd_import_manual(args: argparse.Namespace) -> int:
    """Import host+token for B0 smoke without browser."""
    from .schema import new_credential

    cfg = get_databricks_section()
    data = new_credential(
        email=args.email or "manual@local",
        host=args.host,
        token=args.token,
        status="incomplete",
    )
    data = probe.probe_credential(data, cfg)
    path = pool.save_credential(data, cfg)
    print(f"saved {path} status={data.get('status')}")
    return 0 if data.get("status") == "live" else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="databricks_pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="list pool")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("probe", help="probe credentials")
    s.add_argument("--id", default="")
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("register", help="run signup pipeline")
    s.add_argument("--count", type=int, default=0)
    s.set_defaults(func=cmd_register)

    s = sub.add_parser("disable", help="soft-disable by id")
    s.add_argument("--id", required=True)
    s.add_argument("--reason", default="manual")
    s.set_defaults(func=cmd_disable)

    s = sub.add_parser("proxy", help="run OpenAI-compatible proxy")
    s.set_defaults(func=cmd_proxy)

    s = sub.add_parser("import-manual", help="import host+token and probe")
    s.add_argument("--host", required=True)
    s.add_argument("--token", required=True)
    s.add_argument("--email", default="")
    s.set_defaults(func=cmd_import_manual)

    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
