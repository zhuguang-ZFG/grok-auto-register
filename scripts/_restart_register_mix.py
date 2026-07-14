#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Soft-restart register auto after mail mix changes."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "register_auto.out.log"
PY = r"C:\Users\zhugu\scoop\apps\python313\current\python.exe"


def main() -> int:
    import psutil

    for p in list(psutil.process_iter(["pid", "cmdline"])):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "grok_register_ttk.py" in cmd:
                print(f"terminate pid={p.info['pid']}")
                p.terminate()
        except Exception as exc:
            print(f"skip: {exc}")
    time.sleep(4)
    for p in list(psutil.process_iter(["pid", "cmdline"])):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "grok_register_ttk.py" in cmd:
                print(f"kill pid={p.info['pid']}")
                p.kill()
        except Exception:
            pass
    time.sleep(2)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(LOG, "a", encoding="utf-8")
    logf.write("\n=== RESTART auto (tempmail_lol + mailtm + yunmeng mix) ===\n")
    logf.flush()
    subprocess.Popen(
        [PY, "-u", str(ROOT / "grok_register_ttk.py"), "auto"],
        cwd=str(ROOT),
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    time.sleep(5)
    n = 0
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "grok_register_ttk.py" in cmd and "auto" in cmd:
                print(f"alive pid={p.info['pid']}")
                n += 1
        except Exception:
            pass
    print(f"auto_count={n}")
    return 0 if n else 2


if __name__ == "__main__":
    raise SystemExit(main())
