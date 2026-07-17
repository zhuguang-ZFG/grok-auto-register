#!/usr/bin/env python3
"""Idempotent CLIProxy port migration for Smart Router hand-off.

Maps the Grok CLIProxy config from public client port to internal port:
  - config.yaml        8317 -> 8318

Codex/Claude/GLM pools stay on their public ports in this first pass.

Use --reverse to restore original client ports from .before-router backups.
Only modifies lines matching ``^port:\\s*\\d+$``.  API keys and other content
are never changed or logged.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

CLI_PROXY_DIR = Path("D:/cli-proxy-api")

PUBLIC_PORTS = {
    "config.yaml": 8317,
}

INTERNAL_PORTS = {
    "config.yaml": 8318,
}

PORT_LINE_RE = re.compile(r"^port:\s*\d+$", re.MULTILINE)


def backup_path(config: Path) -> Path:
    return config.with_suffix(config.suffix + ".before-router")


def ensure_backup(config: Path) -> None:
    dst = backup_path(config)
    if not dst.exists():
        shutil.copy2(config, dst)
        print(f"backup: {dst.name}")


def migrate_one(config: Path, target_port: int) -> bool:
    text = config.read_text(encoding="utf-8")
    new_text, count = PORT_LINE_RE.subn(f"port: {target_port}", text)
    if count == 0:
        print(f"warn: no port line in {config.name}")
        return False
    config.write_text(new_text, encoding="utf-8")
    print(f"{config.name} -> {target_port}")
    return True


def restore_from_backup(config: Path) -> bool:
    dst = backup_path(config)
    if not dst.exists():
        print(f"warn: no backup for {config.name}; skipping restore")
        return False
    shutil.copy2(dst, config)
    print(f"restore: {config.name} from {dst.name}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate CLIProxy to internal ports")
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Restore public client ports from .before-router backups",
    )
    args = parser.parse_args()

    mapping = PUBLIC_PORTS if args.reverse else INTERNAL_PORTS

    ok = True
    for name, target_port in mapping.items():
        config = CLI_PROXY_DIR / name
        if not config.exists():
            print(f"error: {config} not found")
            ok = False
            continue

        if args.reverse:
            if not restore_from_backup(config):
                ok = False
        else:
            ensure_backup(config)
            if not migrate_one(config, target_port):
                ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
