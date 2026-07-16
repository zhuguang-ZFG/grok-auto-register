#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Upsert cc-switch codex provider pointing at CLIProxy codex unified :8327."""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
SETTINGS = Path(r"C:/Users/zhugu/.cc-switch/settings.json")
PROVIDER_ID = "codex-unified"
NAME = "Codex Unified (local+remote)"
API_KEY = "sk-local-codex-unified-2026"
BASE = "http://127.0.0.1:8327/v1"

CONFIG_TOML = f"""model_provider = "codexunified"
model = "gpt-5.6"
model_reasoning_effort = "none"

[model_providers.codexunified]
name = "Codex Unified"
base_url = "{BASE}"
wire_api = "responses"
requires_openai_auth = true

[model_providers.codexunified.http_headers]
User-Agent = "codex-cli"
"""

SETTINGS_CONFIG = json.dumps(
    {
        "auth": {"OPENAI_API_KEY": API_KEY},
        "config": CONFIG_TOML,
    },
    ensure_ascii=False,
)


def main() -> None:
    if not DB.is_file():
        raise SystemExit(f"missing {DB}")
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = DB.with_name(f"cc-switch.db.bak-codex-unified-{ts}")
    shutil.copy2(DB, bak)
    print("backup", bak)
    c = sqlite3.connect(str(DB))
    cols = [r[1] for r in c.execute("PRAGMA table_info(providers)").fetchall()]
    print("providers cols", cols)
    row = c.execute(
        "SELECT id FROM providers WHERE id=? AND app_type='codex'",
        (PROVIDER_ID,),
    ).fetchone()
    now = int(time.time() * 1000)
    if row:
        # update known columns only
        if "updated_at" in cols:
            c.execute(
                "UPDATE providers SET name=?, settings_config=?, updated_at=? "
                "WHERE id=? AND app_type='codex'",
                (NAME, SETTINGS_CONFIG, now, PROVIDER_ID),
            )
        else:
            c.execute(
                "UPDATE providers SET name=?, settings_config=? "
                "WHERE id=? AND app_type='codex'",
                (NAME, SETTINGS_CONFIG, PROVIDER_ID),
            )
        print("updated", PROVIDER_ID)
    else:
        # build insert from available columns with safe defaults
        base = {
            "id": PROVIDER_ID,
            "name": NAME,
            "app_type": "codex",
            "settings_config": SETTINGS_CONFIG,
            "is_current": 0,
        }
        if "created_at" in cols:
            base["created_at"] = now
        if "updated_at" in cols:
            base["updated_at"] = now
        if "sort_order" in cols:
            base["sort_order"] = 0
        if "notes" in cols:
            base["notes"] = ""
        if "icon" in cols:
            base["icon"] = ""
        use_cols = [c for c in base if c in cols]
        placeholders = ",".join("?" for _ in use_cols)
        c.execute(
            f"INSERT INTO providers ({','.join(use_cols)}) VALUES ({placeholders})",
            tuple(base[c] for c in use_cols),
        )
        print("inserted", PROVIDER_ID, "cols", use_cols)
    c.commit()
    c.close()
    if SETTINGS.is_file():
        # do not force current here; switch script does
        print("settings present; switch next")
    print("next: python scripts/cc_switch_codex_provider.py switch codex-unified")


if __name__ == "__main__":
    main()
