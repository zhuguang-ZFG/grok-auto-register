#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Force-restart grok_register_ttk.py auto and print status."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "register_auto.out.log"
PY = r"C:\Users\zhugu\scoop\apps\python313\current\python.exe"
SCRIPT = ROOT / "grok_register_ttk.py"


def _cmd_of(p) -> str:
    try:
        return " ".join(p.info.get("cmdline") or [])
    except Exception:
        return ""


def find_register_pids():
    import psutil

    out = []
    for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            cmd = _cmd_of(p)
            if "grok_register_ttk.py" in cmd:
                out.append((p.info["pid"], cmd, p.info.get("create_time") or 0))
        except Exception:
            pass
    return out


def main() -> int:
    import psutil

    before = find_register_pids()
    print(f"before={len(before)}")
    for pid, cmd, ct in before:
        print(f"  kill pid={pid}")
        try:
            psutil.Process(pid).terminate()
        except Exception as e:
            print(f"  terminate err: {e}")

    time.sleep(3)
    for pid, cmd, ct in find_register_pids():
        print(f"  kill -9 pid={pid}")
        try:
            psutil.Process(pid).kill()
        except Exception as e:
            print(f"  kill err: {e}")

    time.sleep(2)
    still = find_register_pids()
    if still:
        print(f"WARN still_alive={len(still)}")
        for pid, cmd, ct in still:
            print(f"  alive {pid} {cmd[:100]}")
        return 3

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as logf:
        logf.write(
            "\n=== RESTART auto "
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
            "(reg_metrics + sso_timeout_rotate) ===\n"
        )
        logf.flush()
        # reopen append for child lifetime — use separate handle
    logf = open(LOG, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [PY, "-u", str(SCRIPT), "auto"],
        cwd=str(ROOT),
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print(f"started pid={proc.pid}")
    time.sleep(5)
    after = find_register_pids()
    print(f"after={len(after)}")
    for pid, cmd, ct in after:
        age = int(time.time() - (ct or time.time()))
        print(f"  pid={pid} age_s={age}")
    # check log grew
    time.sleep(3)
    size = LOG.stat().st_size
    print(f"log_size={size}")
    # last lines
    try:
        raw = LOG.read_bytes()[-4000:]
        try:
            t = raw.decode("utf-8")
        except Exception:
            t = raw.decode("gbk", "replace")
        for line in [l for l in t.splitlines() if l.strip()][-12:]:
            # ascii-safe print
            print(line.encode("ascii", "backslashreplace").decode("ascii")[:180])
    except Exception as e:
        print(f"tail_err={e}")
    return 0 if after else 2


if __name__ == "__main__":
    raise SystemExit(main())
