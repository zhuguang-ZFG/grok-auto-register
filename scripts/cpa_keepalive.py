#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CPA 号池保活 + 死号清理脚本。

社区经验（linux.do 2579944 / 2569034 / 2579061）：
Grok free 号注册后如果不使用，xAI 反滥用系统会在 30 分钟到数小时内封号。
"用了就不会死，不用就死" —— 注册后立即使用过的号会进入"活跃"状态存活更久。

此脚本做三件事：
1. **续期**：access_token 过期或即将过期（< 30 min）时用 refresh_token 换新
2. **保活**：发一个轻量 grok-4.5 chat 请求，让账号保持活跃
3. **清理**：连续保活失败的号移到 cpa_auths_dead/（不删，留审计）

策略：
- 按 expired 时间升序排列（最先过期的优先处理）
- access_token 过期 → 先 refresh，refresh 失败 → 标死
- refresh 成功或 token 仍有效 → 发 keepalive chat
- 保活失败连续 >= max_fail_streak → 移到 dead 目录

用法:
    python scripts/cpa_keepalive.py                     # 跑一轮，默认 100 号
    python scripts/cpa_keepalive.py --max 200           # 每轮最多 200 号
    python scripts/cpa_keepalive.py --interval 3h       # 循环模式，每 3 小时一轮
    python scripts/cpa_keepalive.py --dry-run           # 只探测不写文件
    python scripts/cpa_keepalive.py --proxy http://127.0.0.1:7897
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CPA_AUTH_DIR = PROJECT_ROOT / "cpa_auths"
CPA_DEAD_DIR = PROJECT_ROOT / "cpa_auths_dead"
BASE_URL = "https://cli-chat-proxy.grok.com/v1"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
KEEPALIVE_TIMEOUT = 60
REFRESH_TIMEOUT = 30
REFRESH_MARGIN_MINUTES = 30  # access_token 距过期 < 30 min 就续期
MAX_FAIL_STREAK = 3          # 连续失败 N 次移到 dead
INTER_ACCOUNT_DELAY = 2      # 号间间隔秒数，防 rate limit

DEFAULT_HEADERS = {
    "x-grok-client-version": "0.2.93",
    "x-xai-token-auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-shell",
    "User-Agent": "grok-shell/0.2.93 (linux; x86_64)",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _build_opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    handlers: list[Any] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()


def load_accounts(max_count: int = 100) -> list[dict]:
    """加载 cpa_auths/ 下所有未禁用的号，按 expired 升序排列。"""
    accounts: list[dict] = []
    for f in CPA_AUTH_DIR.glob("xai-*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("disabled"):
            continue
        token = data.get("access_token", "")
        if not token:
            continue
        expired_dt = _parse_iso(data.get("expired", "")) or _now()
        accounts.append({
            "file": f,
            "data": data,
            "token": token,
            "refresh_token": data.get("refresh_token", ""),
            "token_endpoint": data.get("token_endpoint", TOKEN_URL),
            "email": data.get("email", ""),
            "sub": data.get("sub", ""),
            "expired": expired_dt,
            "fail_streak": int(data.get("_keepalive_fail_streak", 0)),
        })
    accounts.sort(key=lambda x: x["expired"])
    return accounts[:max_count]


def refresh_one(account: dict, proxy: str | None = None) -> dict:
    """用 refresh_token 换新 access_token。成功返回新 token dict。"""
    import urllib.parse

    rt = account.get("refresh_token", "").strip()
    if not rt:
        return {"ok": False, "error": "no refresh_token"}

    token_endpoint = account.get("token_endpoint") or TOKEN_URL
    opener = _build_opener(proxy)
    # xAI token endpoint 要 form-urlencoded，不是 JSON（JSON 会返回 415）。
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": CLIENT_ID,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "grok-reg-cpa-keepalive/1.0",
    }
    req = urllib.request.Request(token_endpoint, data=payload, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=REFRESH_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return {
                "ok": True,
                "access_token": body.get("access_token", ""),
                "refresh_token": body.get("refresh_token", rt),
                "id_token": body.get("id_token", ""),
                "expires_in": int(body.get("expires_in", 21600)),
            }
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return {"ok": False, "status": e.code, "error": err_body or str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def keepalive_one(account: dict, proxy: str | None = None) -> bool:
    """发一个轻量 grok-4.5 请求保活。成功返回 True。"""
    opener = _build_opener(proxy)
    url = f"{BASE_URL}/responses"
    payload = json.dumps({
        "model": "grok-4.5",
        "stream": False,
        "input": "Reply with exactly KEEPALIVE",
        "reasoning": {"effort": "low"},
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {account['token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        **DEFAULT_HEADERS,
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=KEEPALIVE_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status == 200 and bool(body)
    except Exception:
        return False


def move_to_dead(account: dict) -> None:
    """把连续失败的号移到 cpa_auths_dead/。"""
    CPA_DEAD_DIR.mkdir(parents=True, exist_ok=True)
    src = account["file"]
    dst = CPA_DEAD_DIR / src.name
    try:
        shutil.move(str(src), str(dst))
    except Exception:
        pass


def process_one(account: dict, proxy: str | None, dry_run: bool) -> str:
    """处理一个号：续期 → 保活。返回状态字符串。"""
    now = _now()
    needs_refresh = (account["expired"] - now).total_seconds() < REFRESH_MARGIN_MINUTES * 60

    # Step 1: 续期
    if needs_refresh:
        if dry_run:
            return "SKIP_DRY"
        r = refresh_one(account, proxy)
        if r.get("ok"):
            new_token = r["access_token"]
            new_refresh = r["refresh_token"]
            account["token"] = new_token
            account["data"]["access_token"] = new_token
            account["data"]["refresh_token"] = new_refresh
            if r.get("id_token"):
                account["data"]["id_token"] = r["id_token"]
            new_expired = now + timedelta(seconds=r.get("expires_in", 21600))
            account["data"]["expired"] = new_expired.strftime("%Y-%m-%dT%H:%M:%SZ")
            account["data"]["last_refresh"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            account["expired"] = new_expired
        else:
            # refresh 失败 → 号已死
            account["fail_streak"] += 1
            account["data"]["_keepalive_fail_streak"] = account["fail_streak"]
            if account["fail_streak"] >= MAX_FAIL_STREAK:
                move_to_dead(account)
                return "DEAD_REFRESH"
            if not dry_run:
                account["file"].write_text(
                    json.dumps(account["data"], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            return f"REFRESH_FAIL({r.get('status', '')})"

    # Step 2: 保活
    if dry_run:
        return "SKIP_DRY"

    ok = keepalive_one(account, proxy)
    if ok:
        account["fail_streak"] = 0
        account["data"]["_keepalive_fail_streak"] = 0
        account["data"]["_last_keepalive"] = now.isoformat()
        account["file"].write_text(
            json.dumps(account["data"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return "OK"
    else:
        account["fail_streak"] += 1
        account["data"]["_keepalive_fail_streak"] = account["fail_streak"]
        account["file"].write_text(
            json.dumps(account["data"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if account["fail_streak"] >= MAX_FAIL_STREAK:
            move_to_dead(account)
            return "DEAD_CHAT"
        return f"CHAT_FAIL(streak={account['fail_streak']})"


def run_round(max_count: int, proxy: str | None, dry_run: bool) -> dict:
    """跑一轮保活。"""
    accounts = load_accounts(max_count=max_count)
    if not accounts:
        print("[keepalive] no accounts found")
        return {"total": 0, "ok": 0, "fail": 0, "dead": 0}

    stats = {"total": len(accounts), "ok": 0, "fail": 0, "dead": 0, "refresh_fail": 0}
    for i, acc in enumerate(accounts):
        status = process_one(acc, proxy, dry_run)
        label = acc.get("email") or acc.get("sub") or acc["file"].stem[:30]
        print(f"  [{i+1}/{len(accounts)}] {label[:40]:40s} {status}")

        if status == "OK":
            stats["ok"] += 1
        elif status.startswith("DEAD"):
            stats["dead"] += 1
        elif "REFRESH_FAIL" in status:
            stats["refresh_fail"] += 1
        elif "FAIL" in status:
            stats["fail"] += 1

        if i < len(accounts) - 1:
            time.sleep(INTER_ACCOUNT_DELAY)

    print(f"\n[keepalive] round done: ok={stats['ok']} fail={stats['fail']} "
          f"refresh_fail={stats['refresh_fail']} dead={stats['dead']} total={stats['total']}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="CPA pool keepalive + dead cleanup")
    parser.add_argument("--max", type=int, default=100, help="每轮最多处理号数 (default: 100)")
    parser.add_argument("--proxy", type=str, default=None, help="HTTP 代理 (e.g. http://127.0.0.1:7897)")
    parser.add_argument("--interval", type=str, default=None,
                        help="循环模式间隔 (e.g. 3h, 30m)；不设则跑一轮退出")
    parser.add_argument("--dry-run", action="store_true", help="只探测不写文件、不发请求")
    args = parser.parse_args()

    if args.interval:
        unit = args.interval[-1].lower()
        try:
            val = int(args.interval[:-1])
        except ValueError:
            print(f"Invalid interval: {args.interval}")
            sys.exit(1)
        seconds = val * {"h": 3600, "m": 60, "s": 1}.get(unit, 3600)
        print(f"[keepalive] loop mode: interval={seconds}s max={args.max} proxy={args.proxy or '(none)'}")
        while True:
            try:
                run_round(args.max, args.proxy, args.dry_run)
            except Exception as e:
                print(f"[keepalive] round error: {e}")
            print(f"[keepalive] sleeping {seconds}s...")
            time.sleep(seconds)
    else:
        run_round(args.max, args.proxy, args.dry_run)


if __name__ == "__main__":
    main()
