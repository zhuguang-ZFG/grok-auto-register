#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Soft-restart grok_register_ttk.py auto (single instance)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "register_auto.out.log"
PY = r"C:\Users\zhugu\scoop\apps\python313\current\python.exe"


def main() -> int:
    try:
        import psutil
    except ImportError:
        print("psutil missing")
        return 1

    for p in list(psutil.process_iter(["pid", "cmdline"])):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "grok_register_ttk.py" in cmd and ("auto" in cmd or "start" in cmd):
                print(f"terminate pid={p.info['pid']}")
                p.terminate()
        except Exception as exc:
            print(f"skip: {exc}")

    time.sleep(3)
    for p in list(psutil.process_iter(["pid", "cmdline"])):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "grok_register_ttk.py" in cmd:
                print(f"still alive pid={p.info['pid']} -> terminate again")
                p.terminate()
        except Exception:
            pass
    time.sleep(2)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(LOG, "a", encoding="utf-8")
    logf.write("\n=== RESTART auto (cf resend fix + mix 0.6) ===\n")
    logf.flush()
    subprocess.Popen(
        [PY, "-u", str(ROOT / "grok_register_ttk.py"), "auto"],
        cwd=str(ROOT),
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    time.sleep(4)
    n = 0
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "grok_register_ttk.py auto" in cmd and "python" in cmd.lower():
                print(f"alive pid={p.info['pid']}")
                n += 1
        except Exception:
            pass
    print(f"auto_count={n}")
    return 0 if n >= 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
