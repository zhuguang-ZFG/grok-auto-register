#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe CLIProxy remote openai-compatibility / claude-api-key channels; disable chat failures.

Community rule: models-only OK is not enough — chat must work or hop noise grows.
Edits only D:/cli-proxy-api/config*.yaml (never commits secrets).

Usage:
  python scripts/disable_bad_upstreams.py              # dry-run report (no streak write)
  python scripts/disable_bad_upstreams.py --apply       # write disabled: true for hard fails
  python scripts/disable_bad_upstreams.py --apply --restart-fleet
  python scripts/disable_bad_upstreams.py --auto        # hard day-1 + soft temp-out + recover
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CLIPROXY = Path(r"D:/cli-proxy-api")
CONFIGS = {
    "grok": CLIPROXY / "config.yaml",
    "codex": CLIPROXY / "config-codex.yaml",
    "claude": CLIPROXY / "config-claude.yaml",
    "glm": CLIPROXY / "config-glm.yaml",
}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _pool_keys() -> dict:
    """Local pool API keys from config.json (gitignored), with built-in fallbacks."""
    defaults = {
        "grok": "sk-local-grok-pool-2026",
        "codex": "sk-local-codex-unified-2026",
        "claude": "sk-local-claude-unified-2026",
        "glm": "sk-local-glm-unified-2026",
    }
    try:
        cfg = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))
        stored = cfg.get("pool_keys") or {}
        if isinstance(stored, dict):
            for k, v in stored.items():
                if isinstance(v, str) and v:
                    defaults[k] = v
    except Exception:
        pass
    return defaults
REPORT = Path(r"D:/Users/grok-auto-register/logs/disable_bad_upstreams.json")
STREAK_FILE = Path(r"D:/Users/grok-auto-register/logs/upstream_bad_streak.json")
SOFT_STREAK_FILE = Path(r"D:/Users/grok-auto-register/logs/upstream_soft_streak.json")
MAIN_SLOW_STREAK_FILE = Path(r"D:/Users/grok-auto-register/logs/upstream_main_slow_streak.json")
TEMP_DISABLE_FILE = Path(r"D:/Users/grok-auto-register/logs/upstream_temp_disable.json")
# Community charity keys: quota often returns after daily check-in (6–24h).
DEFAULT_SOFT_RECOVER_HOURS = 6
SLOW_CHAT_SECONDS = 15.0
# Main-alias demotion (still HTTP 200 but too slow for RR/fill-first sticky pain).
# Evidence: ioll/fengwind/hlwy/cunai — 15s+ sticky; demote earlier at 8s p50-class.
MAIN_DEMOTE_MS = 8000
SOFT_STREAK_TO_TEMP = 2  # consecutive soft fails → temp out (sign-in may refill later)
MAIN_SLOW_STREAK_TO_DEMOTE = 2


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def is_quota_like(code: int, body: str) -> bool:
    """Charity stations: exhausted / rate / weekly limit → temp out, recheck later."""
    b = (body or "").lower()
    markers = (
        "quota",
        "rate_limit",
        "rate limit",
        "upstream_quota",
        "exceed",
        "exhausted",
        "余额",
        "额度",
        "用完",
        "上限",
        "并发",
        "too many",
        "insufficient",
        "weekly",
        "已达",
        "使用量",
        "耗尽",
        "concurrency",
    )
    # Do NOT match bare "no available" — Claude cloak "No available accounts: only
    # Claude Code clients" is not a charity-quota signal.
    if "claude code" in b or "only allows" in b:
        return False
    if any(m in b for m in markers):
        return True
    if code == 429:
        return True
    if code == 500 and any(m in b for m in ("quota", "rate", "limit", "额度", "耗尽")):
        return True
    return False


@dataclass
class Channel:
    pool: str
    kind: str  # openai-compatibility | claude-api-key
    name: str
    base_url: str
    api_key: str
    disabled: bool
    start_line: int  # 0-based line index of "- name:" or first api-key line block
    # for claude-api-key entries name may be synthetic
    headers: dict = field(default_factory=dict)  # per-channel headers from config
    probe_model: str = ""  # first upstream model name from config (avoids probing models the channel doesn't serve)
    # client aliases from models: block (for main-path demotion)
    aliases: list[str] = field(default_factory=list)

    @property
    def on_main_path(self) -> bool:
        """True if any alias is a primary client name (not remote-* debug)."""
        return any(a and not str(a).startswith("remote-") for a in self.aliases)


def http(
    url: str,
    key: str,
    body: dict | None = None,
    *,
    anthropic: bool = False,
    timeout: float = 35.0,
    extra_headers: dict | None = None,
) -> tuple[int, str, float]:
    """Return (status, body_prefix, elapsed_seconds). elapsed is wall time even on transport fail."""
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": UA,
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)  # channel config headers win (e.g. codex_cli_rs UA)
    if anthropic:
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    data = None
    method = "GET"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body_s = resp.read().decode("utf-8", "replace")[:300]
            return resp.status, body_s, time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        try:
            body_s = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body_s = str(e)
        return e.code, body_s, time.perf_counter() - t0
    except Exception as e:
        return 0, str(e), time.perf_counter() - t0


def parse_openai_compat(path: Path, pool: str) -> list[Channel]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    channels: list[Channel] = []
    i = 0
    in_section = False
    while i < len(lines):
        line = lines[i]
        if re.match(r"^openai-compatibility:\s*$", line):
            in_section = True
            i += 1
            continue
        if in_section and re.match(r"^[a-zA-Z0-9_-]+:", line) and not line.startswith(" "):
            in_section = False
        if not in_section:
            i += 1
            continue
        m = re.match(r"^  - name:\s*[\"']?([^\"'#]+?)[\"']?\s*$", line)
        if not m:
            i += 1
            continue
        name = m.group(1).strip()
        start = i
        base = ""
        key = ""
        disabled = False
        chan_headers: dict = {}
        probe_model = ""
        aliases: list[str] = []
        in_models = False
        i += 1
        while i < len(lines):
            l2 = lines[i]
            if re.match(r"^  - name:\s*", l2) or (
                re.match(r"^[a-zA-Z0-9_-]+:", l2) and not l2.startswith(" ")
            ):
                break
            if re.match(r"^    disabled:\s*true", l2, re.I):
                disabled = True
            bm = re.match(r"^    base-url:\s*[\"']?([^\"'#]+?)[\"']?\s*$", l2)
            if bm:
                base = bm.group(1).strip()
            km = re.match(r"^      - api-key:\s*[\"']?([^\"'#]+?)[\"']?\s*$", l2)
            if km:
                key = km.group(1).strip()
            hm = re.match(r"^      ([A-Za-z0-9-]+):\s*[\"']?(.*?)[\"']?\s*$", l2)
            if hm and hm.group(1) not in ("api-key", "proxy-url"):
                # only under headers: block — crude; skip model alias lines handled below
                if not in_models:
                    chan_headers[hm.group(1)] = hm.group(2)
            mm = re.match(r'^    models:\s*$', l2)
            if mm:
                in_models = True
            if in_models:
                mnm = re.match(r"^      - name:\s*[\"']?([^\"'#]+?)[\"']?\s*$", l2)
                if mnm and not probe_model:
                    probe_model = mnm.group(1).strip()
                am = re.match(r"^        alias:\s*[\"']?([^\"'#]+?)[\"']?\s*$", l2)
                if am:
                    aliases.append(am.group(1).strip())
            i += 1
        if name and base and key:
            channels.append(
                Channel(
                    pool,
                    "openai-compatibility",
                    name,
                    base.rstrip("/"),
                    key,
                    disabled,
                    start,
                    chan_headers,
                    probe_model,
                    aliases,
                )
            )
        # do not i+=1 again; while advanced
    return channels


def parse_claude_keys(path: Path, pool: str) -> list[Channel]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    channels: list[Channel] = []
    i = 0
    in_section = False
    idx = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^claude-api-key:\s*$", line):
            in_section = True
            i += 1
            continue
        if in_section and re.match(r"^[a-zA-Z0-9_-]+:", line) and not line.startswith(" "):
            in_section = False
        if not in_section:
            i += 1
            continue
        m = re.match(r"^  - api-key:\s*[\"']?([^\"'#]+?)[\"']?\s*$", line)
        if not m:
            i += 1
            continue
        key = m.group(1).strip()
        start = i
        base = ""
        disabled = False
        i += 1
        while i < len(lines):
            l2 = lines[i]
            if re.match(r"^  - api-key:\s*", l2) or (
                re.match(r"^[a-zA-Z0-9_-]+:", l2) and not l2.startswith(" ")
            ):
                break
            if re.match(r"^    disabled:\s*true", l2, re.I):
                disabled = True
            bm = re.match(r"^    base-url:\s*[\"']?([^\"'#]+?)[\"']?\s*$", l2)
            if bm:
                base = bm.group(1).strip()
            i += 1
        idx += 1
        name = f"claude-key-{idx}"
        if "100xlabs" in base:
            name = f"100xlabs-{idx}"
        elif "shengqain" in base:
            name = "linxi"
        elif "fcapp" in base:
            name = "anyrouter"
        elif "bigmodel" in base:
            name = "glm"
        if base and key:
            channels.append(
                Channel(pool, "claude-api-key", name, base.rstrip("/"), key, disabled, start)
            )
    return channels


def probe_channel(ch: Channel) -> dict[str, Any]:
    if ch.disabled:
        return {
            "pool": ch.pool,
            "name": ch.name,
            "base_url": ch.base_url,
            "skipped": True,
            "reason": "already_disabled",
            "ok": None,
        }
    # skip pure local loopback for disable logic (local-k12 is intentional first hop)
    if "127.0.0.1" in ch.base_url or "localhost" in ch.base_url:
        return {
            "pool": ch.pool,
            "name": ch.name,
            "base_url": ch.base_url,
            "skipped": True,
            "reason": "local_loopback",
            "ok": None,
        }
    if ch.kind == "claude-api-key":
        # Direct probe only for diagnostics; 503 only-CC is soft. Prefer aggregate.
        model = "glm-5.2" if "bigmodel" in ch.base_url else "claude-opus-4-8"
        url = ch.base_url
        if not url.endswith("/messages"):
            if url.endswith("/v1"):
                url = url + "/messages"
            elif "anthropic" in url:
                url = url + "/v1/messages"
            else:
                url = url + "/v1/messages"
        code, body, elapsed = http(
            url,
            ch.api_key,
            {
                "model": model,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "Reply: PONG"}],
            },
            anthropic=True,
        )
        if code == 0:
            time.sleep(2)
            code, body, elapsed = http(
                url,
                ch.api_key,
                {
                    "model": model,
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "Reply: PONG"}],
                },
                anthropic=True,
            )
        ok = code == 200
        soft_fail = code in (0, 429, 500, 502, 503, 504)
        hard = code in (401, 403, 404)
        return {
            "pool": ch.pool,
            "name": ch.name,
            "base_url": ch.base_url,
            "chat_http": code,
            "chat_ms": int(elapsed * 1000),
            "ok": ok,
            "soft_fail": soft_fail and not ok,
            "quota_like": (not ok) and is_quota_like(code, body),
            "slow": ok and elapsed >= SLOW_CHAT_SECONDS,
            "error": None if ok else body[:160],
            "should_disable": (not ok) and hard,
        }

    # openai-compat: models + chat
    models_url = ch.base_url + "/models"
    code_m, body_m, _ = http(models_url, ch.api_key, extra_headers=ch.headers)
    if ch.probe_model:
        model = ch.probe_model
    elif ch.pool == "grok":
        model = "grok-4.5"
    elif ch.pool == "glm":
        model = "glm-5.2"
    else:
        model = "gpt-5.6-sol"

    def _chat(m: str) -> tuple[int, str, float]:
        return http(
            ch.base_url + "/chat/completions",
            ch.api_key,
            {
                "model": m,
                "messages": [{"role": "user", "content": "Reply: PONG"}],
                "max_tokens": 8,
            },
            extra_headers=ch.headers,
        )

    code_c, body_c, elapsed = _chat(model)
    if code_c == 0:
        time.sleep(2)
        code_c, body_c, elapsed = _chat(model)
    if ch.pool == "codex" and code_c != 200:
        code_c2, body_c2, el2 = _chat("gpt-5.6")
        if code_c2 == 200:
            code_c, body_c, elapsed = code_c2, body_c2, el2
    ok = code_c == 200
    soft = code_c in (0, 429, 500, 502, 503, 504)
    should = (not ok) and (not soft)
    return {
        "pool": ch.pool,
        "name": ch.name,
        "base_url": ch.base_url,
        "models_http": code_m,
        "chat_http": code_c,
        "chat_ms": int(elapsed * 1000),
        "ok": ok,
        "soft_fail": soft and not ok,
        "quota_like": (not ok) and is_quota_like(code_c, body_c),
        "slow": ok and elapsed >= SLOW_CHAT_SECONDS,
        "error": None if ok else body_c[:160],
        "should_disable": should,
    }


def probe_claude_aggregate() -> dict:
    """Client-path probe of unified Claude pool :8337 (cloak + hop)."""
    key = _pool_keys().get("claude", "sk-local-claude-unified-2026")
    code, body, elapsed = http(
        "http://127.0.0.1:8337/v1/messages",
        key,
        {
            "model": "claude-opus-4-8",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Reply: PONG"}],
        },
        anthropic=True,
        timeout=45.0,
    )
    ok = code == 200
    return {
        "pool": "claude",
        "name": "aggregate-8337",
        "base_url": "http://127.0.0.1:8337",
        "chat_http": code,
        "chat_ms": int(elapsed * 1000),
        "ok": ok,
        "soft_fail": (not ok) and code in (0, 429, 500, 502, 503, 504),
        "error": None if ok else body[:160],
        "should_disable": False,  # never auto-disable whole pool from aggregate
        "aggregate": True,
    }


def set_disabled(path: Path, start_line: int, disabled: bool = True) -> bool:
    """Insert or flip disabled under a list item starting at start_line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if start_line < 0 or start_line >= len(lines):
        return False
    # find indent of item
    # after start line, look for existing disabled within block
    j = start_line + 1
    block_end = len(lines)
    for k in range(start_line + 1, len(lines)):
        if re.match(r"^  - ", lines[k]) or (
            re.match(r"^[a-zA-Z0-9_-]+:", lines[k]) and not lines[k].startswith(" ")
        ):
            block_end = k
            break
    for k in range(start_line + 1, block_end):
        if re.match(r"^    disabled:\s*", lines[k]):
            lines[k] = f"    disabled: {'true' if disabled else 'false'}"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    # insert after first line of item
    lines.insert(start_line + 1, f"    disabled: {'true' if disabled else 'false'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def demote_main_aliases(path: Path, start_line: int, channel_name: str) -> bool:
    """Rewrite models: aliases on main path to remote-{name}-* debug aliases.

    Keeps upstream model names; only client-facing alias becomes remote-*.
    Idempotent if already all remote-*.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    if start_line < 0 or start_line >= len(lines):
        return False
    block_end = len(lines)
    for k in range(start_line + 1, len(lines)):
        if re.match(r"^  - ", lines[k]) or (
            re.match(r"^[a-zA-Z0-9_-]+:", lines[k]) and not lines[k].startswith(" ")
        ):
            block_end = k
            break
    changed = False
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", channel_name).strip("-").lower() or "src"
    for k in range(start_line + 1, block_end):
        am = re.match(r'^(\s+alias:\s*)["\']?([^"\'#]+?)["\']?\s*$', lines[k])
        if not am:
            continue
        alias = am.group(2).strip()
        if alias.startswith("remote-"):
            continue
        # preserve original alias token in remote name for debug
        new_alias = f"remote-{safe}-{alias}"
        lines[k] = f'{am.group(1)}"{new_alias}"'
        changed = True
    if not changed:
        return False
    # stamp a one-line comment after models: if present
    for k in range(start_line + 1, block_end):
        if re.match(r"^    models:\s*$", lines[k]):
            note = (
                f"      # auto-demote {time.strftime('%Y-%m-%d')}: main-path slow "
                f">={MAIN_DEMOTE_MS}ms → remote-* only"
            )
            if k + 1 >= block_end or "auto-demote" not in lines[k + 1]:
                lines.insert(k + 1, note)
            break
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def kill_by_config(config_name: str) -> None:
    """Kill cli-proxy-api.exe processes whose CommandLine matches config_name."""
    needle = config_name.lower()
    # Single-line PS: bare config.yaml must exclude codex/claude/glm siblings.
    if needle == "config.yaml":
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='cli-proxy-api.exe'\" | "
            "ForEach-Object { $cl=($_.CommandLine+'').ToLower(); "
            "if ($cl.Contains('config.yaml') -and -not $cl.Contains('config-codex') "
            "-and -not $cl.Contains('config-claude') -and -not $cl.Contains('config-glm')) "
            "{ $_.ProcessId } }"
        )
    else:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='cli-proxy-api.exe'\" | "
            f"ForEach-Object {{ $cl=($_.CommandLine+'').ToLower(); "
            f"if ($cl.Contains('{needle}')) {{ $_.ProcessId }} }}"
        )
    out = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", ps], text=True, errors="replace"
    )
    for tok in out.split():
        if tok.strip().isdigit():
            subprocess.run(["taskkill", "/PID", tok.strip(), "/F"], check=False)
            print("killed pid", tok.strip(), "for", config_name)


def restart_fleet() -> None:
    subprocess.run(
        [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            r"D:\Users\grok-auto-register\scripts\cliproxy_fleet_watchdog.ps1",
            "-Once",
        ],
        check=False,
    )


def reenable_recovered(
    channels: list[Channel],
    temp: dict,
    *,
    recover_hours: float = DEFAULT_SOFT_RECOVER_HOURS,
) -> tuple[list[dict], list[dict], bool]:
    """Process temp ledger entries past recover_after.

    Returns (revived, deferred, ledger_dirty).
    - revived: re-enabled into yaml (will be probed this run)
    - deferred: still bad on pre-check OR missing — recover_after extended / entry dropped
    - ledger_dirty: temp dict mutated and needs save

    Same-round fail-fast: after enable we probe immediately; if still quota/slow,
    re-disable and extend recover_after (do not wait another soft_streak cycle).
    """
    now = time.time()
    revived: list[dict] = []
    deferred: list[dict] = []
    dirty = False
    recover_sec = max(0.5, float(recover_hours)) * 3600
    by_key = {f"{c.pool}/{c.name}": c for c in channels}
    for key, meta in list(temp.items()):
        recover_after = float(meta.get("recover_after") or 0)
        if recover_after and now < recover_after:
            continue
        ch = by_key.get(key)
        if not ch:
            temp.pop(key, None)
            dirty = True
            continue
        if not ch.disabled:
            # already enabled outside ledger — drop stale entry
            temp.pop(key, None)
            dirty = True
            continue
        path = CONFIGS.get(ch.pool)
        if not path:
            continue
        # Tentative enable for probe
        ok_en = set_disabled(path, ch.start_line, False)
        ch.disabled = False
        r = probe_channel(ch)
        still_bad = bool(
            r.get("should_disable")
            or (r.get("soft_fail") and r.get("quota_like"))
            or r.get("slow")
        )
        if still_bad:
            set_disabled(path, ch.start_line, True)
            ch.disabled = True
            meta = dict(meta)
            meta["recover_after"] = now + recover_sec
            meta["last_reprobe"] = {
                "chat_http": r.get("chat_http"),
                "chat_ms": r.get("chat_ms"),
                "quota_like": r.get("quota_like"),
                "slow": r.get("slow"),
                "error": (r.get("error") or "")[:120],
                "ts": now,
            }
            meta["extend_count"] = int(meta.get("extend_count") or 0) + 1
            temp[key] = meta
            dirty = True
            print(
                "re-probe still bad, extend recover",
                key,
                r.get("chat_http"),
                r.get("chat_ms"),
                "ms",
            )
            deferred.append(
                {
                    "pool": ch.pool,
                    "name": ch.name,
                    "extended": True,
                    "chat_http": r.get("chat_http"),
                    "chat_ms": r.get("chat_ms"),
                }
            )
            continue
        # healthy — leave enabled, drop ledger
        print("re-enable ok", key, r.get("chat_http"), r.get("chat_ms"), "ms", ok_en)
        revived.append(
            {
                "pool": ch.pool,
                "name": ch.name,
                "reenabled": ok_en,
                "chat_http": r.get("chat_http"),
                "chat_ms": r.get("chat_ms"),
                "was": meta,
            }
        )
        temp.pop(key, None)
        dirty = True
    return revived, deferred, dirty


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restart-fleet", action="store_true")
    ap.add_argument(
        "--include-claude",
        action="store_true",
        help="probe claude-api-key directs (noisy; prefer aggregate)",
    )
    ap.add_argument(
        "--auto",
        action="store_true",
        help=(
            "daily/hourly: hard 401/403/404 disable day-1; soft quota/slow temp-out "
            "with recover_after; re-enable when recover due; reload fleet on write"
        ),
    )
    ap.add_argument(
        "--soft-recover-hours",
        type=float,
        default=DEFAULT_SOFT_RECOVER_HOURS,
        help="hours before temp-disabled charity channel is re-probed (default 6)",
    )
    args = ap.parse_args()

    channels: list[Channel] = []
    channels += parse_openai_compat(CONFIGS["grok"], "grok")
    channels += parse_openai_compat(CONFIGS["codex"], "codex")
    channels += parse_openai_compat(CONFIGS["glm"], "glm")
    claude_channels = parse_claude_keys(CONFIGS["claude"], "claude")
    if args.include_claude:
        channels += claude_channels

    temp = _load_json(TEMP_DISABLE_FILE)
    temp_dirty = False
    revived: list[dict] = []
    deferred: list[dict] = []
    if args.auto:
        revived, deferred, temp_dirty = reenable_recovered(
            channels + claude_channels,
            temp,
            recover_hours=float(args.soft_recover_hours),
        )
        if temp_dirty:
            _save_json(TEMP_DISABLE_FILE, temp)

    results: list[dict] = []
    to_disable: list[tuple[Channel, dict]] = []
    soft_candidates: list[tuple[Channel, dict]] = []  # quota/slow → temp
    main_slow_candidates: list[tuple[Channel, dict]] = []  # 200 but slow on main alias

    for ch in channels:
        r = probe_channel(ch)
        r["on_main_path"] = ch.on_main_path
        r["aliases"] = list(ch.aliases)
        results.append(r)
        mark = "OK" if r.get("ok") else ("SKIP" if r.get("skipped") else "BAD")
        ms = int(r.get("chat_ms") or 0)
        if r.get("ok") and ch.on_main_path and ms >= MAIN_DEMOTE_MS:
            mark = "MAIN_SLOW"
            main_slow_candidates.append((ch, r))
        elif r.get("slow"):
            mark = "SLOW"
        print(f"[{mark}] {ch.pool}/{ch.name} {ch.base_url} {r}")
        if r.get("should_disable"):
            to_disable.append((ch, r))
        elif r.get("soft_fail") and r.get("quota_like"):
            soft_candidates.append((ch, r))
        elif r.get("slow") and not ch.on_main_path:
            # remote-only slow → temp ledger (existing path)
            soft_candidates.append((ch, r))


    if args.auto:
        agg = probe_claude_aggregate()
        results.append(agg)
        print(
            f"[{'OK' if agg.get('ok') else 'BAD'}] claude/aggregate-8337 "
            f"{agg.get('chat_http')} {agg.get('chat_ms')}ms"
        )

    pending_streak: list[str] = []
    soft_temp_selected: list[tuple[Channel, dict, str]] = []
    demoted: list[dict] = []

    if args.auto:
        streak = _load_json(STREAK_FILE)
        soft_streak = _load_json(SOFT_STREAK_FILE)
        main_slow_streak = _load_json(MAIN_SLOW_STREAK_FILE)

        for ch, r in to_disable:
            key = f"{ch.pool}/{ch.name}"
            streak[key] = int(streak.get(key) or 0) + 1
        bad_keys = {f"{c.pool}/{c.name}" for c, _ in to_disable}
        for k in list(streak):
            if k not in bad_keys:
                streak.pop(k)
        _save_json(STREAK_FILE, streak)

        auto_selected: list[tuple[Channel, dict, str]] = []
        for c, r in to_disable:
            key = f"{c.pool}/{c.name}"
            n = int(streak.get(key) or 0)
            code = r.get("chat_http")
            hard = code in (401, 403, 404)
            need = 1 if hard else 2
            if n >= need:
                reason = f"hard_{code}_streak={n}" if hard else f"streak={n}>={need}"
                auto_selected.append((c, r, reason))
            else:
                pending_streak.append(f"{key} streak={n}/{need} chat={code}")
        to_disable = [(c, r) for c, r, _ in auto_selected]

        soft_keys_now = set()
        for ch, r in soft_candidates:
            key = f"{ch.pool}/{ch.name}"
            soft_keys_now.add(key)
            soft_streak[key] = int(soft_streak.get(key) or 0) + 1
        for k in list(soft_streak):
            if k not in soft_keys_now:
                soft_streak.pop(k)
        _save_json(SOFT_STREAK_FILE, soft_streak)

        recover_sec = max(0.5, float(args.soft_recover_hours)) * 3600
        for ch, r in soft_candidates:
            key = f"{ch.pool}/{ch.name}"
            n = int(soft_streak.get(key) or 0)
            if n < SOFT_STREAK_TO_TEMP:
                pending_streak.append(
                    f"{key} soft_streak={n}/{SOFT_STREAK_TO_TEMP} "
                    f"chat={r.get('chat_http')} ms={r.get('chat_ms')}"
                )
                continue
            if ch.disabled:
                continue
            why = "quota" if r.get("quota_like") else ("slow" if r.get("slow") else "soft")
            soft_temp_selected.append((ch, r, f"{why}_streak={n}"))
            temp[key] = {
                "pool": ch.pool,
                "name": ch.name,
                "kind": "temp",
                "reason": why,
                "chat_http": r.get("chat_http"),
                "chat_ms": r.get("chat_ms"),
                "error": (r.get("error") or "")[:120],
                "disabled_at": time.time(),
                "recover_after": time.time() + recover_sec,
                "recover_hours": args.soft_recover_hours,
            }
            temp_dirty = True

        # Hard-disable must not be auto-revived by temp ledger later
        for c, r, _why in auto_selected:
            key = f"{c.pool}/{c.name}"
            if key in temp:
                temp.pop(key, None)
                temp_dirty = True

        # Main-path slow demotion: keep channel enabled, rewrite aliases → remote-*
        slow_keys_now = set()
        for ch, r in main_slow_candidates:
            key = f"{ch.pool}/{ch.name}"
            slow_keys_now.add(key)
            main_slow_streak[key] = int(main_slow_streak.get(key) or 0) + 1
        for k in list(main_slow_streak):
            if k not in slow_keys_now:
                main_slow_streak.pop(k)
        _save_json(MAIN_SLOW_STREAK_FILE, main_slow_streak)

        demote_list: list[tuple[Channel, dict, int]] = []
        for ch, r in main_slow_candidates:
            key = f"{ch.pool}/{ch.name}"
            n = int(main_slow_streak.get(key) or 0)
            ms = int(r.get("chat_ms") or 0)
            if n < MAIN_SLOW_STREAK_TO_DEMOTE:
                pending_streak.append(
                    f"{key} main_slow_streak={n}/{MAIN_SLOW_STREAK_TO_DEMOTE} ms={ms}"
                )
                continue
            if ch.disabled or not ch.on_main_path:
                continue
            demote_list.append((ch, r, n))

        if to_disable or soft_temp_selected or revived or deferred or demote_list:
            args.apply = True
            args.restart_fleet = True
        if to_disable:
            print(
                "auto: hard-disable:",
                [f"{c.pool}/{c.name}({why})" for c, _, why in auto_selected],
            )
        if soft_temp_selected:
            print(
                "auto: temp-disable (recover later):",
                [f"{c.pool}/{c.name}({why})" for c, _, why in soft_temp_selected],
            )
        if demote_list:
            print(
                "auto: demote-main (remote-* only):",
                [f"{c.pool}/{c.name} ms={r.get('chat_ms')} streak={n}" for c, r, n in demote_list],
            )
        if deferred:
            print("auto: recover extended (still bad):", deferred)
        if pending_streak:
            print("auto: pending:", pending_streak)
    else:
        demote_list = []

    applied = []
    write_list: list[tuple[Channel, dict, str]] = []
    for ch, r in to_disable:
        write_list.append((ch, r, "hard"))
    for ch, r, why in soft_temp_selected:
        write_list.append((ch, r, f"temp:{why}"))

    if args.apply and (write_list or revived or deferred or demote_list):
        ts = time.strftime("%Y%m%d_%H%M%S")
        paths = {CONFIGS[c.pool] for c, _, _ in write_list if c.pool in CONFIGS}
        paths |= {CONFIGS[c.pool] for c, _, _ in demote_list if c.pool in CONFIGS}
        if revived:
            paths |= {CONFIGS[x["pool"]] for x in revived if x.get("pool") in CONFIGS}
        if deferred:
            paths |= {CONFIGS[x["pool"]] for x in deferred if x.get("pool") in CONFIGS}
        for p in paths:
            bak = p.with_suffix(p.suffix + f".bak-disable-{ts}")
            bak.write_bytes(p.read_bytes())
            print("backup", bak)
        for ch, r, kind in sorted(write_list, key=lambda x: -x[0].start_line):
            path = CONFIGS[ch.pool]
            ok = set_disabled(path, ch.start_line, True)
            applied.append(
                {
                    "pool": ch.pool,
                    "name": ch.name,
                    "wrote": ok,
                    "kind": kind,
                    "chat_http": r.get("chat_http"),
                    "chat_ms": r.get("chat_ms"),
                }
            )
            print("disabled", kind, ch.pool, ch.name, ok)
        for ch, r, n in sorted(demote_list, key=lambda x: -x[0].start_line):
            path = CONFIGS[ch.pool]
            ok = demote_main_aliases(path, ch.start_line, ch.name)
            demoted.append(
                {
                    "pool": ch.pool,
                    "name": ch.name,
                    "wrote": ok,
                    "kind": "demote_main",
                    "chat_ms": r.get("chat_ms"),
                    "streak": n,
                    "aliases_before": list(ch.aliases),
                }
            )
            applied.append(demoted[-1])
            print("demoted-main", ch.pool, ch.name, ok, r.get("chat_ms"), "ms")
        if temp_dirty or soft_temp_selected or deferred:
            _save_json(TEMP_DISABLE_FILE, temp)
        if args.restart_fleet and (write_list or revived or deferred or demote_list):
            affected = {c.pool for c, _, _ in write_list} | {
                c.pool for c, _, _ in demote_list
            } | {x["pool"] for x in revived if x.get("reenabled")} | {
                x["pool"] for x in deferred
            }
            cfg_map = {
                "grok": "config.yaml",
                "codex": "config-codex.yaml",
                "claude": "config-claude.yaml",
                "glm": "config-glm.yaml",
            }
            for pool in affected:
                cfg = cfg_map.get(pool)
                if cfg:
                    kill_by_config(cfg)
            time.sleep(2)
            restart_fleet()
    elif args.auto and temp_dirty:
        _save_json(TEMP_DISABLE_FILE, temp)

    report = {
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "apply": bool(args.apply),
        "auto": bool(args.auto),
        "results": results,
        "applied": applied,
        "demoted": demoted if args.auto else [],
        "revived": revived,
        "recover_deferred": deferred,
        "pending_streak": pending_streak,
        "main_demote_ms": MAIN_DEMOTE_MS,
        "main_slow_streak_to_demote": MAIN_SLOW_STREAK_TO_DEMOTE,
        "main_slow_streak": _load_json(MAIN_SLOW_STREAK_FILE),
        "temp_disable_ledger": temp if args.auto else _load_json(TEMP_DISABLE_FILE),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", REPORT)
    if args.auto:
        return 0
    hard = [r for r in results if r.get("should_disable")]
    return 1 if hard and not args.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
