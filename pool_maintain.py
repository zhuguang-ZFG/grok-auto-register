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
import os
import subprocess
import sys
import time
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


class RefillGate:
    """Rate-limit auto-refill spawns to avoid runaway registration.

    Uses a small state file under logs/ to persist last spawn time and daily
    count across maintain cycles. The gate limits *spawn attempts*, not
    successful registrations; the registration script has its own daily cap.
    """

    STATE_FILE = LOG_DIR / "_pool_refill_state.json"

    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("pool_auto_refill", True))
        # Explicit 0 means "no limit" rather than falling back to default.
        self.daily_max = (
            int(cfg["pool_refill_daily_max"])
            if "pool_refill_daily_max" in cfg
            else 5
        )
        self.cooldown = (
            float(cfg["pool_refill_cooldown_sec"])
            if "pool_refill_cooldown_sec" in cfg
            else 1800.0
        )

    def _load(self) -> dict:
        if not self.STATE_FILE.is_file():
            return {}
        try:
            return json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def check(self, *, force: bool = False, no_auto: bool = False) -> tuple[bool, str]:
        if no_auto:
            return False, "--no-auto-refill"
        # Manual force overrides config-disable (but not explicit --no-auto-refill).
        if force:
            return True, "force-refill"
        if not self.enabled:
            return False, "pool_auto_refill=false"
        state = self._load()
        today = self._today()
        last_date = state.get("last_refill_date")
        count = int(state.get("refills_today", 0) or 0) if last_date == today else 0
        if self.daily_max > 0 and count >= self.daily_max:
            return False, f"daily_max {count}/{self.daily_max}"
        last_ts = float(state.get("last_refill_ts", 0) or 0)
        elapsed = time.time() - last_ts
        if self.cooldown > 0 and last_ts and elapsed < self.cooldown:
            return False, f"cooldown {int(elapsed)}s < {int(self.cooldown)}s"
        return True, "ok"

    def record(self) -> None:
        state = self._load()
        today = self._today()
        if state.get("last_refill_date") != today:
            state["refills_today"] = 0
        state["last_refill_date"] = today
        state["last_refill_ts"] = time.time()
        state["refills_today"] = int(state.get("refills_today", 0) or 0) + 1
        try:
            tmp = self.STATE_FILE.with_suffix(self.STATE_FILE.suffix + ".tmp")
            tmp.write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, self.STATE_FILE)
        except Exception as exc:
            print(f"[!] refill state write failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="号池维持：refresh + health + 条件补号 + CLI 同步")
    parser.add_argument("--force-refill", type=int, default=0, help="强制补号：忽略 gate 连续跑 N 轮注册")
    parser.add_argument("--skip-refill", action="store_true", help="只做健康检查不同步补号")
    parser.add_argument("--skip-health", action="store_true", help="跳过健康检查直接补号")
    parser.add_argument("--skip-refresh", action="store_true", help="跳过 access_token 批量刷新")
    parser.add_argument(
        "--no-auto-refill",
        action="store_true",
        help="即使号池不足也禁止自动 spawn 注册机（refresh/health 仍继续）",
    )
    args = parser.parse_args()

    cfg = load_cfg()
    min_live = int(cfg.get("pool_min_live", 5) or 5)
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

        # 0) proxy/Clash health (rotate if accounts.x.ai path bad)
        if (ROOT / "proxy_health.py").is_file():
            print("[*] proxy_health --rotate-if-bad")
            code_p = run(
                [py, str(ROOT / "proxy_health.py"), "--rotate-if-bad"],
                log,
            )
            print(f"[*] proxy_health exit={code_p}")

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

        # 1b) hard purge (throttled): default scope=buffer, every N hours
        # soft refresh_pool only touches expiring JWTs; hard_purge catches
        # revoked RT while access JWT still unexpired (shared packs).
        if purge_dead and (ROOT / "scripts" / "hard_purge_pool.py").is_file():
            # hours between runs (default 6). 0 = every maintain cycle. -1/false skip.
            hard_hours = cfg.get("pool_maintain_hard_purge_every_hours", 6)
            try:
                hard_hours = float(hard_hours)
            except Exception:
                hard_hours = 6.0
            # legacy key: every N maintain cycles — if set to 0, skip
            hard_every_legacy = cfg.get("pool_maintain_hard_purge_every", None)
            if hard_every_legacy is not None and int(hard_every_legacy or 0) == 0:
                hard_hours = -1.0
            run_hard = False
            if hard_hours < 0:
                print("[*] hard_purge skipped (disabled in config)")
            elif hard_hours == 0:
                run_hard = True
            else:
                stamp = LOG_DIR / "_hard_purge_last.json"
                last_ts = 0.0
                if stamp.is_file():
                    try:
                        last_ts = float(json.loads(stamp.read_text(encoding="utf-8")).get("ts") or 0)
                    except Exception:
                        last_ts = 0.0
                age_h = (time.time() - last_ts) / 3600.0 if last_ts else 1e9
                if age_h >= hard_hours:
                    run_hard = True
                    print(f"[*] hard_purge due (age={age_h:.1f}h >= {hard_hours}h)")
                else:
                    print(f"[*] hard_purge skip (age={age_h:.1f}h < {hard_hours}h)")
            if run_hard:
                scope = str(cfg.get("pool_hard_purge_scope") or "buffer")
                hard_cmd = [
                    py,
                    str(ROOT / "scripts" / "hard_purge_pool.py"),
                    "--scope",
                    scope,
                ]
                max_n = int(cfg.get("pool_hard_purge_max") or 0)
                if max_n > 0:
                    hard_cmd.extend(["--max", str(max_n)])
                print(f"[*] hard_purge_pool scope={scope}")
                code_h = run(hard_cmd, log)
                print(f"[*] hard_purge exit={code_h}")


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

        if args.skip_refill:
            need_refill = False

        gate = RefillGate(cfg)
        allowed, reason = gate.check(
            force=args.force_refill > 0, no_auto=args.no_auto_refill
        )
        if need_refill and not allowed:
            print(
                f"[*] refill requested but gated ({reason}); skip spawn grok_register_ttk.py"
            )
            log.write(f"refill gated ({reason}); skip spawn register\n")
            need_refill = False

        if need_refill:
            rounds = max(1, int(args.force_refill)) if args.force_refill > 0 else 1
            print(
                f"[*] refill allowed ({reason}); spawn {rounds} round(s) "
                f"grok_register_ttk.py start"
            )
            log.write(
                f"refill allowed; spawn {rounds} round(s) grok_register_ttk.py start\n"
            )
            for i in range(1, rounds + 1):
                code = 1
                for attempt in range(1, 3):
                    code = run(
                        [py, str(ROOT / "grok_register_ttk.py"), "start"],
                        log,
                    )
                    print(f"[*] refill round={i}/{rounds} attempt={attempt} exit={code}")
                    if code == 0:
                        break
                # Record every spawn attempt so failed rounds also consume budget
                # and prevent tight retry loops.
                gate.record()
            print(f"[*] refill rounds done")
            # 补完再健康同步一次
            if not args.skip_health:
                code2 = run([py, str(ROOT / "pool_health.py")], log)
                print(f"[*] re-health exit={code2}")
        else:
            print(f"[*] pool healthy (min_live={min_live}), skip refill")

        # 自动挂接 CLI auth 目录
        run([py, str(ROOT / "auto_link_cli.py")], log)

        # prefer policy: buffer ammo → own base (community tiered pool)
        # 1) if buffer_first and buffer thin → auto release own (failover)
        # 2) if still buffer_first → re-hold newly minted own accounts
        try:
            from pool_policy import (
                ensure_buffer_failover,
                hold_own_for_buffer,
                prefer_mode,
            )

            ad = ROOT / str(cfg.get("cpa_auth_dir") or "cpa_auths")
            if not ad.is_absolute():
                ad = (ROOT / ad).resolve()

            def _plog(msg: str) -> None:
                print(msg)
                log.write(msg + "\n")

            fo = ensure_buffer_failover(
                ad, cfg, config_path=CONFIG, log=_plog
            )
            print(
                f"[*] buffer_failover action={fo.get('action')} "
                f"buffer_live={fo.get('buffer_live')} "
                f"mode={fo.get('mode_after')}"
            )
            log.write(f"buffer_failover: {fo}\n")

            # reload prefer after possible config write
            cfg = load_cfg() or cfg
            if prefer_mode(cfg) == "buffer_first":
                st_hold = hold_own_for_buffer(ad, cfg)
                print(f"[*] prefer=buffer_first re-hold own: {st_hold}")
                log.write(f"re-hold own: {st_hold}\n")
        except Exception as exc:
            print(f"[!] prefer/buffer failover failed: {exc}")

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
