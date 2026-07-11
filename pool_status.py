#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""号池本地概况：accounts_*.txt + cpa_auths 统计。"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    cfg_path = ROOT / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[!] 读 config 失败: {exc}")

    print(f"[*] 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[*] 目录: {ROOT}")
    print(
        f"[*] 配置: count={cfg.get('register_count')} concurrency={cfg.get('concurrency')} "
        f"domains={cfg.get('defaultDomains')!r}"
    )

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
    print(f"[*] accounts 文件: {len(acc_files)} 个 | 账号行合计: {total_lines}")
    if domain_counter:
        top = ", ".join(f"{d}={n}" for d, n in domain_counter.most_common(8))
        print(f"[*] 账号域名分布: {top}")
    if acc_files:
        latest = acc_files[-1]
        print(f"[*] 最新 accounts: {latest.name} ({datetime.fromtimestamp(latest.stat().st_mtime)})")

    # cpa
    cpa_dir = ROOT / str(cfg.get("cpa_auth_dir") or "./cpa_auths")
    if not cpa_dir.is_absolute():
        cpa_dir = (ROOT / cpa_dir).resolve()
    auths = sorted(cpa_dir.glob("xai-*.json")) if cpa_dir.is_dir() else []
    alive = expired = unknown = 0
    now = datetime.now(timezone.utc)
    for p in auths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
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
    print(f"[*] CPA auth: {len(auths)} 个 | access 未过期(粗判)≈{alive} | 已过期≈{expired} | 未知={unknown}")
    pending = cpa_dir / "cpa_push_pending.txt"
    if pending.is_file():
        n = sum(1 for ln in pending.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip())
        print(f"[*] 待重推: {n} （python grok_register_ttk.py --retry-push）")

    print("[*] 维持建议: 域名先接入 mail 后端；单批 6~12；并发 1；计划任务每 2~4 小时 run_pool.bat")


if __name__ == "__main__":
    main()
