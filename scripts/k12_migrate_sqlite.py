#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Migrate chatgpt2api pool JSON -> SQLite (community large-pool practice).

Schema matches services/storage/database_storage.py AccountModel:
  id INTEGER PK AUTOINCREMENT
  access_token VARCHAR(2048) UNIQUE NOT NULL
  data TEXT NOT NULL  # full account JSON

Usage (prefer gateway STOPPED):
  python scripts/k12_migrate_sqlite.py
  python scripts/k12_migrate_sqlite.py --force

Restart:
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


def load_accounts(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("accounts"), list):
        return [x for x in raw["accounts"] if isinstance(x, dict)]
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        return [v for v in raw.values() if isinstance(v, dict)]
    raise SystemExit(f"unsupported json shape: {type(raw)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", default=str(JSON_PATH))
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    json_path = Path(args.json)
    db_path = Path(args.db)
    if not json_path.is_file():
        print(f"missing {json_path}")
        return 1
    if db_path.exists() and not args.force:
        print(f"{db_path} exists; pass --force to overwrite")
        return 2

    sys.path.insert(0, str(GW))
    os.chdir(GW)
    try:
        from sqlalchemy import Column, Integer, String, Text, create_engine
        from sqlalchemy.orm import declarative_base, sessionmaker
    except ImportError:
        print("sqlalchemy missing; cd chatgpt2api && uv sync")
        return 1

    print(f"loading {json_path} ({json_path.stat().st_size / 1e6:.1f} MB)...")
    items = load_accounts(json_path)
    print(f"accounts in json: {len(items)}")

    Base = declarative_base()

    class AccountModel(Base):
        __tablename__ = "accounts"
        id = Column(Integer, primary_key=True, autoincrement=True)
        access_token = Column(String(2048), unique=True, nullable=False, index=True)
        data = Column(Text, nullable=False)

    if db_path.exists() and args.force:
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            side = Path(str(db_path) + suffix)
            if side.exists():
                side.unlink()

    url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)

    n = 0
    skipped = 0
    with Session() as sess:
        for item in items:
            tok = str(item.get("access_token") or item.get("accessToken") or "").strip()
            if not tok:
                skipped += 1
                continue
            # truncate only if somehow longer than column; real JWTs are <2k
            if len(tok) > 2048:
                tok = tok[:2048]
            sess.add(AccountModel(access_token=tok, data=json.dumps(item, ensure_ascii=False)))
            n += 1
            if n % 5000 == 0:
                sess.commit()
                print(f"  wrote {n}...")
        sess.commit()

    mb = db_path.stat().st_size / (1024 * 1024)
    print(f"done: {n} rows (skipped {skipped}) -> {db_path} ({mb:.1f} MB)")
    print("")
    print("Next (restart gateway):")
    print(f'  cd /d "{GW}"')
    print("  set STORAGE_BACKEND=sqlite")
    print(f"  set DATABASE_URL=sqlite:///{db_path.as_posix()}")
    print("  set CHATGPT2API_AUTH_KEY=<local-key>")
    print("  uv run uvicorn main:app --host 127.0.0.1 --port 8124 --log-level warning")
    print("Keep accounts.json as cold backup until status/chat probe OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
