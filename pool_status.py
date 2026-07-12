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


def _mint_log_stats(log_path: Path, *, max_bytes: int = 400_000) -> dict[str, Any]:
    """Count protocol/browser mint signals since last RESTART marker."""
    out: dict[str, Any] = {
        "log": str(log_path),
        "ok": False,
        "since_marker": None,
        "mint_start": 0,
        "protocol_ok": 0,
        "protocol_fail": 0,
        "authcode_ok": 0,
        "authcode_fail": 0,
        "egress_rotated": 0,
        "browser_allow": 0,
        "export_ok_protocol": 0,
        "export_ok_browser": 0,
        "export_ok_authcode": 0,
    }
    if not log_path.is_file():
        out["error"] = "missing"
        return out
    try:
        raw = log_path.read_bytes()
        if len(raw) > max_bytes:
            raw = raw[-max_bytes:]
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        out["error"] = str(e)
        return out
    marker = "===== RESTART"
    idx = text.rfind(marker)
    if idx >= 0:
        # capture marker line for human display
        line_end = text.find("\n", idx)
        out["since_marker"] = text[idx : line_end if line_end > idx else idx + 80].strip()[:120]
        chunk = text[idx:]
    else:
        chunk = text
        out["since_marker"] = "(no RESTART marker; whole tail)"
    low = chunk.lower()
    out["mint_start"] = low.count("mint start:")
    out["protocol_ok"] = low.count("protocol mint ok")
    out["protocol_fail"] = low.count("protocol mint failed")
    out["authcode_ok"] = low.count("authcode mint ok")
    out["authcode_fail"] = low.count("authcode mint failed")
    out["egress_rotated"] = low.count("mint egress rotated")
    out["browser_allow"] = chunk.count("clicked REAL exact")
    out["export_ok_protocol"] = low.count("export ok method=protocol") + low.count(
        "method=protocol path="
    )
    out["export_ok_browser"] = low.count("export ok method=browser") + low.count(
        "method=browser path="
    )
    out["export_ok_authcode"] = low.count("export ok method=authcode") + low.count(
        "method=authcode path="
    )
    out["ok"] = True
    return out


def _power_ac_sleep_status() -> dict[str, Any]:
    """Read-only powercfg: AC standby idle + lid action (Windows).

    Uses active scheme GUID + setting GUIDs (locale-safe). SCHEME_CURRENT +
    SUB_SLEEP aliases are rejected on some Windows builds when mixed.
    """
    import re

    out: dict[str, Any] = {"ok": False, "ac_sleep_never": None, "ac_lid_do_nothing": None}
    exe = r"C:\Windows\System32\powercfg.exe"
    sub_sleep = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
    standby = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"
    sub_btn = "4f971e89-eebd-4455-a8de-9e59040e7347"
    lid_set = "5ca83367-6e45-459f-a27b-476b1d01c936"

    def _run(args: list[str]) -> str:
        r = subprocess.run(
            [exe, *args], capture_output=True, timeout=15, check=False
        )
        raw = r.stdout or b""
        for enc in ("mbcs", "utf-8", "utf-16le"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", errors="replace")

    try:
        active_blob = _run(["/GETACTIVESCHEME"])
        m = re.search(
            r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
            active_blob,
        )
        if not m:
            out["error"] = "active scheme GUID not found"
            return out
        scheme = m.group(1)
        out["scheme"] = scheme
        sleep = _run(["/q", scheme, sub_sleep, standby])
        lid = _run(["/q", scheme, sub_btn, lid_set])
    except Exception as e:
        out["error"] = str(e)
        return out

    def _ac_index(blob: str) -> int | None:
        # Prefer AC/current-AC line; Chinese: 当前交流电源的电源设置索引
        for line in blob.splitlines():
            s = line.strip()
            if "0x" not in s.lower():
                continue
            is_ac = (
                "交流" in s
                or "AC" in s.upper()
                or "Current AC" in s
                or ("当前" in s and "直流" not in s and "DC" not in s.upper())
            )
            if not is_ac and "当前交流" not in s:
                continue
            try:
                hx = re.search(r"0x([0-9a-fA-F]+)", s, re.I)
                if hx:
                    return int(hx.group(1), 16)
            except Exception:
                continue
        # Ordered: first "当前交流", else first Current AC Power, else first 0x after STANDBY
        for pat in (
            r"当前交流[^\n]*0x([0-9a-fA-F]+)",
            r"Current AC Power[^\n]*0x([0-9a-fA-F]+)",
            r"AC Power Setting Index:\s*0x([0-9a-fA-F]+)",
        ):
            m2 = re.search(pat, blob, re.I)
            if m2:
                try:
                    return int(m2.group(1), 16)
                except Exception:
                    pass
        # last resort: first power setting index line (often AC then DC)
        ms = re.findall(
            r"(?:电源设置索引|Power Setting Index)[^\n]*0x([0-9a-fA-F]+)",
            blob,
            re.I,
        )
        if ms:
            try:
                return int(ms[0], 16)
            except Exception:
                return None
        return None

    ac_sleep = _ac_index(sleep)
    ac_lid = _ac_index(lid)
    out["ac_standby_sec"] = ac_sleep
    out["ac_lid_action"] = ac_lid
    out["ac_sleep_never"] = ac_sleep == 0 if ac_sleep is not None else None
    out["ac_lid_do_nothing"] = ac_lid == 0 if ac_lid is not None else None
    out["ok"] = True
    out["warn"] = not (
        out["ac_sleep_never"] is True and out["ac_lid_do_nothing"] is True
    )
    return out


def _load_heartbeat_file() -> dict[str, Any]:
    p = ROOT / "logs" / "heartbeat.json"
    if not p.is_file():
        return {"ok": False, "error": "missing"}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _cliproxy_affinity_stats(
    log_path: Path, *, max_bytes: int = 300_000
) -> dict[str, Any]:
    """Tail CLIProxy main.log for sticky session reselect / REMOVE churn."""
    out: dict[str, Any] = {
        "log": str(log_path),
        "ok": False,
        "affinity_hit": 0,
        "affinity_miss": 0,
        "affinity_reselect": 0,
        "auth_remove": 0,
        "auth_create": 0,
        "auth_write": 0,
    }
    if not log_path.is_file():
        out["error"] = "missing"
        return out
    try:
        raw = log_path.read_bytes()
        if len(raw) > max_bytes:
            raw = raw[-max_bytes:]
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        out["error"] = str(e)
        return out
    out["affinity_hit"] = text.count("session-affinity: cache hit |")
    out["affinity_miss"] = text.count("session-affinity: cache miss")
    out["affinity_reselect"] = text.count("auth unavailable, reselected")
    out["auth_remove"] = text.count("auth file changed (REMOVE)")
    out["auth_create"] = text.count("auth file changed (CREATE)")
    out["auth_write"] = text.count("auth file changed (WRITE)")
    denom = out["affinity_hit"] + out["affinity_miss"] + out["affinity_reselect"]
    out["reselect_rate"] = round(out["affinity_reselect"] / denom, 4) if denom else None
    out["ok"] = True
    return out


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
            "own_register_target": cfg.get("own_register_target")
            or cfg.get("quota_watch_target_pool"),
            "cpa_mint_workers": cfg.get("cpa_mint_workers"),
            "cpa_prefer_protocol": cfg.get("cpa_prefer_protocol"),
            "cpa_mint_rotate_egress": cfg.get("cpa_mint_rotate_egress"),
            "cpa_mint_rotate_on_tls": cfg.get("cpa_mint_rotate_on_tls"),
            "cpa_protocol_attempts": cfg.get("cpa_protocol_attempts"),
            "anti_detect_viewport": cfg.get("anti_detect_viewport"),
            "anti_detect_tz_locale": cfg.get("anti_detect_tz_locale"),
            "anti_detect_ua_pool": cfg.get("anti_detect_ua_pool"),
            "pool_prefer_mode": cfg.get("pool_prefer_mode"),
        },
    }

    # recent mint path stats from register log (since last RESTART marker)
    snap["mint_log"] = _mint_log_stats(ROOT / "logs" / "register_auto.out.log")

    # CLIProxy sticky / auth-file churn (community: reselect kills cache)
    snap["cliproxy_affinity"] = _cliproxy_affinity_stats(
        Path(r"D:/cli-proxy-api/logs/main.log")
    )

    # last proxy health snapshot (if any)
    ph = ROOT / ".proxy_health.json"
    if ph.is_file():
        try:
            snap["proxy_health"] = json.loads(ph.read_text(encoding="utf-8"))
        except Exception as e:
            snap["proxy_health"] = {"error": str(e)}
    else:
        snap["proxy_health"] = {"ok": None, "error": "no snapshot yet"}

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

    # power (read-only) + last heartbeat file
    try:
        snap["power_ac"] = _power_ac_sleep_status()
    except Exception as exc:
        snap["power_ac"] = {"ok": False, "error": str(exc)}
    snap["heartbeat"] = _load_heartbeat_file()

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
        target = int((snap.get("config") or {}).get("own_register_target") or 0)
        if not target:
            try:
                target = int(
                    json.loads((ROOT / "config.json").read_text(encoding="utf-8")).get(
                        "own_register_target"
                    )
                    or snap.get("config", {}).get("quota_watch_target_pool")
                    or 0
                )
            except Exception:
                target = int((snap.get("config") or {}).get("quota_watch_target_pool") or 0)
        if target:
            own_n = int(cpa.get("own_files") or 0)
            gap = max(0, target - own_n)
            pct = min(100.0, 100.0 * own_n / target) if target else 0
            print(f"[*] 自有域进度: {own_n}/{target} ({pct:.1f}%) 还差 {gap}")
    if cpa.get("domains"):
        top = ", ".join(f"{d}={n}" for d, n in list(cpa["domains"].items())[:8])
        print(f"[*] CPA 域名分布: {top}")

    print(snap.get("domain_health_line") or "[*] 域名健康: n/a")

    ml = snap.get("mint_log") or {}
    if ml.get("ok"):
        print(
            f"[*] 铸造日志(自 {ml.get('since_marker') or '?'}): "
            f"start={ml.get('mint_start')} protocol_ok={ml.get('protocol_ok')} "
            f"protocol_fail={ml.get('protocol_fail')} "
            f"authcode_ok={ml.get('authcode_ok')} authcode_fail={ml.get('authcode_fail')} "
            f"egress_rot={ml.get('egress_rotated')} browser_allow={ml.get('browser_allow')}"
        )
    elif ml.get("error"):
        print(f"[*] 铸造日志: n/a ({ml.get('error')})")

    ph = snap.get("proxy_health") or {}
    if ph.get("error") and ph.get("ok") is None:
        print(f"[*] 代理健康: n/a ({ph.get('error')})")
    else:
        print(
            f"[*] 代理健康: ok={ph.get('ok')} clash={ph.get('clash_ok')} "
            f"xai={ph.get('xai_ok')} ip={ph.get('exit_ip')} "
            f"node={str(ph.get('node') or '')[:40]!r} ts={ph.get('ts')}"
        )

    route = snap.get("cliproxy_routing") or {}
    if route.get("error"):
        print(f"[*] CLIProxy 路由: (unavailable: {route['error']})")
    else:
        print(
            f"[*] CLIProxy 路由: profile={route.get('profile')} "
            f"strategy={route.get('strategy')} affinity={route.get('session_affinity')}"
        )

    aff = snap.get("cliproxy_affinity") or {}
    if aff.get("ok"):
        rr = aff.get("reselect_rate")
        rr_s = f"{rr:.1%}" if isinstance(rr, float) else "n/a"
        print(
            f"[*] CLIProxy sticky(tail): hit={aff.get('affinity_hit')} "
            f"miss={aff.get('affinity_miss')} reselect={aff.get('affinity_reselect')} "
            f"rate={rr_s} REMOVE={aff.get('auth_remove')} WRITE={aff.get('auth_write')}"
        )
        if isinstance(rr, float) and rr > 0.15:
            print(
                "[!] sticky reselect_rate>15%: 查 REMOVE/disabled 导致的 affinity rebind"
                "（soft-disable 勿硬删 live 池）"
            )
    elif aff.get("error"):
        print(f"[*] CLIProxy sticky: n/a ({aff.get('error')})")

    pwr = snap.get("power_ac") or {}
    if pwr.get("ok"):
        sleep_s = "never" if pwr.get("ac_sleep_never") else f"sec={pwr.get('ac_standby_sec')}"
        lid_s = "do-nothing" if pwr.get("ac_lid_do_nothing") else f"code={pwr.get('ac_lid_action')}"
        flag = "WARN" if pwr.get("warn") else "ok"
        print(f"[*] 电源AC: sleep={sleep_s} lid={lid_s} ({flag})")
        if pwr.get("warn"):
            print("[!] 插电仍可能睡眠/合盖休眠 → scripts/ensure_power_awake.ps1")
    elif pwr.get("error"):
        print(f"[*] 电源AC: n/a ({pwr.get('error')})")

    hb = snap.get("heartbeat") or {}
    if hb.get("level") or hb.get("ok") is True:
        print(
            f"[*] heartbeat: level={hb.get('level')} live={hb.get('pool_live_est')}/"
            f"{hb.get('min_live')} ts={hb.get('ts_iso') or '?'}"
        )
        for a in (hb.get("alerts") or [])[:3]:
            print(f"    ! {a}")
    elif hb.get("error") and hb.get("error") != "missing":
        print(f"[*] heartbeat: n/a ({hb.get('error')})")

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

    print(
        "[*] 维持建议: 号池充足时 1 并发补号（见 docs/HARDEN.md）；"
        "水位见 quota_watch_min/target_pool"
    )
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
