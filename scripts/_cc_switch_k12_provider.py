#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Upsert local K12 chatgpt2api as a cc-switch codex provider + model catalog."""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
CATALOG = Path(r"C:/Users/zhugu/.codex/cc-switch-model-catalog.json")
PROVIDER_ID = "k12-local-chatgpt2api"
API_KEY = "k12-pool-local"
BASE = "http://127.0.0.1:8124/v1"

BASE_INSTR = (
    "You are Codex, a coding agent. You and the user share the same workspace "
    "and collaborate to achieve the user's goals."
)
REASONING_LEVELS = [
    {"description": "Disable Thinking", "effort": "none"},
    {"description": "Enabled Thinking", "effort": "high"},
]


def model_entry(slug: str, ctx: int, priority: int, default_effort: str = "none") -> dict:
    return {
        "additional_speed_tiers": [],
        "availability_nux": None,
        "base_instructions": BASE_INSTR,
        "context_window": ctx,
        "default_reasoning_level": default_effort,
        "default_reasoning_summary": "none",
        "description": slug,
        "display_name": slug,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text"],
        "max_context_window": ctx,
        "priority": priority,
        "service_tiers": [],
        "shell_type": "shell_command",
        "slug": slug,
        "support_verbosity": False,
        "supported_in_api": True,
        "supported_reasoning_levels": REASONING_LEVELS,
        "supports_image_detail_original": False,
        "supports_parallel_tool_calls": False,
        "supports_reasoning_summaries": True,
        "supports_search_tool": False,
        "truncation_policy": {"limit": 10000, "mode": "bytes"},
        "upgrade": None,
        "visibility": "list",
    }


# 1M for 5.5/5.6 family; 400k for older gpt-5
MODELS = [
    ("gpt-5.6", 1_000_000, 100),
    ("gpt-5.6-sol", 1_000_000, 101),
    ("gpt-5.6-terra", 1_000_000, 102),
    ("gpt-5.6-luna", 1_000_000, 103),
    ("gpt-5-5", 1_000_000, 110),
    ("gpt-5.5", 1_000_000, 111),
    ("gpt-5", 400_000, 200),
    ("gpt-5-1", 400_000, 201),
    ("gpt-5-2", 400_000, 202),
    ("gpt-5-3", 400_000, 203),
    ("gpt-5-3-mini", 400_000, 204),
    ("gpt-5-mini", 400_000, 205),
    ("gpt-5-5-mini", 400_000, 206),
    ("auto", 400_000, 300),
]


def main() -> None:
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = DB.with_name(f"cc-switch.db.bak-k12-{ts}")
    shutil.copy2(DB, bak)
    print("backup", bak)

    existing: dict = {"models": []}
    if CATALOG.exists():
        shutil.copy2(CATALOG, CATALOG.with_suffix(f".json.bak-k12-{ts}"))
        existing = json.loads(CATALOG.read_text(encoding="utf-8"))

    by_slug: dict[str, dict] = {}
    for m in existing.get("models") or []:
        if isinstance(m, dict) and m.get("slug"):
            by_slug[str(m["slug"])] = m
    for slug, ctx, pri in MODELS:
        by_slug[slug] = model_entry(slug, ctx, pri, default_effort="none")

    catalog = {
        "models": sorted(
            by_slug.values(),
            key=lambda x: (x.get("priority", 9999), x.get("slug", "")),
        )
    }
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("catalog models", len(catalog["models"]), "path", CATALOG)

    catalog_path = CATALOG.as_posix()

    config_toml = f"""model_provider = "k12local"
model = "gpt-5.6"
model_reasoning_effort = "none"
disable_response_storage = true
model_catalog_json = "{catalog_path}"
windows_wsl_setup_acknowledged = true
sandbox_mode = "workspace-write"

[model_providers.k12local]
name = "K12 Local chatgpt2api"
base_url = "{BASE}"
wire_api = "responses"
requires_openai_auth = true

[model_providers.k12local.http_headers]
User-Agent = "codex_cli_rs/0.144.1"
"""

    model_catalog_ui = {
        "models": [{"model": slug, "displayName": slug} for slug, _, _ in MODELS]
    }

    settings_config = {
        "auth": {"OPENAI_API_KEY": API_KEY},
        "config": config_toml,
        "modelCatalog": model_catalog_ui,
    }

    meta = json.dumps(
        {
            "commonConfigEnabled": False,
            "endpointAutoSelect": True,
            "apiFormat": "openai_responses",
            "localK12": True,
            "note": "Shared K12 pool via chatgpt2api; no RT; window ~2026-07-23",
        },
        ensure_ascii=False,
    )

    now = int(time.time() * 1000)
    sc = json.dumps(settings_config, ensure_ascii=False)

    conn = sqlite3.connect(str(DB))
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT id FROM providers WHERE id=?", (PROVIDER_ID,)).fetchone()
        if row:
            cur.execute(
                """UPDATE providers SET name=?, settings_config=?, website_url=?, meta=?,
                   provider_type=?, is_current=0 WHERE id=?""",
                (
                    "K12 Local chatgpt2api",
                    sc,
                    BASE,
                    meta,
                    "openai_compatible",
                    PROVIDER_ID,
                ),
            )
            print("updated provider", PROVIDER_ID)
        else:
            cur.execute(
                """INSERT INTO providers
                   (id, app_type, name, settings_config, website_url, category,
                    created_at, sort_index, notes, icon, icon_color, meta,
                    is_current, in_failover_queue, cost_multiplier, limit_daily_usd,
                    limit_monthly_usd, provider_type)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    PROVIDER_ID,
                    "codex",
                    "K12 Local chatgpt2api",
                    sc,
                    BASE,
                    "local",
                    now,
                    0,
                    "Local shared K12 via chatgpt2api SQLite pool; wire_api=responses",
                    None,
                    None,
                    meta,
                    0,
                    0,
                    "0.0",
                    None,
                    None,
                    "openai_compatible",
                ),
            )
            print("inserted provider", PROVIDER_ID)

        cols = [r[1] for r in cur.execute("PRAGMA table_info(provider_endpoints)").fetchall()]
        print("endpoint cols", cols)
        exist_ep = cur.execute(
            "SELECT * FROM provider_endpoints WHERE provider_id=?", (PROVIDER_ID,)
        ).fetchall()
        if not exist_ep and "provider_id" in cols and "url" in cols:
            mapping = {
                "provider_id": PROVIDER_ID,
                "url": BASE,
                "name": "k12-local",
                "is_active": 1,
                "priority": 0,
                "created_at": now,
            }
            fields = [c for c in cols if c in mapping]
            vals = [mapping[c] for c in fields]
            if fields:
                q = (
                    f"INSERT INTO provider_endpoints ({','.join(fields)}) "
                    f"VALUES ({','.join('?' * len(fields))})"
                )
                try:
                    cur.execute(q, vals)
                    print("endpoint inserted")
                except Exception as e:
                    print("endpoint skip", e)
        else:
            print("endpoint skip exist_or_schema", len(exist_ep))

        conn.commit()
        r = cur.execute(
            "SELECT id,name,app_type,is_current FROM providers WHERE id=?",
            (PROVIDER_ID,),
        ).fetchone()
        print("verify", r)
        cur_row = cur.execute(
            "SELECT id,name,is_current FROM providers WHERE app_type='codex' AND is_current=1"
        ).fetchone()
        print("current codex", cur_row)

        # sanity: config TOML contains base_url
        sc2 = cur.execute(
            "SELECT settings_config FROM providers WHERE id=?", (PROVIDER_ID,)
        ).fetchone()[0]
        j = json.loads(sc2)
        assert "k12local" in j["config"]
        assert BASE in j["config"]
        assert j["auth"]["OPENAI_API_KEY"] == API_KEY
        print("settings_config ok")
    finally:
        conn.close()
    print("done")


if __name__ == "__main__":
    main()
