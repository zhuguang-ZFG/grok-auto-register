#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K12 auto-refill from slim backup / external dump — keep live pool small.

Shared K12 burns tokens fast (one workspace, many snapshot ATs). After slim
(~1.5k live), dead/disabled accounts shrink the ready set. This script:

  1. Reads live pool size (gateway API preferred, SQLite fallback).
  2. If ready/total below --min-ready / --target, samples NEVER-USED candidates
     from a source DB (default: largest pre_slim backup under backups/k12_db).
  3. POST /api/accounts in small batches — NEVER re-import the full 80k.

Hard caps (16GB host):
  - --target default 1800, --hard-cap 2500 (refuse to grow beyond).
  - only refill when live total < target; never "top up" by reloading 80k.

Examples:
  python scripts/k12_pool_refill.py status
  python scripts/k12_pool_refill.py refill --dry-run
  python scripts/k12_pool_refill.py refill --min-ready 800 --target 1800
  python scripts/k12_pool_refill.py refill --source backups/k12_db/accounts.db.pre_slim_...
  python scripts/k12_pool_refill.py watch --interval 900
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "chatgpt2api" / "data" / "accounts.db"
BACKUP_DIR = ROOT / "backups" / "k12_db"
GATEWAY = os.environ.get("K12_GATEWAY", "http://127.0.0.1:8124")
AUTH_KEY = os.environ.get("CHATGPT2API_AUTH_KEY", "k12-pool-local")
LOG = ROOT / "logs" / "k12_pool_refill.log"
LOCK = ROOT / "logs" / "k12_pool_refill.watch.lock"
BATCH = 100

# Shared snapshots whose workspace is known-dead (deactivated_workspace).
# Never refill these account_id prefixes from pre_slim backups.
DEAD_WORKSPACE_PREFIXES: tuple[str, ...] = (
    "fc4f8db5-72cd-44cb-ae0d-fef1370a16c8",
    "fc4f8db5",
)


def is_dead_workspace(acc: dict[str, Any]) -> bool:
    """True if account belongs to a blacklisted dead K12 workspace."""
    for key in ("account_id", "chatgpt_account_id", "workspace_id"):
        val = str(acc.get(key) or "").strip().lower()
        if not val:
            continue
        for pref in DEAD_WORKSPACE_PREFIXES:
            if val == pref.lower() or val.startswith(pref.lower()):
                return True
    return False


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def http_json(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 120.0,
) -> tuple[int, Any]:
    data = None
    headers = {"Authorization": f"Bearer {AUTH_KEY}"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{GATEWAY.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, 0, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


def acquire_watch_lock() -> bool:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            old = int((LOCK.read_text(encoding="utf-8") or "0").strip().split()[0])
        except Exception:
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            log(f"watch already running pid={old}; exit")
            return False
    LOCK.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return True


def release_watch_lock() -> None:
    try:
        if LOCK.exists():
            cur = int((LOCK.read_text(encoding="utf-8") or "0").strip().split()[0])
            if cur == os.getpid():
                LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def parse_acc(data: str | dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(data, dict):
        return data
    try:
        o = json.loads(data)
    except Exception:
        return None
    return o if isinstance(o, dict) else None


def is_used(acc: dict[str, Any]) -> bool:
    if acc.get("last_used_at"):
        return True
    try:
        if int(acc.get("text_success") or 0) > 0:
            return True
    except Exception:
        pass
    try:
        if int(acc.get("success") or 0) > 0:
            return True
    except Exception:
        pass
    return False


def is_normal_status(status: Any) -> bool:
    s = str(status or "").strip().lower()
    if not s:
        return True
    # chinese gateway statuses
    if s in {"正常", "normal", "ok", "ready", "active", "enabled"}:
        return True
    if s in {"禁用", "disabled", "abnormal", "异常", "limited", "限流"}:
        return False
    # unknown -> treat as ready (shared K12 often only uses 正常)
    return s not in {"dead", "banned", "invalid"}


def gateway_counts() -> dict[str, int]:
    out = {"total": 0, "normal": 0, "limited": 0, "abnormal": 0, "disabled": 0, "source": "api"}
    code, body = http_json("GET", "/api/accounts?page=1&page_size=1", timeout=20)
    if code != 200 or not isinstance(body, dict):
        out["source"] = "api-fail"
        return out
    out["total"] = int(body.get("total") or 0)
    for st, key in (("normal", "normal"), ("limited", "limited"), ("abnormal", "abnormal"), ("disabled", "disabled")):
        c, b = http_json("GET", f"/api/accounts?page=1&page_size=1&status={st}", timeout=20)
        if c == 200 and isinstance(b, dict):
            out[key] = int(b.get("total") or 0)
    # chinese status labels used by chatgpt2api UI
    for st, key in (("正常", "normal"), ("限流", "limited"), ("异常", "abnormal"), ("禁用", "disabled")):
        if out[key]:
            continue
        c, b = http_json("GET", f"/api/accounts?page=1&page_size=1&status={st}", timeout=20)
        if c == 200 and isinstance(b, dict):
            out[key] = int(b.get("total") or 0)
    return out


def sqlite_counts(db_path: Path) -> dict[str, int]:
    out = {"total": 0, "normal": 0, "limited": 0, "abnormal": 0, "disabled": 0, "source": "sqlite"}
    if not db_path.is_file():
        return out
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT data FROM accounts").fetchall()
    finally:
        conn.close()
    out["total"] = len(rows)
    for (data,) in rows:
        acc = parse_acc(data)
        if not acc:
            continue
        st = str(acc.get("status") or "")
        if st in ("正常", "normal") or is_normal_status(st):
            out["normal"] += 1
        elif st in ("禁用", "disabled"):
            out["disabled"] += 1
        elif st in ("异常", "abnormal"):
            out["abnormal"] += 1
        elif st in ("限流", "limited"):
            out["limited"] += 1
        else:
            out["normal"] += 1
    return out


def live_counts() -> dict[str, int]:
    c = gateway_counts()
    if c.get("total", 0) > 0 or c.get("source") == "api":
        if c.get("total", 0) > 0:
            return c
    return sqlite_counts(DB_PATH)


def live_token_set(db_path: Path) -> set[str]:
    if not db_path.is_file():
        return set()
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT access_token FROM accounts").fetchall()
    finally:
        conn.close()
    return {str(r[0]).strip() for r in rows if r and r[0]}


def find_default_source() -> Path | None:
    """Prefer largest pre_slim backup; else newest accounts_*.db backup."""
    if not BACKUP_DIR.is_dir():
        return None
    candidates = list(BACKUP_DIR.glob("accounts.db.pre_slim_*")) + list(
        BACKUP_DIR.glob("accounts.db.pre_slim*")
    )
    # also accept online-backup style names
    candidates += list(BACKUP_DIR.glob("accounts_*.db"))
    candidates = [p for p in candidates if p.is_file() and p.stat().st_size > 1_000_000]
    if not candidates:
        return None
    # prefer files that look like full dumps (largest)
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0]


def sample_candidates(
    source_db: Path,
    *,
    live_tokens: set[str],
    need: int,
    only_unused: bool = True,
    only_k12: bool = True,
    prefer_newest: bool = True,
    scan_limit: int = 200_000,
) -> list[dict[str, Any]]:
    """Sample account dicts from source not already in live pool."""
    if need <= 0:
        return []
    conn = sqlite3.connect(str(source_db))
    try:
        # stream; avoid loading 80k full JSON twice
        cur = conn.execute("SELECT access_token, data FROM accounts")
        pool: list[tuple[str, dict[str, Any]]] = []
        scanned = 0
        while True:
            batch = cur.fetchmany(2000)
            if not batch:
                break
            for tok, data in batch:
                scanned += 1
                if scanned > scan_limit:
                    break
                token = str(tok or "").strip()
                if not token or token in live_tokens:
                    continue
                acc = parse_acc(data)
                if not acc:
                    continue
                if only_k12:
                    plan = str(acc.get("plan_type") or acc.get("type") or "").lower()
                    if plan and plan not in {"k12", "education", "edu"}:
                        continue
                if only_unused and is_used(acc):
                    # used accounts in backup may already be burned; still allow if pool empty
                    continue
                if is_dead_workspace(acc):
                    continue
                if not is_normal_status(acc.get("status")):
                    continue
                # normalize status for re-import
                acc = dict(acc)
                acc["access_token"] = token
                acc["status"] = "正常"
                # reset runtime counters so new life in slim pool
                acc["text_fail"] = 0
                acc["fail"] = 0
                acc["invalid_count"] = 0
                pool.append((str(acc.get("created_at") or ""), acc))
            if scanned > scan_limit:
                break
    finally:
        conn.close()

    if prefer_newest:
        pool.sort(key=lambda x: x[0], reverse=True)
    else:
        random.shuffle(pool)

    # if unused pool too small, fall back to any non-live k12
    if len(pool) < need and only_unused:
        log(f"unused candidates only {len(pool)}; relaxing only_unused")
        return sample_candidates(
            source_db,
            live_tokens=live_tokens,
            need=need,
            only_unused=False,
            only_k12=only_k12,
            prefer_newest=prefer_newest,
            scan_limit=scan_limit,
        )

    out = [acc for _, acc in pool[:need]]
    log(f"source scan scanned~={scanned} candidates={len(pool)} pick={len(out)}")
    return out


def import_gateway(records: list[dict[str, Any]]) -> dict[str, int]:
    added = skipped = errors = 0
    for i in range(0, len(records), BATCH):
        batch = records[i : i + BATCH]
        # strip huge unused fields? keep as-is for gateway compatibility
        code, body = http_json(
            "POST",
            "/api/accounts",
            {"accounts": batch, "refresh": False, "return_items": False},
            timeout=180,
        )
        if code != 200 or not isinstance(body, dict):
            errors += 1
            log(f"  batch {i // BATCH + 1} ERROR {code} {str(body)[:140]}")
            continue
        a = int(body.get("added") or 0)
        s = int(body.get("skipped") or 0)
        added += a
        skipped += s
        log(f"  batch {i // BATCH + 1}: +{a} skip={s}")
        time.sleep(0.15)
    return {"added": added, "skipped": skipped, "errors": errors}


def chat_probe(model: str = "gpt-5-mini") -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "1"}],
        "stream": False,
    }
    t0 = time.time()
    code, body = http_json("POST", "/v1/chat/completions", payload, timeout=90)
    elapsed = round(time.time() - t0, 1)
    if code == 200 and isinstance(body, dict) and body.get("choices"):
        return {"ok": True, "latency_s": elapsed}
    return {"ok": False, "latency_s": elapsed, "http": code, "error": str(body)[:160]}


def cmd_status(args: argparse.Namespace) -> int:
    c = live_counts()
    ready = int(c.get("normal") or 0)
    total = int(c.get("total") or 0)
    disabled = int(c.get("disabled") or 0)
    log(
        f"pool source={c.get('source')} total={total} normal/ready={ready} "
        f"limited={c.get('limited')} abnormal={c.get('abnormal')} disabled={disabled}"
    )
    src = find_default_source()
    if src:
        log(f"default refill source: {src.name} ({src.stat().st_size / 1e6:.1f} MB)")
    else:
        log("no large backup source found under backups/k12_db")
    # All-disabled / paused K12 pool: skip chat probe (saves traffic; probe can be
    # misleading when gateway still returns 200 on disabled rows).
    skip_probe = bool(getattr(args, "no_probe", False))
    if total > 0 and ready == 0 and disabled >= total:
        log("all accounts disabled; skip chat probe (pool paused / dead workspace)")
        skip_probe = True
    if skip_probe:
        return 0
    probe = chat_probe()
    if probe.get("ok"):
        log(f"chat probe OK {probe['latency_s']}s")
    else:
        log(f"chat probe FAIL {probe}")
    return 0 if probe.get("ok") else 1


def cmd_refill(args: argparse.Namespace) -> int:
    min_ready = max(0, int(args.min_ready))
    target = max(1, int(args.target))
    hard_cap = max(target, int(args.hard_cap))
    batch_max = max(1, int(args.max_add))

    c = live_counts()
    ready = int(c.get("normal") or 0)
    total = int(c.get("total") or 0)
    disabled = int(c.get("disabled") or 0)
    log(
        f"before total={total} ready={ready} min_ready={min_ready} "
        f"target={target} hard_cap={hard_cap} source_counts={c.get('source')}"
    )

    # Intentional full-disable (e.g. fc4f8db5 deactivated): do NOT refill from
    # pre_slim — candidates are the same dead workspace (also filtered in sample).
    if total > 0 and ready == 0 and disabled >= total and not bool(getattr(args, "force", False)):
        log(
            "all accounts disabled; skip refill "
            "(pass --force only when importing a NEW live workspace)"
        )
        return 0

    if total >= hard_cap:
        log(f"at/above hard_cap={hard_cap}; refuse refill (run slim first)")
        return 0
    if ready >= min_ready and total >= target:
        log("water level OK; no refill")
        return 0

    need = max(target - total, min_ready - ready, 0)
    need = min(need, batch_max, hard_cap - total)
    if need <= 0:
        log("need=0")
        return 0

    source = Path(args.source) if args.source else find_default_source()
    if not source or not source.is_file():
        log("no source DB; place pre_slim backup under backups/k12_db or pass --source")
        return 2

    log(f"refill need={need} from {source}")
    live_tokens = live_token_set(DB_PATH)
    # also try API page sample? token set from sqlite is enough for de-dupe
    candidates = sample_candidates(
        source,
        live_tokens=live_tokens,
        need=need,
        only_unused=not bool(args.allow_used),
        only_k12=not bool(args.allow_non_k12),
        prefer_newest=not bool(args.random),
    )
    if not candidates:
        log("no candidates available in source")
        return 1
    if args.dry_run:
        log(f"dry-run would import {len(candidates)} e.g. {candidates[0].get('email')}")
        return 0

    result = import_gateway(candidates)
    log(f"import result {result}")
    after = live_counts()
    log(
        f"after total={after.get('total')} ready={after.get('normal')} "
        f"disabled={after.get('disabled')}"
    )
    if args.probe:
        p = chat_probe()
        log(f"chat probe after refill: {p}")
    return 0 if result.get("errors", 0) == 0 else 1


def cmd_watch(args: argparse.Namespace) -> int:
    if not acquire_watch_lock():
        return 0
    interval = max(60, int(args.interval))
    log(f"refill watch start interval={interval}s min_ready={args.min_ready} target={args.target}")
    try:
        while True:
            try:
                # rebuild namespace-like for refill
                rc = cmd_refill(args)
                log(f"watch cycle exit_code={rc}")
            except Exception as e:
                log(f"watch cycle error: {e}")
            time.sleep(interval)
    except KeyboardInterrupt:
        log("watch stop")
    finally:
        release_watch_lock()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K12 pool auto-refill from backup")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_s = sub.add_parser("status")
    p_s.add_argument(
        "--no-probe",
        action="store_true",
        help="skip chat probe (also auto-skipped when all accounts disabled)",
    )

    def add_refill_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--source", default="", help="source accounts.db (default: largest pre_slim)")
        sp.add_argument("--min-ready", type=int, default=800, help="refill if normal < this")
        sp.add_argument("--target", type=int, default=1800, help="desired live total after refill")
        sp.add_argument("--hard-cap", type=int, default=2500, help="never grow live pool past this")
        sp.add_argument("--max-add", type=int, default=500, help="max accounts per refill run")
        sp.add_argument("--allow-used", action="store_true", help="allow reusing already-used backup rows")
        sp.add_argument("--allow-non-k12", action="store_true")
        sp.add_argument("--random", action="store_true", help="random sample instead of newest")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--probe", action="store_true", help="chat probe after refill")
        sp.add_argument(
            "--force",
            action="store_true",
            help="allow refill even when live pool is all-disabled (new workspace only)",
        )

    p_r = sub.add_parser("refill")
    add_refill_args(p_r)

    p_w = sub.add_parser("watch")
    add_refill_args(p_w)
    p_w.add_argument("--interval", type=int, default=900, help="seconds between checks")

    args = p.parse_args(argv)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "refill":
        return cmd_refill(args)
    if args.cmd == "watch":
        return cmd_watch(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
