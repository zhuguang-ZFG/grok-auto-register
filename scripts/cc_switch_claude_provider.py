#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Switch Claude provider in cc-switch.db (schema-agnostic; mirrors codex switcher)."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
SETTINGS = Path(r"C:/Users/zhugu/.cc-switch/settings.json")


def connect() -> sqlite3.Connection:
    if not DB.is_file():
        raise SystemExit(f"missing {DB}")
    return sqlite3.connect(str(DB))


def list_providers() -> list[tuple]:
    c = connect()
    try:
        return c.execute(
            "SELECT id, name, is_current FROM providers WHERE app_type='claude' "
            "ORDER BY is_current DESC, name"
        ).fetchall()
    finally:
        c.close()


def cmd_list(_: argparse.Namespace) -> int:
    for pid, name, cur in list_providers():
        print(f"{'*' if cur else ' '} {pid}  {name}")
    return 0


def cmd_current(_: argparse.Namespace) -> int:
    c = connect()
    try:
        row = c.execute(
            "SELECT id, name, settings_config FROM providers "
            "WHERE app_type='claude' AND is_current=1"
        ).fetchone()
    finally:
        c.close()
    if not row:
        print("no current claude provider")
        return 1
    pid, name, sc = row
    print(f"id:   {pid}")
    print(f"name: {name}")
    try:
        env = json.loads(sc or "{}").get("env") or {}
        for k in sorted(env):
            if "TOKEN" in k or "KEY" in k:
                print(f"env:  {k}=len:{len(str(env[k]))}")
            else:
                print(f"env:  {k}={env[k]}")
    except Exception as e:
        print(f"parse err: {e}")
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    target = str(args.id).strip()
    c = connect()
    try:
        row = c.execute(
            "SELECT id, name, settings_config FROM providers "
            "WHERE id=? AND app_type='claude'",
            (target,),
        ).fetchone()
        if not row:
            print(f"provider not found: {target}")
            for pid, name, cur in list_providers():
                print(f"  {pid}  {name}")
            return 1
        pid, name, sc = row
        bak = DB.with_name(
            f"cc-switch.db.bak-claude-switch-{time.strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(DB, bak)
        print(f"backup {bak}")
        c.execute("UPDATE providers SET is_current=0 WHERE app_type='claude'")
        c.execute(
            "UPDATE providers SET is_current=1 WHERE id=? AND app_type='claude'",
            (pid,),
        )
        c.commit()
    finally:
        c.close()
    if SETTINGS.is_file():
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
        s["currentProviderClaude"] = pid
        SETTINGS.write_text(
            json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"settings currentProviderClaude={pid}")
    print(f"switched claude -> {pid} ({name})")
    print("use scripts/claude_unified.ps1 or claude_code_start.ps1 to apply env")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("current")
    p_sw = sub.add_parser("switch")
    p_sw.add_argument("id")
    args = p.parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "current":
        return cmd_current(args)
    if args.cmd == "switch":
        return cmd_switch(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
