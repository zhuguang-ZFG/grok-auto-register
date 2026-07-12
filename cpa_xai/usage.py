#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-account quota tracking for smarter pool rotation.

Each CPA file gets a ``quota_state`` section tracking:
- tokens_used: cumulative tokens used (from 429 error context)
- exhausted_at: when 429 was last hit for this account
- recover_after: estimated time when quota resets (24h rolling window)

CLIProxyAPI only understands the top-level ``disabled`` flag on auth JSON
files — it does **not** read ``quota_state``. So when we mark exhaustion we
also set ``disabled: true``; on recovery we clear both so the file watcher
re-admits the account into the live rotation pool.

``quota_watch`` still uses ``quota_state`` when picking a CPA file for the
official ``~/.grok/auth.json`` path.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

FREE_TIER_LIMIT = 2_000_000  # tokens per rolling 24h window
RESET_WINDOW_SEC = 24 * 3600  # 24 hours

LogFn = Callable[[str], None]


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically: write to .tmp then os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def mark_account_exhausted(
    cpa_file: Path,
    *,
    tokens_used: int | None = None,
    log: LogFn | None = None,
    disable_for_proxy: bool = True,
) -> None:
    """Mark a CPA account as quota-exhausted (called when 429 hits it).

    Also sets ``disabled: true`` so CLIProxyAPI's auth-dir file watcher drops
    the credential from round-robin until recovery. Without this, Kimi→CLIProxy
    keeps hammering the same exhausted free-tier account.
    """
    if not cpa_file.is_file():
        return
    try:
        data = json.loads(cpa_file.read_text(encoding="utf-8"))
        qs = data.setdefault("quota_state", {})
        now = time.time()
        qs["exhausted_at"] = now
        qs["recover_after"] = now + RESET_WINDOW_SEC
        if tokens_used:
            qs["tokens_used"] = tokens_used
        qs["limit"] = FREE_TIER_LIMIT
        qs["reason"] = qs.get("reason") or "free-usage-exhausted"
        if disable_for_proxy:
            data["disabled"] = True
            qs["proxy_disabled"] = True
        _atomic_write_json(cpa_file, data)
        if log:
            email = data.get("email", cpa_file.name)
            log(f"[quota] marked {email} exhausted+disabled (recovers in ~24h)")
    except Exception:
        pass


def is_account_recovered(cpa_file: Path) -> bool:
    """Check if a previously-exhausted account has recovered its quota."""
    if not cpa_file.is_file():
        return True
    try:
        data = json.loads(cpa_file.read_text(encoding="utf-8"))
        qs = data.get("quota_state", {})
        recover_after = qs.get("recover_after") or 0
        if not recover_after:
            return True  # never exhausted
        return time.time() >= recover_after
    except Exception:
        return True


def recover_in_sec(cpa_file: Path) -> int:
    """Seconds until this account recovers. 0 if already recovered."""
    if not cpa_file.is_file():
        return 0
    try:
        data = json.loads(cpa_file.read_text(encoding="utf-8"))
        qs = data.get("quota_state", {})
        recover_after = qs.get("recover_after") or 0
        if not recover_after:
            return 0
        remain = int(recover_after - time.time())
        return max(0, remain)
    except Exception:
        return 0


def clear_exhausted_mark(cpa_file: Path, *, log: LogFn | None = None) -> None:
    """Clear quota_state and re-enable for CLIProxy when quota is usable again."""
    if not cpa_file.is_file():
        return
    try:
        data = json.loads(cpa_file.read_text(encoding="utf-8"))
        changed = False
        if "quota_state" in data:
            del data["quota_state"]
            changed = True
        if data.get("disabled"):
            data["disabled"] = False
            changed = True
        if changed:
            _atomic_write_json(cpa_file, data)
            if log:
                email = data.get("email", cpa_file.name)
                log(f"[quota] re-enabled recovered account {email}")
    except Exception:
        pass


def reenable_recovered_accounts(
    auth_dir: Path,
    *,
    log: LogFn | None = None,
    max_per_run: int = 50,
) -> dict[str, Any]:
    """Scan auth-dir; re-enable CPA files past recover_after.

    Returns counts for logging. Safe to call frequently.
    """
    stats = {"scanned": 0, "reenabled": 0, "still_exhausted": 0}
    if not auth_dir.is_dir():
        return stats
    n = 0
    for path in sorted(auth_dir.glob("xai-*.json")):
        stats["scanned"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        qs = data.get("quota_state") or {}
        recover_after = float(qs.get("recover_after") or 0)
        disabled = bool(data.get("disabled"))
        if not recover_after and not disabled:
            continue
        if recover_after and time.time() < recover_after:
            stats["still_exhausted"] += 1
            continue
        # recovered or disabled without recover window
        if recover_after and time.time() >= recover_after:
            clear_exhausted_mark(path, log=log)
            stats["reenabled"] += 1
            n += 1
        elif disabled and not recover_after:
            # operator-disabled without quota_state — leave alone
            continue
        if n >= max_per_run:
            break
    return stats


def sync_disabled_from_cds(
    auth_dir: Path,
    *,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """If CLIProxy wrote .cds cooldown files, mark matching xai-*.json disabled.

    save-cooldown-status:true writes per-auth cooldown next to auth files.
    We treat an active .cds as quota/cooldown signal and set disabled:true so
    the ready pool shrinks even when log lines lack the email.
    """
    stats = {"cds": 0, "marked": 0}
    if not auth_dir.is_dir():
        return stats
    cds_files = list(auth_dir.glob("*.cds")) + list(auth_dir.glob("**/*.cds"))
    stats["cds"] = len(cds_files)
    for cds in cds_files:
        stem = cds.name
        # try match xai-*.json by stem prefix / email fragment
        base = cds.stem  # without .cds
        candidates = list(auth_dir.glob(f"{base}*.json"))
        if not candidates:
            # cds name may be sanitized auth id; try substring match on files
            for p in auth_dir.glob("xai-*.json"):
                if base and base.lower() in p.name.lower():
                    candidates.append(p)
        for p in candidates:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("disabled") and data.get("quota_state"):
                continue
            mark_account_exhausted(p, log=log, disable_for_proxy=True)
            stats["marked"] += 1
    return stats
