#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""号池维持主入口：健康检查 → 不足则补号 → 再健康同步 CLI。

给计划任务调用：
  python pool_maintain.py
  python pool_maintain.py --force-refill 8
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
LOG_DIR = ROOT / "logs"


def load_cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run(cmd: list[str], log_fp) -> int:
    log_fp.write(f"\n$ {' '.join(cmd)}\n")
    log_fp.flush()
    p = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return int(p.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="号池维持：health + 条件补号 + CLI 同步")
    parser.add_argument("--force-refill", type=int, default=0, help="强制补 N 个，忽略健康阈值")
    parser.add_argument("--skip-refill", action="store_true", help="只做健康检查不同步补号")
    parser.add_argument("--skip-health", action="store_true", help="跳过健康检查直接补号")
    args = parser.parse_args()

    cfg = load_cfg()
    min_live = int(cfg.get("pool_min_live", 5) or 5)
    refill_count = int(cfg.get("pool_refill_count", cfg.get("register_count", 8)) or 8)
    concurrency = int(cfg.get("pool_refill_concurrency", cfg.get("concurrency", 1)) or 1)
    py = sys.executable

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"maintain_{ts}.log"
    print(f"[*] log -> {log_path}")

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"=== pool_maintain {datetime.now(timezone.utc).isoformat()} ===\n")
        need_refill = False
        live_count = None

        if not args.skip_health:
            code = run([py, str(ROOT / "pool_health.py")], log)
            report = ROOT / str(cfg.get("cpa_auth_dir") or "cpa_auths") / "pool_health_report.json"
            if not report.is_absolute():
                report = ROOT / report
            if report.is_file():
                try:
                    data = json.loads(report.read_text(encoding="utf-8"))
                    live_count = int(data.get("live_count") or 0)
                    need_refill = bool(data.get("need_refill"))
                except Exception:
                    need_refill = code == 2
            else:
                need_refill = code == 2
            print(f"[*] health exit={code} live={live_count} need_refill={need_refill}")
        else:
            need_refill = True

        if args.force_refill > 0:
            need_refill = True
            refill_count = args.force_refill

        if args.skip_refill:
            need_refill = False

        if need_refill:
            print(f"[*] refill start count={refill_count} concurrency={concurrency}")
            # 无人值守：补号失败最多再试 1 次（网络抖动）
            code = 1
            for attempt in range(1, 3):
                code = run(
                    [
                        py,
                        str(ROOT / "grok_register_ttk.py"),
                        "start",
                    ],
                    log,
                )
                print(f"[*] refill attempt={attempt} exit={code}")
                if code == 0:
                    break
            # 补完再健康同步一次
            if not args.skip_health:
                code2 = run([py, str(ROOT / "pool_health.py")], log)
                print(f"[*] re-health exit={code2}")
        else:
            print(f"[*] pool healthy (min_live={min_live}), skip refill")

        # 自动挂接 CLI auth 目录
        run([py, str(ROOT / "auto_link_cli.py")], log)

        # 状态快照
        run([py, str(ROOT / "pool_status.py")], log)

        # 日志轮转：只留最近 30 个 maintain_*.log
        try:
            logs = sorted(LOG_DIR.glob("maintain_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in logs[30:]:
                old.unlink(missing_ok=True)
        except Exception:
            pass

    print("[*] maintain done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
