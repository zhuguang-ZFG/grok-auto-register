#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量探测 cpa_auths 中的账号，将 429/401 死号标记为 disabled。

社区经验 + HARDEN 2026-07-18：
- free 账号 ~24-48h 寿命上限，429 exhausted 不会在短期内恢复
- quota_watch 只在 grok CLI 实际用号时被动发现死号 → 池里大量隐藏死号
- 本脚本主动探测全池，把死号一次性标记为 disabled，让 CLIProxy/CLI 只轮询活号

用法:
    python batch_probe_accounts.py                  # 探测全池，标记死号 disabled
    python batch_probe_accounts.py --workers 8      # 并发探测
    python batch_probe_accounts.py --max 100        # 只探测前 100 个
    python batch_probe_accounts.py --dry-run        # 只探测，不写文件
    python batch_probe_accounts.py --sample 50      # 随机抽样 50 个
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CPA_AUTH_DIR = ROOT / "cpa_auths"
BASE_URL = "https://cli-chat-proxy.grok.com/v1"
TIMEOUT = 8
PROBE_PAYLOAD = {
    "model": "grok-4.5",
    "messages": [{"role": "user", "content": "test"}],
    "max_tokens": 1,
    "stream": False,
}


def log(msg: str) -> None:
    print(msg, flush=True)


def load_account(cpa_file: Path) -> dict[str, Any] | None:
    try:
        return json.loads(cpa_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def probe_account(cpa_file: Path) -> tuple[str, str]:
    """探测单个账号。返回 (status, email)。

    status: ok | 429 | 401 | other | skip | error
    """
    import urllib.error
    import urllib.request

    data = load_account(cpa_file)
    if data is None:
        return "error", "bad_json"
    if data.get("disabled"):
        return "skip", data.get("email", "")

    token = str(data.get("access_token") or "").strip()
    if not token:
        return "error", data.get("email", "no_token")

    base = str(data.get("base_url") or BASE_URL).rstrip("/")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if isinstance(data.get("headers"), dict):
        headers.update({str(k): str(v) for k, v in data["headers"].items()})

    proxy = os.environ.get("CPA_PROXY") or None
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()

    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(PROBE_PAYLOAD).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            code = int(getattr(resp, "status", 200) or 200)
            if 200 <= code < 300:
                return "ok", data.get("email", "")
            return "other", data.get("email", "")
    except urllib.error.HTTPError as e:
        code = int(e.code or 0)
        if code == 429:
            return "429", data.get("email", "")
        if code == 401:
            return "401", data.get("email", "")
        if code == 403:
            return "other", data.get("email", "")
        return "other", data.get("email", "")
    except Exception as e:
        return "error", str(e)[:50]


def disable_account(cpa_file: Path, status: str) -> bool:
    """原子标记账号为 disabled（带 quota_state，CLIProxy/CLI 跳过）。"""
    data = load_account(cpa_file)
    if data is None:
        return False
    if data.get("disabled"):
        return False
    data["disabled"] = True
    qs = data.get("quota_state") or {}
    qs["reason"] = "free-usage-exhausted" if status == "429" else status
    qs["exhausted_at"] = time.time()
    qs["recover_after"] = time.time() + 6 * 3600  # 6h 滚动窗口
    qs["proxy_disabled"] = True
    data["quota_state"] = qs
    tmp = cpa_file.with_suffix(cpa_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, cpa_file)
    return True


def main(argv: list[str] | None = None) -> int:
    global TIMEOUT

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=6, help="并发线程数 (default: 6)")
    ap.add_argument("--max", type=int, default=0, help="最多探测 N 个 (0=all)")
    ap.add_argument("--sample", type=int, default=0, help="随机抽样 N 个 (0=不抽样)")
    ap.add_argument("--dry-run", action="store_true", help="只探测不写文件")
    ap.add_argument("--timeout", type=float, default=TIMEOUT, help=f"单号超时秒 (default: {TIMEOUT})")
    args = ap.parse_args(argv)

    TIMEOUT = args.timeout

    files = sorted(CPA_AUTH_DIR.glob("xai-*.json"))
    if args.sample > 0 and args.sample < len(files):
        import random
        random.seed(42)
        files = random.sample(files, args.sample)
    if args.max > 0:
        files = files[: args.max]

    total = len(files)
    log(f"待探测账号: {total}  workers={args.workers}  dry_run={args.dry_run}\n")

    stats: dict[str, int] = {"ok": 0, "429": 0, "401": 0, "other": 0, "error": 0, "skip": 0}
    to_disable: list[tuple[Path, str]] = []
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        fut_to_file = {pool.submit(probe_account, f): f for f in files}
        for fut in as_completed(fut_to_file):
            cpa_file = fut_to_file[fut]
            try:
                status, email = fut.result()
            except Exception as e:  # noqa: BLE001
                status, email = "error", str(e)[:50]
            stats[status] = stats.get(status, 0) + 1
            done += 1
            if status in ("429", "401", "other"):
                to_disable.append((cpa_file, status))
            if done % 50 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed else 0
                log(
                    f"进度: {done}/{total} | OK={stats['ok']} 429={stats['429']} "
                    f"401={stats['401']} 其他={stats['other']} 错误={stats['error']} "
                    f"跳过={stats['skip']} | {rate:.1f}/s"
                )

    log(f"\n=== 探测完成 ({time.time()-t0:.0f}s) ===")
    log(f"可用:   {stats['ok']} ({stats['ok']*100//max(total,1)}%)")
    log(f"429:    {stats['429']} ({stats['429']*100//max(total,1)}%)")
    log(f"401:    {stats['401']}")
    log(f"其他:   {stats['other']}")
    log(f"错误:   {stats['error']}")
    log(f"已跳过: {stats['skip']} (之前已 disabled)")
    log(f"\n待禁用: {len(to_disable)}")

    disabled_n = 0
    if to_disable and not args.dry_run:
        log("标记死号为 disabled...")
        for cpa_file, status in to_disable:
            if disable_account(cpa_file, status):
                disabled_n += 1
        log(f"已标记 {disabled_n} 个死号")

    live_now = sum(1 for f in CPA_AUTH_DIR.glob("xai-*.json")
                   if not (load_account(f) or {}).get("disabled"))
    log(f"\n>>> 当前可用账号: {live_now}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
