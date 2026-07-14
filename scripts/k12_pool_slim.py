#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K12 live pool slim — keep ever-used + newest unused candidates.

Shared K12 dumps can be 80k tokens for the same workspace. Loading them all
into chatgpt2api RAM is wasteful; a few thousand for rotation is enough.

Policy (community large-pool practice, local 16GB host):
  1. Always keep accounts that were ever used
     (last_used_at / text_success>0 / success>0).
  2. Among never-used, keep the newest ``--keep-recent`` by created_at.
  3. Snapshot backup before delete; VACUUM after; print before/after.

Safe modes:
  - default: delete via gateway admin API (runtime stays consistent), then
    optional direct VACUUM if the file is not locked hard.
  - --direct-sqlite: mutate DB offline (prefer gateway stopped / restart after).

Examples:
  python scripts/k12_pool_slim.py --dry-run
  python scripts/k12_pool_slim.py --keep-recent 1500
  python scripts/k12_pool_slim.py --keep-recent 1500 --direct-sqlite
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "chatgpt2api" / "data" / "accounts.db"
BACKUP_DIR = ROOT / "backups" / "k12_db"
GATEWAY = os.environ.get("K12_GATEWAY", "http://127.0.0.1:8124")
AUTH_KEY = os.environ.get("CHATGPT2API_AUTH_KEY", "k12-pool-local")
LOG = ROOT / "logs" / "k12_pool_slim.log"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def http_json(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 120.0,
) -> tuple[int, Any]:
    data = None
    headers = {"Authorization": f"Bearer {AUTH_KEY}"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{GATEWAY.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def parse_account(data: str | dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(data, dict):
        return data
    try:
        obj = json.loads(data)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def is_used(acc: dict[str, Any]) -> bool:
    if acc.get("last_used_at"):
        return True
    try:
        if int(acc.get("text_success") or 0) > 0:
            return True
    except Exception:
        pass
    try:
        if int(acc.get("success") or 0) > 0:
            return True
    except Exception:
        pass
    return False


def created_key(acc: dict[str, Any]) -> str:
    return str(acc.get("created_at") or acc.get("expires_at") or "")


def load_rows(db_path: Path) -> list[tuple[int, str, dict[str, Any]]]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT id, access_token, data FROM accounts").fetchall()
    finally:
        conn.close()
    out: list[tuple[int, str, dict[str, Any]]] = []
    for rid, tok, data in rows:
        acc = parse_account(data)
        if not acc:
            continue
        token = str(tok or acc.get("access_token") or "").strip()
        if not token:
            continue
        out.append((int(rid), token, acc))
    return out


def select_keep(
    rows: list[tuple[int, str, dict[str, Any]]],
    keep_recent: int,
) -> tuple[set[str], dict[str, int]]:
    used: list[tuple[int, str, dict[str, Any]]] = []
    never: list[tuple[int, str, dict[str, Any]]] = []
    for row in rows:
        if is_used(row[2]):
            used.append(row)
        else:
            never.append(row)
    never.sort(key=lambda r: created_key(r[2]), reverse=True)
    keep_tokens = {r[1] for r in used}
    for r in never[: max(0, keep_recent)]:
        keep_tokens.add(r[1])
    stats = {
        "total": len(rows),
        "used": len(used),
        "never": len(never),
        "keep_used": len(used),
        "keep_recent": min(len(never), max(0, keep_recent)),
        "keep_total": len(keep_tokens),
        "drop": len(rows) - len(keep_tokens),
    }
    return keep_tokens, stats


def snapshot_backup(db_path: Path, tag: str = "pre_slim") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"accounts.db.{tag}_{ts}"
    # online backup when possible
    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except Exception:
        shutil.copy2(db_path, dest)
    log(f"backup -> {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def delete_via_api(tokens: list[str], batch: int = 200) -> int:
    removed = 0
    for i in range(0, len(tokens), batch):
        chunk = tokens[i : i + batch]
        code, body = http_json("DELETE", "/api/accounts", {"tokens": chunk}, timeout=180)
        if code != 200:
            log(f"  api delete batch fail {code}: {str(body)[:160]}")
            continue
        if isinstance(body, dict):
            removed += int(body.get("removed") or body.get("deleted") or len(chunk))
        else:
            removed += len(chunk)
        log(f"  api deleted batch {i // batch + 1}: +{len(chunk)}")
        time.sleep(0.2)
    return removed


def delete_direct(db_path: Path, drop_ids: list[int], batch: int = 500) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        n = 0
        for i in range(0, len(drop_ids), batch):
            chunk = drop_ids[i : i + batch]
            conn.executemany("DELETE FROM accounts WHERE id = ?", [(x,) for x in chunk])
            n += len(chunk)
            if n % 2000 == 0:
                conn.commit()
                log(f"  direct deleted {n}/{len(drop_ids)}")
        conn.commit()
        log("  VACUUM ...")
        conn.execute("VACUUM")
        conn.commit()
        return n
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="K12 pool slim (used + newest unused)")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--keep-recent", type=int, default=1500, help="newest never-used to keep")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--direct-sqlite",
        action="store_true",
        help="delete rows in SQLite (restart gateway after for RAM reclaim)",
    )
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--max-drop", type=int, default=0, help="0=no cap; else max tokens to drop")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_file():
        log(f"missing db: {db_path}")
        return 1

    rows = load_rows(db_path)
    keep_tokens, stats = select_keep(rows, int(args.keep_recent))
    drop = [(rid, tok) for rid, tok, _ in rows if tok not in keep_tokens]
    if args.max_drop and len(drop) > args.max_drop:
        drop = drop[: int(args.max_drop)]

    log(
        f"slim plan total={stats['total']} used={stats['used']} never={stats['never']} "
        f"keep={stats['keep_total']} (used+recent) drop={len(drop)} "
        f"keep_recent={args.keep_recent}"
    )
    if not drop:
        log("nothing to drop")
        return 0
    if args.dry_run:
        log("dry-run: no changes")
        return 0

    if not args.no_backup:
        snapshot_backup(db_path, tag="pre_slim")

    before_mb = db_path.stat().st_size / (1024 * 1024)
    if args.direct_sqlite:
        n = delete_direct(db_path, [rid for rid, _ in drop])
        after_mb = db_path.stat().st_size / (1024 * 1024)
        log(
            f"done direct drop={n} size {before_mb:.1f}MB -> {after_mb:.1f}MB "
            f"(restart k12 gateway to reclaim process RAM)"
        )
    else:
        n = delete_via_api([tok for _, tok in drop])
        # best-effort vacuum if file not exclusively locked
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                live = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
                log(f"db rows now={live}; attempting VACUUM")
                conn.execute("VACUUM")
            finally:
                conn.close()
        except Exception as e:
            log(f"VACUUM skipped: {e}")
        after_mb = db_path.stat().st_size / (1024 * 1024)
        log(
            f"done api drop~={n} size {before_mb:.1f}MB -> {after_mb:.1f}MB "
            f"(gateway memory shrinks after process restart)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
