#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Migrate chatgpt2api account pool from JSON file backend to SQLite.

Community guidance for large pools (~10k+): prefer STORAGE_BACKEND=sqlite
over a multi-hundred-MB accounts.json (slower load/save, bigger RAM spike).

This script:
  1) Reads current chatgpt2api/data/accounts.json (or gateway export)
  2) Writes chatgpt2api/data/accounts.db via SQLAlchemy schema used by chatgpt2api
  3) Prints env flags to restart gateway on sqlite

Safety:
  - Does NOT delete accounts.json (keeps as backup)
  - Stops if accounts.db already exists unless --force
  - Run with gateway STOPPED for clean cutover

Usage:
  # gateway must be stopped
  python scripts/k12_migrate_sqlite.py
  python scripts/k12_migrate_sqlite.py --force

Then start gateway with:
  set STORAGE_BACKEND=sqlite
  set DATABASE_URL=sqlite:///D:/Users/grok-auto-register/chatgpt2api/data/accounts.db
  set CHATGPT2API_AUTH_KEY=...
  uv run uvicorn main:app --host 127.0.0.1 --port 8124
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GW = ROOT / "chatgpt2api"
DATA = GW / "data"
JSON_PATH = DATA / "accounts.json"
DB_PATH = DATA / "accounts.db"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="overwrite existing accounts.db")
    p.add_argument("--json", default=str(JSON_PATH))
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args()

    json_path = Path(args.json)
    db_path = Path(args.db)
    if not json_path.is_file():
        print(f"missing {json_path}")
        return 1
    if db_path.exists() and not args.force:
        print(f"{db_path} exists; pass --force to overwrite")
        return 2

    # Use chatgpt2api venv deps via path insert + import after chdir
    sys.path.insert(0, str(GW))
    os.chdir(GW)

    try:
        from sqlalchemy import Column, Integer, String, Text, create_engine
        from sqlalchemy.orm import declarative_base, sessionmaker
    except ImportError:
        print("sqlalchemy missing; run: cd chatgpt2api && uv sync")
        return 1

    print(f"loading {json_path} ...")
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    # chatgpt2api json backend shapes: dict token->account or {accounts:[...]} or list
    if isinstance(raw, dict) and "accounts" in raw and isinstance(raw["accounts"], list):
        items = raw["accounts"]
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = list(raw.values())
    else:
        print("unsupported json shape")
        return 1
    print(f"accounts: {len(items)}")

    Base = declarative_base()

    class AccountRow(Base):
        __tablename__ = "accounts"
        # Match chatgpt2api DatabaseStorageBackend as closely as practical.
        # If schema drifts, prefer gateway-native export/import after first sqlite boot.
        access_token = Column(String(4096), primary_key=True)
        payload = Column(Text, nullable=False)

    if db_path.exists() and args.force:
        db_path.unlink()

    url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)

    n = 0
    with Session() as sess:
        for item in items:
            if not isinstance(item, dict):
                continue
            tok = str(item.get("access_token") or item.get("accessToken") or "").strip()
            if not tok:
                continue
            sess.merge(AccountRow(access_token=tok, payload=json.dumps(item, ensure_ascii=False)))
            n += 1
            if n % 5000 == 0:
                sess.commit()
                print(f"  wrote {n}...")
        sess.commit()

    size_mb = db_path.stat().st_size / (1024 * 1024)
    print(f"done: {n} rows -> {db_path} ({size_mb:.1f} MB)")
    print("")
    print("Restart gateway with:")
    print(f'  cd "{GW}"')
    print("  set STORAGE_BACKEND=sqlite")
    print(f'  set DATABASE_URL=sqlite:///{db_path.as_posix()}')
    print("  set CHATGPT2API_AUTH_KEY=<your-local-key>")
    print("  uv run uvicorn main:app --host 127.0.0.1 --port 8124 --log-level warning")
    print("")
    print("NOTE: If gateway sqlite schema differs, use admin export/import after first sqlite boot.")
    print("Keep accounts.json as cold backup until verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
