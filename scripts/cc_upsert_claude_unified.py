#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Upsert cc-switch claude provider pointing at CLIProxy :8337."""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
PROVIDER_ID = "claude-unified"
NAME = "Claude Unified (multi-relay)"
API_KEY = "sk-local-claude-unified-2026"
BASE = "http://127.0.0.1:8337"

ENV = {
    "ANTHROPIC_BASE_URL": BASE,
    "ANTHROPIC_AUTH_TOKEN": API_KEY,
    "ANTHROPIC_API_KEY": API_KEY,
    "ANTHROPIC_MODEL": "claude-opus-4-8",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "claude-opus-4-8",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-opus-4-7",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "claude-opus-4-7",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-opus-4-6",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": "claude-opus-4-6",
}
SETTINGS_CONFIG = json.dumps({"env": ENV}, ensure_ascii=False)


def main() -> None:
    if not DB.is_file():
        raise SystemExit(f"missing {DB}")
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = DB.with_name(f"cc-switch.db.bak-claude-unified-{ts}")
    shutil.copy2(DB, bak)
    print("backup", bak)
    c = sqlite3.connect(str(DB))
    cols = [r[1] for r in c.execute("PRAGMA table_info(providers)").fetchall()]
    now = int(time.time() * 1000)
    row = c.execute(
        "SELECT id FROM providers WHERE id=? AND app_type='claude'",
        (PROVIDER_ID,),
    ).fetchone()
    if row:
        if "updated_at" in cols:
            c.execute(
                "UPDATE providers SET name=?, settings_config=?, updated_at=? "
                "WHERE id=? AND app_type='claude'",
                (NAME, SETTINGS_CONFIG, now, PROVIDER_ID),
            )
        else:
            c.execute(
                "UPDATE providers SET name=?, settings_config=? "
                "WHERE id=? AND app_type='claude'",
                (NAME, SETTINGS_CONFIG, PROVIDER_ID),
            )
        print("updated", PROVIDER_ID)
    else:
        base = {
            "id": PROVIDER_ID,
            "name": NAME,
            "app_type": "claude",
            "settings_config": SETTINGS_CONFIG,
            "is_current": 0,
            "created_at": now,
            "notes": "CLIProxy claude-api-key multi-relay :8337",
            "icon": "",
        }
        use = [k for k in base if k in cols]
        c.execute(
            f"INSERT INTO providers ({','.join(use)}) VALUES ({','.join('?' for _ in use)})",
            tuple(base[k] for k in use),
        )
        print("inserted", PROVIDER_ID, "cols", use)
    c.commit()
    c.close()
    print("next: python scripts/cc_switch_claude_provider.py switch claude-unified")


if __name__ == "__main__":
    main()
