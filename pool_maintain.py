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
    parser = argparse.ArgumentParser(description="号池维持：refresh + health + 条件补号 + CLI 同步")
    parser.add_argument("--force-refill", type=int, default=0, help="强制补 N 个，忽略健康阈值")
    parser.add_argument("--skip-refill", action="store_true", help="只做健康检查不同步补号")
    parser.add_argument("--skip-health", action="store_true", help="跳过健康检查直接补号")
    parser.add_argument("--skip-refresh", action="store_true", help="跳过 access_token 批量刷新")
    args = parser.parse_args()

    cfg = load_cfg()
    min_live = int(cfg.get("pool_min_live", 5) or 5)
    refill_count = int(cfg.get("pool_refill_count", cfg.get("register_count", 8)) or 8)
    concurrency = int(cfg.get("pool_refill_concurrency", cfg.get("concurrency", 1)) or 1)
    refresh_within = float(cfg.get("pool_maintain_refresh_within_hours", 2) or 2)
    refresh_max = int(cfg.get("pool_maintain_refresh_max", 300) or 300)
    refresh_workers = int(cfg.get("pool_maintain_refresh_workers", 3) or 3)
    purge_dead = bool(cfg.get("pool_maintain_purge_dead", True))
    py = sys.executable

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"maintain_{ts}.log"
    print(f"[*] log -> {log_path}")

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"=== pool_maintain {datetime.now(timezone.utc).isoformat()} ===\n")
        need_refill = False
        live_count = None

        # 1) bulk-refresh expiring tokens + optional purge of revoked RT
        if not args.skip_refresh and (ROOT / "refresh_pool.py").is_file():
            refresh_cmd = [
                py,
                str(ROOT / "refresh_pool.py"),
                "--within-hours",
                str(refresh_within),
                "--max",
                str(max(0, refresh_max)),
                "--workers",
                str(max(1, refresh_workers)),
            ]
            if purge_dead:
                refresh_cmd.append("--purge-dead")
            print(
                f"[*] refresh within={refresh_within}h max={refresh_max} "
                f"purge_dead={purge_dead}"
            )
            code_r = run(refresh_cmd, log)
            print(f"[*] refresh exit={code_r}")

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
            # 注意：grok_register_ttk start 只读 config.register_count（通常为 1）。
            # 以前 refill_count 只打印不用，导致 --force-refill 8 也只跑 1 次。
            # 这里按 refill_count 连续调用 start；单次失败再重试 1 次。
            ok_n = 0
            fail_n = 0
            for i in range(1, max(1, refill_count) + 1):
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
                    print(f"[*] refill {i}/{refill_count} attempt={attempt} exit={code}")
                    if code == 0:
                        ok_n += 1
                        break
                else:
                    fail_n += 1
            print(f"[*] refill summary ok={ok_n} fail={fail_n}")
            # 补完再健康同步一次
            if not args.skip_health:
                code2 = run([py, str(ROOT / "pool_health.py")], log)
                print(f"[*] re-health exit={code2}")
        else:
            print(f"[*] pool healthy (min_live={min_live}), skip refill")

        # 自动挂接 CLI auth 目录
        run([py, str(ROOT / "auto_link_cli.py")], log)

        # buffer_first：新注册的自有号也 soft-hold，继续先烧缓冲
        try:
            from pool_policy import hold_own_for_buffer, prefer_mode

            if prefer_mode(cfg) == "buffer_first":
                ad = ROOT / str(cfg.get("cpa_auth_dir") or "cpa_auths")
                if not ad.is_absolute():
                    ad = (ROOT / ad).resolve()
                st_hold = hold_own_for_buffer(ad, cfg)
                print(f"[*] prefer=buffer_first re-hold own: {st_hold}")
                log.write(f"re-hold own: {st_hold}\n")
        except Exception as exc:
            print(f"[!] re-hold own failed: {exc}")

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
