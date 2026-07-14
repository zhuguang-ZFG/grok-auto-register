#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K12 网关每日 SQLite 快照备份。

在 WAL 模式下，运行时复制 accounts.db 是安全的（SQLite 的 online backup API）。
备份保留最近 N 天，旧备份自动清理。

用法:
    python k12_daily_backup.py              # 立即备份
    python k12_daily_backup.py --retention 7  # 保留 7 天

可配为 Windows 计划任务，每天凌晨跑一次。
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path("D:/Users/grok-auto-register/chatgpt2api/data/accounts.db")
BACKUP_DIR = Path("D:/Users/grok-auto-register/backups/k12_db")
DEFAULT_RETENTION_DAYS = 14


def backup_db(retention_days: int = DEFAULT_RETENTION_DAYS) -> Path:
    """用 SQLite online backup API 做一致性快照，不影响网关运行。"""
    if not DB_PATH.exists():
        print(f"[backup] DB not found: {DB_PATH}")
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"accounts_{timestamp}.db"

    # SQLite online backup：在 WAL 模式下安全，不阻塞写入。
    src_conn = sqlite3.connect(str(DB_PATH))
    dst_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"[backup] created {dest.name} ({size_mb:.1f} MB)")

    # 清理过期备份
    cutoff = datetime.now().timestamp() - retention_days * 86400
    removed = 0
    for old in BACKUP_DIR.glob("accounts_*.db"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
            removed += 1
    if removed:
        print(f"[backup] cleaned {removed} old backups (>{retention_days}d)")

    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="K12 SQLite daily backup")
    parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION_DAYS,
                        help=f"保留天数 (default: {DEFAULT_RETENTION_DAYS})")
    args = parser.parse_args()
    backup_db(args.retention)


if __name__ == "__main__":
    main()
