#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Switch Codex provider in cc-switch.db without CLI (schema v13 > CLI v5.9.0 max v11).

GUI (CC Switch 3.17+) bumped SQLite user_version to 13; SaladDay/cc-switch-cli 5.9.0
only supports schema <=11, so `cc-switch --app codex provider switch` fails.

This script updates:
  - providers.is_current for app_type=codex
  - settings.json currentProviderCodex
  - ~/.codex/auth.json OPENAI_API_KEY + config.toml from provider settings_config

Usage:
  python scripts/cc_switch_codex_provider.py list
  python scripts/cc_switch_codex_provider.py current
  python scripts/cc_switch_codex_provider.py switch k12-local-chatgpt2api
  python scripts/cc_switch_codex_provider.py switch mycodex-1782970213160
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
SETTINGS = Path(r"C:/Users/zhugu/.cc-switch/settings.json")
CODEX_HOME = Path(r"C:/Users/zhugu/.codex")
AUTH = CODEX_HOME / "auth.json"
CONFIG = CODEX_HOME / "config.toml"


def connect() -> sqlite3.Connection:
    if not DB.is_file():
        raise SystemExit(f"missing {DB}")
    return sqlite3.connect(str(DB))


def list_providers() -> list[tuple]:
    c = connect()
    try:
        return c.execute(
            "SELECT id, name, is_current FROM providers WHERE app_type='codex' ORDER BY is_current DESC, name"
        ).fetchall()
    finally:
        c.close()


def current_id() -> str | None:
    for pid, name, cur in list_providers():
        if cur:
            return str(pid)
    return None


def cmd_list(_: argparse.Namespace) -> int:
    for pid, name, cur in list_providers():
        mark = "*" if cur else " "
        print(f"{mark} {pid}  {name}")
    return 0


def cmd_current(_: argparse.Namespace) -> int:
    c = connect()
    try:
        row = c.execute(
            "SELECT id, name, settings_config FROM providers WHERE app_type='codex' AND is_current=1"
        ).fetchone()
    finally:
        c.close()
    if not row:
        print("no current codex provider")
        return 1
    pid, name, sc = row
    print(f"id:   {pid}")
    print(f"name: {name}")
    try:
        j = json.loads(sc)
        cfg = j.get("config") or ""
        for line in str(cfg).splitlines():
            if any(k in line for k in ("model_provider", "model =", "base_url", "wire_api")):
                print(f"cfg:  {line}")
        key = (j.get("auth") or {}).get("OPENAI_API_KEY", "")
        print(f"key:  {'set' if key else 'missing'} len={len(key)}")
    except Exception as e:
        print(f"settings_config parse err: {e}")
    return 0


def apply_live(settings_config: str) -> None:
    j = json.loads(settings_config)
    auth = j.get("auth") or {}
    config_toml = j.get("config") or ""
    if not isinstance(config_toml, str) or not config_toml.strip():
        raise SystemExit("provider has empty config TOML")

    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    # backup
    ts = time.strftime("%Y%m%d_%H%M%S")
    if CONFIG.is_file():
        shutil.copy2(CONFIG, CONFIG.with_suffix(f".toml.bak-{ts}"))
    if AUTH.is_file():
        shutil.copy2(AUTH, AUTH.with_suffix(f".json.bak-{ts}"))

    # Write config.toml: keep only provider-related top + model_providers blocks from
    # settings_config; preserve trailing mcp if present in old file is complex —
    # community path: full replace from provider config (cc-switch does the same).
    CONFIG.write_text(config_toml if config_toml.endswith("\n") else config_toml + "\n", encoding="utf-8")

    key = str(auth.get("OPENAI_API_KEY") or "").strip()
    AUTH.write_text(json.dumps({"OPENAI_API_KEY": key}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # model catalog already referenced inside TOML if present
    print(f"wrote {CONFIG}")
    print(f"wrote {AUTH} key_len={len(key)}")


def cmd_switch(args: argparse.Namespace) -> int:
    target = str(args.id).strip()
    c = connect()
    try:
        row = c.execute(
            "SELECT id, name, settings_config FROM providers WHERE id=? AND app_type='codex'",
            (target,),
        ).fetchone()
        if not row:
            print(f"provider not found for codex: {target}")
            print("available:")
            for pid, name, cur in list_providers():
                print(f"  {pid}  {name}")
            return 1
        pid, name, sc = row
        # backup db
        bak = DB.with_name(f"cc-switch.db.bak-switch-{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(DB, bak)
        print(f"backup {bak}")

        c.execute("UPDATE providers SET is_current=0 WHERE app_type='codex'")
        c.execute("UPDATE providers SET is_current=1 WHERE id=? AND app_type='codex'", (pid,))
        c.commit()
    finally:
        c.close()

    # settings.json
    if SETTINGS.is_file():
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
        s["currentProviderCodex"] = pid
        SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"settings currentProviderCodex={pid}")

    apply_live(sc)
    print(f"switched codex -> {pid} ({name})")
    print("restart Codex client to apply")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="cc-switch codex provider switch (schema-agnostic)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("current")
    p_sw = sub.add_parser("switch")
    p_sw.add_argument("id", help="provider id, e.g. k12-local-chatgpt2api")
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
