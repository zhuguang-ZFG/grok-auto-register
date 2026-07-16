#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot restart chatgpt2api on :8124 (hidden via PowerShell)."""
from __future__ import annotations

import subprocess
import time
import urllib.request


def models_ok() -> bool:
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8124/v1/models",
            headers={"Authorization": "Bearer k12-pool-local"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception:
        return False


def listen_pids(port: int = 8124) -> set[int]:
    out = subprocess.check_output("netstat -ano", shell=True, text=True, errors="replace")
    pids: set[int] = set()
    needle = f":{port}"
    for line in out.splitlines():
        if needle in line and "LISTENING" in line:
            parts = line.split()
            try:
                pids.add(int(parts[-1]))
            except Exception:
                pass
    return pids


def main() -> int:
    pids = listen_pids()
    print("listen_pids", pids, "models_before", models_ok())
    for pid in pids:
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
            print("killed", pid)
    time.sleep(2)

    ps = r"""
$env:STORAGE_BACKEND = 'sqlite'
$env:DATABASE_URL = 'sqlite:///D:/Users/grok-auto-register/chatgpt2api/data/accounts.db'
$env:CHATGPT2API_AUTH_KEY = 'k12-pool-local'
$p = Start-Process -FilePath 'uv' `
  -ArgumentList @('run','uvicorn','main:app','--host','127.0.0.1','--port','8124','--log-level','warning','--timeout-keep-alive','30','--limit-concurrency','10') `
  -WorkingDirectory 'D:\Users\grok-auto-register\chatgpt2api' `
  -WindowStyle Hidden -PassThru
$p.Id | Out-File 'D:\Users\grok-auto-register\logs\chatgpt2api_gateway.pid' -Encoding utf8
Write-Output $p.Id
"""
    out = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", ps],
        text=True,
        errors="replace",
    )
    print("started_pid", out.strip())
    for i in range(25):
        time.sleep(2)
        if models_ok():
            print("gateway_up_after_sec", (i + 1) * 2)
            return 0
    print("gateway_still_down")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
