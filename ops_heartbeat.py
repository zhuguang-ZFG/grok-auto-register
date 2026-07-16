#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight ops heartbeat: processes + live pool watermark (no network probe).

Usage:
  python ops_heartbeat.py
  python ops_heartbeat.py --json
  python ops_heartbeat.py --write logs/heartbeat.json

Exit codes:
  0 ok
  1 warn (low pool / optional component missing)
  2 critical (register or CLIProxy not running)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _load_cfg() -> dict[str, Any]:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def list_matching(pattern: str) -> list[dict[str, Any]]:
    """Windows: match process CommandLine via PowerShell CIM (no psutil)."""
    # Escape single quotes for PowerShell single-quoted match fragment
    pat = pattern.replace("'", "''")
    ps = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -match '{pat}' }} | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True,
            errors="replace",
            timeout=30,
        )
    except Exception:
        return []
    if not (out or "").strip():
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    if isinstance(data, dict):
        return [data]
    return list(data or [])


def count_live_pool(auth_dir: Path, cfg: dict[str, Any] | None = None) -> tuple[int, int]:
    """Return (live_est, total_xai). live = not disabled. No JWT probe.

    Own-domain watermark only when *explicit* cfg enables it **and** lists
    defaultDomains/own_domains. Bare ``count_live_pool(dir)`` (tests / ad-hoc)
    never loads project config.toml side effects — empty cfg ⇒ count all
    non-disabled files (backward compatible).
    """
    if not auth_dir.is_dir():
        return 0, 0

    # None → empty (do NOT auto-load project config: breaks unit tests and
    # makes live_est depend on whoever's cwd config.json).
    # Callers that want watermark must pass cfg (build_heartbeat does).
    if cfg is None:
        cfg = {}

    is_own_path = None
    own_only = False
    try:
        from pool_policy import is_own_path as _iop
        from pool_policy import own_domains, watermark_own_only

        is_own_path = _iop
        # Only filter when watermark on AND own domain list is non-empty.
        own_only = bool(watermark_own_only(cfg)) and bool(own_domains(cfg))
    except Exception:
        is_own_path = None
        own_only = False

    total = 0
    live = 0
    for p in auth_dir.glob("xai-*.json"):
        total += 1
        if own_only and is_own_path is not None:
            try:
                if not is_own_path(p, cfg):
                    continue
            except Exception:
                pass
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("disabled"):
            continue
        live += 1
    return live, total


def min_live_from_cfg(cfg: dict[str, Any]) -> int:
    for key in ("pool_min_live", "quota_watch_min_pool"):
        try:
            v = int(cfg.get(key) or 0)
            if v > 0:
                return v
        except Exception:
            pass
    return 100


def build_heartbeat(
    *,
    root: Path | None = None,
    cfg: dict[str, Any] | None = None,
    proc_rows: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Pure-ish core: inject proc_rows in tests to avoid PowerShell."""
    root = root or ROOT
    cfg = cfg if cfg is not None else _load_cfg()
    if proc_rows is None:
        proc_rows = {
            "register": list_matching("grok_register_ttk\\.py"),
            "quota_watch": list_matching("quota_watch\\.py"),
            "cliproxy": list_matching("cli-proxy-api"),
        }

    def _alive(rows: list[dict[str, Any]]) -> bool:
        noise = ("powershell", "cmd.exe")
        for r in rows or []:
            name = str(r.get("Name") or r.get("name") or "").lower()
            if not name or any(n in name for n in noise):
                continue
            if name.endswith(".exe"):
                return True
        # fallback: any non-empty row that is not shell wrapper noise
        for r in rows or []:
            name = str(r.get("Name") or r.get("name") or "").lower()
            if name and not any(n in name for n in noise):
                return True
        return False

    def _port_up(port: int, headers: dict[str, str]) -> bool:
        """Best-effort local HTTP probe (no chat body — cheap liveness)."""
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{port}/v1/models"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return int(getattr(resp, "status", 0) or 0) == 200
        except Exception:
            return False

    procs = {
        "register": {
            "alive": _alive(proc_rows.get("register") or []),
            "count": len(proc_rows.get("register") or []),
        },
        "quota_watch": {
            "alive": _alive(proc_rows.get("quota_watch") or []),
            "count": len(proc_rows.get("quota_watch") or []),
        },
        "cliproxy": {
            "alive": _alive(proc_rows.get("cliproxy") or []),
            "count": len(proc_rows.get("cliproxy") or []),
        },
    }

    # Unified pools (keys from config.json pool_keys, gitignored)
    _keys = dict(cfg.get("pool_keys") or {}) if isinstance(cfg.get("pool_keys"), dict) else {}
    k_grok = _keys.get("grok") or "sk-local-grok-pool-2026"
    k_codex = _keys.get("codex") or "sk-local-codex-unified-2026"
    k_claude = _keys.get("claude") or "sk-local-claude-unified-2026"
    k_glm = _keys.get("glm") or "sk-local-glm-unified-2026"
    ports = {
        "grok_8317": _port_up(8317, {"Authorization": f"Bearer {k_grok}"}),
        "codex_8327": _port_up(8327, {"Authorization": f"Bearer {k_codex}"}),
        "claude_8337": _port_up(
            8337,
            {
                "Authorization": f"Bearer {k_claude}",
                "x-api-key": k_claude,
            },
        ),
        "glm_8347": _port_up(8347, {"Authorization": f"Bearer {k_glm}"}),
    }

    cpa_raw = str(cfg.get("cpa_auth_dir") or "cpa_auths")
    cpa_dir = Path(cpa_raw)
    if not cpa_dir.is_absolute():
        cpa_dir = root / cpa_dir
    live, total = count_live_pool(cpa_dir, cfg)
    min_live = min_live_from_cfg(cfg)

    alerts: list[str] = []
    level = "ok"
    if not procs["register"]["alive"]:
        alerts.append("register process not running")
        level = "critical"
    if not procs["cliproxy"]["alive"]:
        alerts.append("cli-proxy-api not running")
        level = "critical"
    if not procs["quota_watch"]["alive"]:
        alerts.append("quota_watch not running")
        if level != "critical":
            level = "warn"
    if live < min_live:
        alerts.append(f"pool_live_est={live} < min_live={min_live}")
        if level == "ok":
            level = "warn"
    for port_name, up in ports.items():
        if not up:
            alerts.append(f"{port_name} /v1/models not 200")
            if level == "ok":
                level = "warn"

    # Recent disable_bad_upstreams report (hard + temp-out charity sources)
    disable_rep: dict[str, Any] = {}
    rep_path = root / "logs" / "disable_bad_upstreams.json"
    if rep_path.is_file():
        try:
            disable_rep = json.loads(rep_path.read_text(encoding="utf-8"))
        except Exception:
            disable_rep = {}
    applied = list(disable_rep.get("applied") or [])
    revived = list(disable_rep.get("revived") or [])
    temp_ledger = disable_rep.get("temp_disable_ledger") or {}
    if applied:
        alerts.append(
            "upstream_applied="
            + ",".join(f"{a.get('pool')}/{a.get('name')}:{a.get('kind')}" for a in applied[:8])
        )
        if level == "ok":
            level = "warn"
    if isinstance(temp_ledger, dict) and temp_ledger:
        alerts.append(f"temp_disabled_n={len(temp_ledger)}")

    return {
        "ok": level == "ok",
        "level": level,
        "procs": procs,
        "ports": ports,
        "pool_live_est": live,
        "pool_total": total,
        "min_live": min_live,
        "alerts": alerts,
        "upstream_applied": applied,
        "upstream_revived": revived,
        "temp_disabled": list(temp_ledger.keys()) if isinstance(temp_ledger, dict) else [],
        "disable_report_ts": disable_rep.get("ts_iso"),
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def exit_code_for(level: str) -> int:
    if level == "critical":
        return 2
    if level == "warn":
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ops heartbeat for grok pool")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument(
        "--write",
        default="",
        help="also write JSON to path (default logs/heartbeat.json if 'default')",
    )
    args = parser.parse_args(argv)
    hb = build_heartbeat()
    text = json.dumps(hb, ensure_ascii=False, indent=2)
    write_path = (args.write or "").strip()
    if write_path.lower() == "default":
        write_path = str(ROOT / "logs" / "heartbeat.json")
    if write_path:
        path = Path(write_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    if args.json or write_path:
        print(text)
    else:
        ports = hb.get("ports") or {}
        print(
            f"[heartbeat] level={hb['level']} live={hb['pool_live_est']}/{hb['min_live']} "
            f"reg={hb['procs']['register']['alive']} qw={hb['procs']['quota_watch']['alive']} "
            f"proxy={hb['procs']['cliproxy']['alive']} "
            f"8317={ports.get('grok_8317')} 8327={ports.get('codex_8327')} "
            f"8337={ports.get('claude_8337')} 8347={ports.get('glm_8347')}"
        )
        for a in hb.get("alerts") or []:
            print(f"  ! {a}")
    return exit_code_for(str(hb.get("level") or "ok"))


if __name__ == "__main__":
    raise SystemExit(main())
