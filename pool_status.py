#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""号池本地概况：accounts / cpa_auths / 域名健康 / 路由 / 进程（可选）。

用法:
  python pool_status.py
  python pool_status.py --json
  python pool_status.py --json --procs
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _load_cfg() -> dict[str, Any]:
    cfg_path = ROOT / "config.json"
    if not cfg_path.is_file():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _list_procs(pattern: str) -> list[dict[str, Any]]:
    """Match process command lines on Windows via CIM (python only)."""
    ps = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.Name -eq 'python.exe' -or $_.Name -like 'cli-proxy*' }} | "
        "Where-Object { $_.CommandLine -like '*" + pattern + "*' } | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True,
            errors="replace",
            timeout=15,
        )
    except Exception:
        return []
    if not out.strip():
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    rows = []
    for r in data or []:
        rows.append(
            {
                "pid": r.get("ProcessId"),
                "name": r.get("Name"),
                "cmd": (r.get("CommandLine") or "")[:200],
            }
        )
    return rows


def collect_snapshot(*, include_procs: bool = False) -> dict[str, Any]:
    """Build a machine-readable status snapshot."""
    cfg = _load_cfg()
    snap: dict[str, Any] = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(ROOT),
        "config": {
            "register_count": cfg.get("register_count"),
            "concurrent_count": cfg.get("concurrent_count") or cfg.get("concurrency"),
            "defaultDomains": cfg.get("defaultDomains"),
            "quota_watch_min_pool": cfg.get("quota_watch_min_pool"),
            "quota_watch_target_pool": cfg.get("quota_watch_target_pool"),
            "cpa_mint_workers": cfg.get("cpa_mint_workers"),
            "anti_detect_viewport": cfg.get("anti_detect_viewport"),
            "anti_detect_tz_locale": cfg.get("anti_detect_tz_locale"),
            "anti_detect_ua_pool": cfg.get("anti_detect_ua_pool"),
        },
    }

    # accounts
    acc_files = sorted(ROOT.glob("accounts_*.txt"), key=lambda p: p.stat().st_mtime)
    total_lines = 0
    domain_counter: Counter[str] = Counter()
    for f in acc_files:
        try:
            lines = [
                ln.strip()
                for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines()
                if ln.strip() and "----" in ln
            ]
        except Exception:
            continue
        total_lines += len(lines)
        for ln in lines:
            email = ln.split("----", 1)[0]
            if "@" in email:
                domain_counter[email.split("@", 1)[1].lower()] += 1
    snap["accounts"] = {
        "files": len(acc_files),
        "lines": total_lines,
        "domains": dict(domain_counter.most_common()),
        "latest": acc_files[-1].name if acc_files else None,
    }

    # cpa
    cpa_dir = ROOT / str(cfg.get("cpa_auth_dir") or "./cpa_auths")
    if not cpa_dir.is_absolute():
        cpa_dir = (ROOT / cpa_dir).resolve()
    auths = sorted(cpa_dir.glob("xai-*.json")) if cpa_dir.is_dir() else []
    alive = expired = unknown = disabled = 0
    now = datetime.now(timezone.utc)
    domain_auth: Counter[str] = Counter()
    for p in auths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("disabled"):
                disabled += 1
            email = str(data.get("email") or "")
            if "@" in email:
                domain_auth[email.split("@", 1)[1].lower()] += 1
            elif "@" in p.stem:
                domain_auth[p.stem.split("@", 1)[1].lower()] += 1
            exp = str(data.get("expired") or "").strip()
            if exp:
                try:
                    if exp.endswith("Z"):
                        exp = exp[:-1] + "+00:00"
                    dt = datetime.fromisoformat(exp)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt > now:
                        alive += 1
                    else:
                        expired += 1
                    continue
                except Exception:
                    pass
            unknown += 1
        except Exception:
            unknown += 1
    own_n = buf_n = 0
    try:
        from pool_policy import is_own_path, summarize_pool_files

        part = summarize_pool_files(auths, cfg)
        own_n, buf_n = part.get("own", 0), part.get("buffer", 0)
        prefer = part.get("prefer_mode") or cfg.get("pool_prefer_mode") or "own_first"
    except Exception:
        prefer = cfg.get("pool_prefer_mode") or "own_first"
    snap["cpa"] = {
        "dir": str(cpa_dir),
        "files": len(auths),
        "access_alive": alive,
        "access_expired": expired,
        "unknown": unknown,
        "disabled": disabled,
        "own_files": own_n,
        "buffer_files": buf_n,
        "prefer_mode": prefer,
        "domains": dict(domain_auth.most_common()),
    }

    # domain health
    try:
        import domain_health as _dh

        snap["domain_health"] = _dh.snapshot(cfg)
        snap["domain_health_line"] = _dh.format_summary_line(cfg)
    except Exception as exc:
        snap["domain_health"] = {"error": str(exc)}
        snap["domain_health_line"] = f"[*] 域名健康: (unavailable: {exc})"

    # routing
    try:
        from set_cliproxy_routing import DEFAULT_CONFIG, detect_profile, parse_routing

        cpath = Path(DEFAULT_CONFIG)
        if cpath.is_file():
            parsed = parse_routing(cpath.read_text(encoding="utf-8"))
            snap["cliproxy_routing"] = {
                "config": str(cpath),
                "profile": detect_profile(parsed),
                "strategy": parsed.get("strategy"),
                "session_affinity": parsed.get("session_affinity"),
            }
        else:
            snap["cliproxy_routing"] = {"error": f"missing {cpath}"}
    except Exception as exc:
        snap["cliproxy_routing"] = {"error": str(exc)}

    # local grok auth
    try:
        from local_grok_auth import default_auth_path, load_auth_file

        ap = default_auth_path()
        entry_email = None
        expires = None
        if ap.is_file():
            data = load_auth_file(ap)
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, dict) and (
                        v.get("email") or v.get("access_token") or v.get("key")
                    ):
                        entry_email = v.get("email")
                        expires = v.get("expires") or v.get("expired")
                        break
        snap["local_grok_auth"] = {
            "path": str(ap),
            "email": entry_email,
            "expires": expires,
            "exists": ap.is_file(),
        }
    except Exception as exc:
        snap["local_grok_auth"] = {"error": str(exc)}

    # quota watch state
    st_path = ROOT / ".quota_watch_state.json"
    if st_path.is_file():
        try:
            st = json.loads(st_path.read_text(encoding="utf-8"))
            snap["quota_watch_state"] = {
                "last_action": st.get("last_action"),
                "last_email": st.get("last_email"),
                "last_trigger_reason": (st.get("last_trigger_reason") or "")[:160],
                "last_sample_live_ratio": st.get("last_sample_live_ratio"),
                "triggers_today": st.get("triggers_today"),
                "updated_at": st.get("updated_at"),
            }
        except Exception as exc:
            snap["quota_watch_state"] = {"error": str(exc)}
    else:
        snap["quota_watch_state"] = None

    if include_procs:
        snap["processes"] = {
            "quota_watch": _list_procs("quota_watch.py"),
            "register": _list_procs("grok_register_ttk.py"),
            "cliproxy": _list_procs("cli-proxy-api"),
        }

    return snap


def print_human(snap: dict[str, Any]) -> None:
    cfg = snap.get("config") or {}
    print(f"[*] 时间: {snap.get('ts')}")
    print(f"[*] 目录: {snap.get('root')}")
    print(
        f"[*] 配置: count={cfg.get('register_count')} concurrency={cfg.get('concurrent_count')} "
        f"domains={cfg.get('defaultDomains')!r}"
    )
    acc = snap.get("accounts") or {}
    print(f"[*] accounts 文件: {acc.get('files')} 个 | 账号行合计: {acc.get('lines')}")
    if acc.get("domains"):
        top = ", ".join(f"{d}={n}" for d, n in list(acc["domains"].items())[:8])
        print(f"[*] 账号域名分布: {top}")
    if acc.get("latest"):
        print(f"[*] 最新 accounts: {acc.get('latest')}")

    cpa = snap.get("cpa") or {}
    print(
        f"[*] CPA auth: {cpa.get('files')} 个 | access 未过期(粗判)≈{cpa.get('access_alive')} "
        f"| 已过期≈{cpa.get('access_expired')} | disabled≈{cpa.get('disabled')} | 未知={cpa.get('unknown')}"
    )
    if cpa.get("own_files") is not None:
        mode = (cpa.get("prefer_mode") or "own_first")
        hint = "先烧缓冲" if mode == "buffer_first" else "本地换号优先自有"
        print(
            f"[*] 池分层: 自有域≈{cpa.get('own_files')} | 缓冲域≈{cpa.get('buffer_files')} "
            f"| prefer={mode} ({hint})"
        )
    if cpa.get("domains"):
        top = ", ".join(f"{d}={n}" for d, n in list(cpa["domains"].items())[:8])
        print(f"[*] CPA 域名分布: {top}")

    print(snap.get("domain_health_line") or "[*] 域名健康: n/a")

    route = snap.get("cliproxy_routing") or {}
    if route.get("error"):
        print(f"[*] CLIProxy 路由: (unavailable: {route['error']})")
    else:
        print(
            f"[*] CLIProxy 路由: profile={route.get('profile')} "
            f"strategy={route.get('strategy')} affinity={route.get('session_affinity')}"
        )

    auth = snap.get("local_grok_auth") or {}
    if auth.get("error"):
        print(f"[*] 本机 Grok auth: (unavailable: {auth['error']})")
    else:
        print(
            f"[*] 本机 Grok auth: email={auth.get('email')} expires={auth.get('expires')}"
        )

    qw = snap.get("quota_watch_state")
    if isinstance(qw, dict) and not qw.get("error"):
        print(
            f"[*] quota_watch: action={qw.get('last_action')} email={qw.get('last_email')} "
            f"sample_ratio={qw.get('last_sample_live_ratio')} triggers_today={qw.get('triggers_today')}"
        )

    procs = snap.get("processes")
    if procs:
        for name, rows in procs.items():
            pids = [str(r.get("pid")) for r in rows if r.get("name") == "python.exe" or "cli-proxy" in str(r.get("name") or "").lower() or name == "cliproxy"]
            # simplify: show first real pid
            real = []
            for r in rows:
                n = str(r.get("name") or "")
                if n.lower() in ("python.exe", "cli-proxy-api.exe") or n.endswith(".exe"):
                    if "powershell" in n.lower() or "bash" in n.lower():
                        continue
                    real.append(f"{n}:{r.get('pid')}")
            print(f"[*] 进程 {name}: {', '.join(real) if real else '未运行'}")

    print("[*] 维持建议: 单批 2 并发补号；水位见 quota_watch_min/target_pool")
    print("[*] 路由切换: python set_cliproxy_routing.py status|pool|cache")
    print("[*] JSON: python pool_status.py --json [--procs]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="号池 / 路由 / 进程概况")
    parser.add_argument("--json", action="store_true", help="输出 JSON 快照")
    parser.add_argument(
        "--procs",
        action="store_true",
        help="JSON 模式下包含进程；人机模式默认已包含",
    )
    args = parser.parse_args(argv)
    if args.json:
        snap = collect_snapshot(include_procs=bool(args.procs))
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0
    snap = collect_snapshot(include_procs=True)
    print_human(snap)
    return 0


if __name__ == "__main__":
    try:
        import stdio_utf8  # noqa: F401
    except Exception:
        pass
    raise SystemExit(main())
