#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cc-switch Claude 四渠道轮换。

数据源：C:/Users/zhugu/.cc-switch/cc-switch.db（只读，mode=ro，不与 GUI 抢锁）。
写入点：
  - C:/Users/zhugu/.claude/settings.json 的 env（Claude Code 每次启动重读 → 下个会话生效）
  - Windows User 环境变量 ANTHROPIC_*（旧链路兜底，新进程即生效）

用法：
  python scripts/cc_rotate_claude_provider.py list            # 列出渠道 + 当前激活
  python scripts/cc_rotate_claude_provider.py current         # 当前激活渠道
  python scripts/cc_rotate_claude_provider.py next            # 轮换到下一个（环形）
  python scripts/cc_rotate_claude_provider.py switch <关键字>  # 手动切到指定渠道（匹配 name/id/url）

注意：
  - env 重建规则：丢掉旧渠道的所有 ANTHROPIC_*，写入新渠道 env 全集；
    保留非 ANTHROPIC 键（如 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC）。
  - 渠道无 ANTHROPIC_MODEL 时，从 settings.json 与 User env 删除该键（防止旧 model 污染）。
  - 状态/审计：logs/_cc_claude_rotate.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

CC_DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
SETTINGS = Path(r"C:/Users/zhugu/.claude/settings.json")
STATE = Path(__file__).resolve().parent.parent / "logs" / "_cc_claude_rotate.json"

# Shared metrics data dir (A2A bridge data dir) for agentic probe + truncated events
_A2A_DATA_DIR = Path(r"C:/Users/zhugu/.kimi-code/mcp-a2a-bridge/data")
_AGENTIC_PROBE_METRICS = _A2A_DATA_DIR / "agentic_probe_metrics.jsonl"

# 固定轮换顺序（按 name+id 排序，与 GUI 无关，稳定可预期）


def load_providers() -> list[dict]:
    db = sqlite3.connect(f"file:{CC_DB}?mode=ro", uri=True)
    rows = list(
        db.execute(
            "SELECT id, name, settings_config FROM providers WHERE app_type='claude'"
        )
    )
    out = []
    for pid, name, cfg in rows:
        env = (json.loads(cfg) or {}).get("env", {}) or {}
        if not env.get("ANTHROPIC_BASE_URL") or not env.get("ANTHROPIC_AUTH_TOKEN"):
            continue  # 跳过缺凭据的条目
        out.append({"id": pid, "name": name, "env": env})
    out.sort(key=lambda p: (p["name"], p["id"]))
    return out


def read_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def current_token() -> str:
    return (read_settings().get("env", {}) or {}).get("ANTHROPIC_AUTH_TOKEN", "")


def current_provider(providers: list[dict]) -> dict | None:
    tok = current_token()
    env_now = read_settings().get("env", {}) or {}
    model_now = env_now.get("ANTHROPIC_MODEL", "")
    url_now = env_now.get("ANTHROPIC_BASE_URL", "")
    candidates = [p for p in providers if p["env"].get("ANTHROPIC_AUTH_TOKEN") == tok]
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    # 同 token 多渠道（如 GLM 团队 key 共享）：再用 model/base_url 消歧
    for p in candidates:
        pe = p["env"]
        if pe.get("ANTHROPIC_MODEL", "") == model_now and pe.get("ANTHROPIC_BASE_URL", "") == url_now:
            return p
    for p in candidates:
        if p["env"].get("ANTHROPIC_BASE_URL", "") == url_now:
            return p
    return candidates[0]


LOCKFILE = Path.home() / ".claude" / ".settings.json.lock"
PINFILE = Path.home() / ".claude" / ".channel_pin.json"


def _acquire_lock():
    """Cross-process lock via msvcrt.locking on settings.json.lock.
    Blocks up to 10s. Returns fd (locked) or None (timeout).
    """
    import msvcrt
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCKFILE), os.O_CREAT | os.O_RDWR, 0o666)
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return fd
        except (OSError, BlockingIOError):
            time.sleep(0.05)
    # timeout: force acquire
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return fd
    except (OSError, BlockingIOError):
        os.close(fd)
        return None


def _release_lock(fd):
    """Release cross-process lock."""
    import msvcrt
    if fd is not None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except (OSError, BlockingIOError):
            pass
        os.close(fd)


def set_user_env(key: str, value: str | None) -> None:
    """写/删 Windows User 环境变量（winreg，避免 setx 1024 截断与广播）。"""
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as k:
        try:
            if value is None:
                winreg.DeleteValue(k, key)
            else:
                winreg.SetValueEx(k, key, 0, winreg.REG_SZ, value)
        except FileNotFoundError:
            pass


def _write_settings_atomic(s: dict) -> None:
    """Atomically write settings.json: write tmp file then os.replace.
    Caller must hold _acquire_lock.
    """
    content = json.dumps(s, ensure_ascii=False, indent=2) + "\n"
    fd_tmp, tmp = tempfile.mkstemp(dir=str(SETTINGS.parent), suffix=".tmp")
    try:
        with os.fdopen(fd_tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(SETTINGS))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_provider(p: dict) -> None:
    # 1) cross-process lock FIRST: the read-modify-write below must be fully
    #    covered by the lock, otherwise concurrent writers can lose
    #    non-ANTHROPIC keys (B2 fix — read moved inside the lock).
    lock_fd = _acquire_lock()
    if lock_fd is None:
        raise RuntimeError("锁获取失败，放弃写入 settings.json")
    try:
        s = read_settings()
        env = s.get("env", {}) or {}
        # 2) remove old ANTHROPIC_* keys
        env = {k: v for k, v in env.items() if not k.startswith("ANTHROPIC_")}
        # 3) write new provider env
        env.update(p["env"])
        s["env"] = env
        _write_settings_atomic(s)
    finally:
        _release_lock(lock_fd)
    # 4) sync User env
    set_user_env("ANTHROPIC_BASE_URL", p["env"].get("ANTHROPIC_BASE_URL"))
    set_user_env("ANTHROPIC_AUTH_TOKEN", p["env"].get("ANTHROPIC_AUTH_TOKEN"))
    set_user_env("ANTHROPIC_MODEL", p["env"].get("ANTHROPIC_MODEL"))


def record(entry: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    st = {"history": []}
    if STATE.exists():
        try:
            st = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    st["history"] = (st.get("history") or [])[-19:] + [entry]
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def read_pin() -> dict | None:
    """Read pin file. Returns {channel, expires_at, ...} or None."""
    if not PINFILE.exists():
        return None
    try:
        return json.loads(PINFILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_pin(channel_name: str, ttl_s: int = 600) -> None:
    """Write pin file marking current channel + expiry.
    Called by watchdog after switching back to real Claude.
    Includes token_suffix so wrapper can verify pin matches current channel.
    """
    PINFILE.parent.mkdir(parents=True, exist_ok=True)
    settings = read_settings()
    cur_token = (settings.get("env", {}) or {}).get("ANTHROPIC_AUTH_TOKEN", "")
    pin = {
        "channel": channel_name,
        "token_suffix": cur_token[-12:] if cur_token else "",
        "written_at": time.time(),
        "expires_at": time.time() + ttl_s,
        "ttl_s": ttl_s,
    }
    tmp = str(PINFILE) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pin, f, ensure_ascii=False)
        os.replace(tmp, str(PINFILE))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def fmt(p: dict) -> str:
    e = p["env"]
    return f"{p['name']} | {e.get('ANTHROPIC_BASE_URL')} | ...{e.get('ANTHROPIC_AUTH_TOKEN','')[-6:]} | model={e.get('ANTHROPIC_MODEL','-')}"



def _probe_single(p: dict) -> tuple[bool, str, float]:
    """Probe a single channel. Returns (healthy, note, elapsed_s).
    Never prints full token (max last 6 chars).
    """
    import requests
    from urllib.parse import urlparse

    url = p["env"]["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages"
    token = p["env"]["ANTHROPIC_AUTH_TOKEN"]

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}]
    })
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": token,
    }

    start = time.time()
    try:
        resp = requests.post(url, json=json.loads(payload), headers=headers, timeout=(5, 10))
        code = resp.status_code
        body = resp.text
    except requests.exceptions.Timeout:
        code, body = 0, "timeout"
    except requests.exceptions.ConnectionError as e:
        code, body = 0, f"conn: {e}"
    except Exception as e:
        code, body = 0, str(e)

    elapsed = time.time() - start
    healthy = 200 <= code < 300
    note = ""
    if not healthy:
        lb = body.lower()
        if "only allows claude code clients" in lb:
            note = "client-rstr"
        elif code in (502, 503):
            note = "down"
        elif code == 0:
            note = "unreachable"
        else:
            note = f"err-{code}"
    return healthy, note, elapsed




def _agentic_probe_single(p: dict):
    """Agentic probe: send request with dummy tool, check tool_use.
    Returns (status, reason, elapsed_s).
    """
    url = p["env"]["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages"
    token = p["env"]["ANTHROPIC_AUTH_TOKEN"]
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 256,
        "tools": [{
            "name": "get_weather",
            "description": "Get current weather for a location",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"}
                },
                "required": ["location"]
            }
        }],
        "messages": [
            {"role": "user", "content": "What's the weather in Beijing? Use the get_weather tool to answer."}
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": token,
    }
    start = time.time()
    import requests
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=(5, 15))
        elapsed = time.time() - start
        code = resp.status_code
        if 200 <= code < 300:
            try:
                data = resp.json()
            except Exception:
                return "agentic-degraded", "non-json response", elapsed
            content = data.get("content", [])
            stop_reason = data.get("stop_reason", "")
            has_tool_use = any(b.get("type") == "tool_use" for b in content)
            if has_tool_use and stop_reason == "tool_use":
                return "agentic-ok", "", elapsed
            elif has_tool_use:
                return "agentic-degraded", "tool_use present but stop_reason=" + str(stop_reason), elapsed
            elif stop_reason == "end_turn":
                return "agentic-degraded", "no tool_use, stop_reason=end_turn", elapsed
            else:
                return "agentic-degraded", "no tool_use, stop_reason=" + str(stop_reason), elapsed
        else:
            return "down", "http-" + str(code), time.time() - start
    except requests.exceptions.Timeout:
        return "down", "timeout", time.time() - start
    except requests.exceptions.ConnectionError as e:
        return "down", "conn: " + str(e), time.time() - start
    except Exception as e:
        return "down", str(e), time.time() - start


def _write_agentic_probe_metrics(status, reason, channel_name, channel_url, elapsed):
    """Append one agentic-probe result to agentic_probe_metrics.jsonl."""
    row = {
        "ts": time.time(),
        "channel": channel_name,
        "url": channel_url,
        "status": status,
        "reason": reason,
        "elapsed_s": round(elapsed, 3),
    }
    try:
        _AGENTIC_PROBE_METRICS.parent.mkdir(parents=True, exist_ok=True)
        with _AGENTIC_PROBE_METRICS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass

def cmd_probe(providers: list[dict]) -> int:
    """probe 子命令：轻量探测所有渠道的 /v1/messages 端点。"""
    import requests
    from urllib.parse import urlparse

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}]
    })
    headers_tmpl = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    print(f"{'名称':<10} | {'host':<24} | {'http_code':>9} | {'耗时':>6} | healthy")
    print("-" * 70)
    for p in providers:
        url = p["env"]["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages"
        token = p["env"]["ANTHROPIC_AUTH_TOKEN"]
        host = urlparse(url).hostname or url

        headers = dict(headers_tmpl)
        headers["x-api-key"] = token

        start = time.time()
        try:
            resp = requests.post(url, json=json.loads(payload), headers=headers, timeout=(5, 10))
            code = resp.status_code
            body = resp.text
            elapsed = time.time() - start
        except requests.exceptions.Timeout:
            code, body, elapsed = 0, "timeout", time.time() - start
        except requests.exceptions.ConnectionError as e:
            code, body, elapsed = 0, f"conn: {e}", time.time() - start
        except Exception as e:
            code, body, elapsed = 0, str(e), time.time() - start

        healthy = 200 <= code < 300
        note = ""
        if not healthy:
            lb = body.lower()
            if "only allows claude code clients" in lb:
                note = "client-rstr"
            elif code in (502, 503):
                note = "down"
            elif code == 0:
                note = "unreachable"
            else:
                note = f"err-{code}"

        status = "OK" if healthy else f"FAIL {note}" if note else "FAIL"
        # token safety: never print full token
        print(f"{p['name']:<10} | {host:<24} | {code:>9} | {elapsed:>5.1f}s | {status}")
    return 0



def cmd_agentic_probe(providers: list[dict]) -> int:
    """agentic-probe subcommand: probe all channels with dummy tool."""
    print(f"{'名称':<12} | {'status':<18} | {'原因':<40} | {'耗时':>6}")
    print("-" * 80)
    for p in providers:
        status, reason, elapsed = _agentic_probe_single(p)
        _write_agentic_probe_metrics(status, reason, p["name"], p["env"].get("ANTHROPIC_BASE_URL", ""), elapsed)
        print(f"{p['name']:<12} | {status:<18} | {reason:<40} | {elapsed:>5.1f}s")
    return 0

def main() -> int:
    ap = argparse.ArgumentParser(description="cc-switch Claude 渠道轮换")
    ap.add_argument("cmd", choices=["list", "current", "next", "switch", "probe", "probe-one", "try-next", "agentic-probe"])
    ap.add_argument("pattern", nargs="?", help="switch 用的关键字")
    ap.add_argument("--agentic", action="store_true", help="use agentic probe for try-next")
    args = ap.parse_args()

    providers = load_providers()
    if not providers:
        print("[!] cc-switch DB 里没有可用 claude 渠道")
        return 2
    cur = current_provider(providers)


    if args.cmd == "probe":
        return cmd_probe(providers)

    if args.cmd == "agentic-probe":
        return cmd_agentic_probe(providers)

    if args.cmd == "probe-one":
        if not args.pattern:
            print("[!] probe-one needs a keyword")
            return 2
        pat = args.pattern.lower()
        hit = [p for p in providers if pat in p["name"].lower() or pat in p["id"].lower()]
        if len(hit) == 0:
            print("[!] no matching channel")
            return 2
        p = hit[0]
        healthy, note, elapsed = _probe_single(p)
        status = "OK" if healthy else f"FAIL {note}" if note else "FAIL"
        e = p["env"]
        print(f"{p['name']} | {e.get('ANTHROPIC_BASE_URL')} | ...{e.get('ANTHROPIC_AUTH_TOKEN','')[-6:]} | time={elapsed:.1f}s | {status}")
        return 0 if healthy else 1

    if args.cmd == "try-next":
        # probe-then-switch: probe candidates, switch to first healthy
        probe_fn = _agentic_probe_single if args.agentic else _probe_single
        if cur is None:
            candidates = providers
        else:
            from urllib.parse import urlparse
            idx = next(i for i, p in enumerate(providers) if p["id"] == cur["id"])
            cur_host = urlparse(cur["env"].get("ANTHROPIC_BASE_URL", "")).hostname
            candidates = []
            for i in range(1, len(providers)):
                nxt = (idx + i) % len(providers)
                p = providers[nxt]
                if urlparse(p["env"].get("ANTHROPIC_BASE_URL", "")).hostname != cur_host:
                    candidates.append(p)
            if not candidates:
                candidates = [providers[(idx + 1) % len(providers)]]

        for p in candidates:
            if args.agentic:
                status, note, _ = probe_fn(p)
                accept = status == "agentic-ok"
            else:
                healthy, note, _ = probe_fn(p)
                accept = healthy
            if accept:
                if cur and p["id"] == cur["id"]:
                    mode_label = "agentic-ok" if args.agentic else "already on target"
                    print(f"[=] {mode_label}: {fmt(p)}")
                    return 0
                apply_provider(p)
                mode_name = "try-next-agentic" if args.agentic else "probe-then-switch"
                record({
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "from": cur["name"] if cur else None,
                    "to": p["name"],
                    "url": p["env"].get("ANTHROPIC_BASE_URL"),
                    "mode": mode_name,
                })
                label = "agentic-ok" if args.agentic else "switched"
                print(f"[ok] {label} -> {fmt(p)}")
                return 0
            e = p["env"]
            status_str = f" | ({status})" if args.agentic else ""
            print(f"[!] candidate unhealthy {p['name']} | {e.get('ANTHROPIC_BASE_URL')} | ...{e.get('ANTHROPIC_AUTH_TOKEN','')[-6:]} | {note}{status_str}", flush=True)

        print("[!] all candidates unhealthy, skipping rotation", flush=True)
        return 2

    if args.cmd == "list":
        for i, p in enumerate(providers):
            mark = " <== 当前" if cur and p["id"] == cur["id"] else ""
            print(f"[{i}] {fmt(p)}{mark}")
        return 0

    if args.cmd == "current":
        print(fmt(cur) if cur else f"未匹配（settings.json token ...{current_token()[-6:]} 不在 cc-switch 渠道中）")
        return 0

    if args.cmd == "switch":
        if not args.pattern:
            print("[!] switch 需要关键字")
            return 2
        pat = args.pattern.lower()
        hit = [p for p in providers if pat in p["name"].lower() or pat in p["id"].lower() or pat in p["env"].get("ANTHROPIC_BASE_URL", "").lower()]
        if len(hit) != 1:
            print(f"[!] 匹配到 {len(hit)} 个：{[h['name'] for h in hit]}")
            return 2
        target = hit[0]
    else:  # next — host-aware: 优先切到不同 host 的渠道
        if cur is None:
            target = providers[0]
        else:
            from urllib.parse import urlparse
            idx = next(i for i, p in enumerate(providers) if p["id"] == cur["id"])
            cur_host = urlparse(cur["env"].get("ANTHROPIC_BASE_URL", "")).hostname
            target = None
            for i in range(1, len(providers)):
                nxt = (idx + i) % len(providers)
                p = providers[nxt]
                if urlparse(p["env"].get("ANTHROPIC_BASE_URL", "")).hostname != cur_host:
                    target = p
                    break
            if target is None:
                target = providers[(idx + 1) % len(providers)]

    if cur and target["id"] == cur["id"]:
        print(f"[=] 已是目标渠道：{fmt(target)}")
        return 0
    apply_provider(target)
    record({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "from": cur["name"] if cur else None,
        "to": target["name"],
        "url": target["env"].get("ANTHROPIC_BASE_URL"),
    })
    print(f"[ok] 已切换 → {fmt(target)}")
    print("     生效时机：下个 Claude Code 会话（进行中的会话不受影响）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
