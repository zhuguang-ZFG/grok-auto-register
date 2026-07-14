#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified stack supervisor for the grok-auto-register local fleet.

One entry point to see (and optionally revive) every long-running service.
Replaces the "5 scattered VBS + scheduled-task" mental model with a single
`status` view. Deliberately conservative:

- ``status`` (default): read-only. Probe every service, print a table, exit 0
  if all core services are UP else exit 1. Safe to call anytime / from cron.
- ``ensure``: for any DOWN service, run its documented start command once.
  Never kills anything. Idempotent — each launcher already self-guards against
  double-start (port-in-use / PID lock).

Health check tiers (cheapest first, matching how each service actually fails):
- http    : GET a lightweight endpoint (``/healthz`` or ``/v1/models``), 3s timeout
- port    : TCP connect only (service has no cheap HTTP health route)
- process : a matching process command line exists (background workers, no port)

Usage:
    python scripts/supervisor.py                 # status table
    python scripts/supervisor.py status --json    # machine-readable
    python scripts/supervisor.py ensure           # revive DOWN core services
    python scripts/supervisor.py ensure --all      # also revive optional ones
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A2A_DIR = Path(r"C:/Users/zhugu/.kimi-code/mcp-a2a-bridge")


@dataclass
class Service:
    name: str
    kind: str  # "http" | "port" | "process"
    core: bool = True
    port: int | None = None
    http_path: str = "/healthz"
    proc_match: str = ""  # substring to find in process command line
    start_cmd: list[str] | None = None  # command to (re)launch when DOWN
    start_cwd: Path | None = None
    note: str = ""


SERVICES: list[Service] = [
    Service(
        name="cliproxy",
        kind="port",
        port=8317,
        start_cmd=["wscript.exe", "//B", "//Nologo", str(ROOT / "start_cliproxy_hidden.vbs")],
        note="grok-4.5 upstream proxy (needs API key; port-check only)",
    ),
    Service(
        name="k12_gateway",
        kind="http",
        port=8124,
        http_path="/healthz",
        note="chatgpt2api (GPT-5.x) — K12StackWatchdog; slim/refill: k12_pool_*.py / K12-Pool-Maintain",
    ),
    Service(
        name="cpa_inspect",
        kind="http",
        port=18318,
        http_path="/healthz",
        core=False,
        start_cmd=["wscript.exe", "//B", "//Nologo", str(ROOT / "start_cpa_inspect_hidden.vbs")],
        note="auth pool inspector UI",
    ),
    Service(
        name="a2a_bridge",
        kind="port",
        port=41242,
        core=False,
        note="A2A MCP bridge — revived by A2A-Bridge-Watchdog task",
    ),
    Service(
        name="quota_watch",
        kind="process",
        proc_match="quota_watch.py",
        start_cmd=["wscript.exe", "//B", "//Nologo", str(ROOT / "start_quota_watch_hidden.vbs")],
        note="pool rotation / 429 guard",
    ),
    Service(
        name="register",
        kind="process",
        core=False,
        proc_match="grok_register_ttk.py",
        start_cmd=["wscript.exe", "//B", "//Nologo", str(ROOT / "start_register_hidden.vbs")],
        note="auto CPA registration",
    ),
]


def _http_ok(port: int, path: str, timeout: float = 3.0) -> tuple[bool, str]:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        t0 = time.time()
        with urllib.request.urlopen(url, timeout=timeout) as r:
            code = r.getcode()
            dt = (time.time() - t0) * 1000
            return (200 <= code < 500, f"{code} {dt:.0f}ms")
    except Exception as exc:
        return (False, type(exc).__name__)


def _port_open(port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False


def _proc_running(match: str) -> bool:
    """Windows: find a python/node process whose command line contains match."""
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process "
                    "| Where-Object { $_.CommandLine -like '*"
                    + match
                    + "*' } | Measure-Object | Select-Object -ExpandProperty Count"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return int((out.stdout or "0").strip() or "0") > 0
    except Exception:
        return False


def check(svc: Service) -> dict:
    up = False
    detail = ""
    if svc.kind == "http" and svc.port:
        up, detail = _http_ok(svc.port, svc.http_path)
        if not up:
            # port may be LISTEN but health slow/under-load — treat port as alive
            if _port_open(svc.port):
                up, detail = True, f"port-open ({detail})"
    elif svc.kind == "port" and svc.port:
        up = _port_open(svc.port)
        detail = "port-open" if up else "closed"
    elif svc.kind == "process":
        up = _proc_running(svc.proc_match)
        detail = "running" if up else "not-found"
    return {"name": svc.name, "up": up, "detail": detail, "core": svc.core, "note": svc.note}


def do_status(as_json: bool) -> int:
    rows = [check(s) for s in SERVICES]
    if as_json:
        print(json.dumps({"services": rows, "ts": time.time()}, ensure_ascii=False))
    else:
        print("== stack supervisor ==")
        for r in rows:
            flag = "UP  " if r["up"] else "DOWN"
            tag = "core" if r["core"] else "opt "
            print(f"  [{flag}] {tag} {r['name']:14s} {r['detail']:22s} {r['note']}")
    core_down = [r for r in rows if r["core"] and not r["up"]]
    return 1 if core_down else 0


def do_ensure(include_optional: bool) -> int:
    rows = []
    for svc in SERVICES:
        st = check(svc)
        rows.append(st)
        if st["up"]:
            continue
        if not svc.core and not include_optional:
            print(f"  skip optional DOWN {svc.name} (use --all to revive)")
            continue
        if not svc.start_cmd:
            print(f"  {svc.name} DOWN but no start_cmd (managed by scheduled task) — skipping")
            continue
        print(f"  reviving {svc.name} via {svc.start_cmd[0]} ...")
        try:
            subprocess.Popen(
                svc.start_cmd,
                cwd=str(svc.start_cwd or ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"    failed: {exc}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="grok-auto-register stack supervisor")
    sub = ap.add_subparsers(dest="action")
    p_status = sub.add_parser("status", help="print service status table (default)")
    p_status.add_argument("--json", action="store_true", help="machine-readable output")
    p_ensure = sub.add_parser("ensure", help="revive DOWN services (never kills)")
    p_ensure.add_argument("--all", action="store_true", help="also revive optional services")
    args = ap.parse_args()

    if args.action == "ensure":
        return do_ensure(include_optional=getattr(args, "all", False))
    # default = status
    return do_status(as_json=getattr(args, "json", False))


if __name__ == "__main__":
    sys.exit(main())
