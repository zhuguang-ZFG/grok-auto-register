#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Switch pool preference: burn buffer first vs protect own domains.

  python set_pool_prefer.py status
  python set_pool_prefer.py buffer   # hold own → CLIProxy uses lsw666 first
  python set_pool_prefer.py own      # release hold → own first again
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_cfg() -> dict:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_cfg(cfg: dict) -> None:
    p = ROOT / "config.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def auth_dir(cfg: dict) -> Path:
    d = Path(str(cfg.get("cpa_auth_dir") or "cpa_auths"))
    return d if d.is_absolute() else (ROOT / d).resolve()


def main(argv: list[str] | None = None) -> int:
    try:
        import stdio_utf8  # noqa: F401
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Prefer buffer or own domain pool")
    ap.add_argument(
        "action",
        choices=["status", "buffer", "own", "buffer_first", "own_first", "check"],
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    action = args.action
    if action == "buffer_first":
        action = "buffer"
    if action == "own_first":
        action = "own"

    cfg = load_cfg()
    from pool_policy import (
        count_live_tiers,
        ensure_buffer_failover,
        hold_own_for_buffer,
        prefer_mode,
        release_own_hold,
        summarize_pool_files,
    )

    ad = auth_dir(cfg)
    files = list(ad.glob("xai-*.json")) if ad.is_dir() else []
    summary = summarize_pool_files(files, cfg)

    if action == "status":
        mode = prefer_mode(cfg)
        held = 0
        for p in files:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if d.get("hold_reason") == "prefer_buffer":
                held += 1
        tiers = count_live_tiers(ad, cfg)
        print(f"[*] pool_prefer_mode={mode}")
        print(f"[*] own={summary['own']} buffer={summary['buffer']} total={summary['total']}")
        print(f"[*] own soft-held (prefer_buffer)={held}")
        print(
            f"[*] live own={tiers['own_live']} buffer={tiers['buffer_live']} "
            f"(failover min={cfg.get('pool_buffer_min_live', 50)})"
        )
        print(
            f"[*] auto_failover={cfg.get('pool_buffer_failover_enabled', True)} "
            f"auto_recover={cfg.get('pool_buffer_auto_recover', False)}"
        )
        print("[*] buffer → CLIProxy burns third-party first; own held")
        print("[*] own    → restore own domains into rotation")
        print("[*] check  → run buffer-low auto failover once (dry-run with --dry-run)")
        return 0

    if action == "check":
        fo = ensure_buffer_failover(
            ad,
            cfg,
            config_path=None if args.dry_run else (ROOT / "config.json"),
            dry_run=bool(args.dry_run),
        )
        print(f"[*] ensure_buffer_failover: {fo}")
        return 0

    if action == "buffer":
        cfg["pool_prefer_mode"] = "buffer_first"
        cfg["pool_prefer"] = "buffer_first"
        cfg["prefer_mode"] = "buffer_first"
        cfg["pool_local_use_buffer"] = True
        if not args.dry_run:
            save_cfg(cfg)
        st = hold_own_for_buffer(ad, cfg, dry_run=bool(args.dry_run))
        print(f"[+] mode=buffer_first dry_run={args.dry_run}")
        print(f"[+] hold own: {st}")
        print("[*] CLIProxy will skip held own auths (disabled:true) and use buffer")
        print("[*] auto: buffer_live < pool_buffer_min_live → release own (maintain/check)")
        print("[*] restore: python set_pool_prefer.py own")
        return 0

    if action == "own":
        cfg["pool_prefer_mode"] = "own_first"
        cfg["pool_prefer"] = "own_first"
        cfg["prefer_mode"] = "own_first"
        if not args.dry_run:
            save_cfg(cfg)
        st = release_own_hold(ad, cfg, dry_run=bool(args.dry_run))
        print(f"[+] mode=own_first dry_run={args.dry_run}")
        print(f"[+] release own hold: {st}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
