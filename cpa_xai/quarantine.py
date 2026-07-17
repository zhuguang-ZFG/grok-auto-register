#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Soft-quarantine for auth files that fail with recoverable errors.

Community absorb (acpa_watchdog + AGENTS.md):
  - 403 permission-denied is NOT death. Hold for ``recover_after`` seconds
    then retest. If chat works, move back to live pool.
  - 429 / rate-limit / 401 are definitive: move to _discarded.
  - State is kept inside the quarantined JSON file itself
    (``_quarantine`` block) so no separate DB is required.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RECOVER_AFTER_SEC = 24 * 3600  # 24h
MAX_RETESTS = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quarantine_dir(root: Path) -> Path:
    d = root / "cpa_auths_quarantine"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _discarded_dir(root: Path) -> Path:
    d = root / "cpa_auths_quarantine" / "_discarded"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_name(email: str, sub: str, imported: int = 0) -> str:
    import re

    em = re.sub(r"[^\w.@+-]+", "_", (email or "").strip())
    if em:
        return f"xai-{em}.json"
    s = re.sub(r"[^\w.-]+", "_", (sub or "").strip())
    if s:
        return f"xai-{s}.json"
    return f"xai-unknown-{imported}.json"


def quarantine_auth(
    auth: dict[str, Any],
    *,
    root: Path,
    reason: str,
    recover_after_sec: float | None = None,
    retest_count: int = 0,
) -> Path:
    """Write a soft-held auth into cpa_auths_quarantine/.

    Args:
        auth: auth dict (will be deep-copied and tagged)
        root: project root
        reason: e.g. 'permission_denied'
        recover_after_sec: default 24h
        retest_count: how many times it has already been retested
    """
    recover = DEFAULT_RECOVER_AFTER_SEC if recover_after_sec is None else max(0.0, float(recover_after_sec))
    now = time.time()
    entry = dict(auth)
    entry["_quarantine"] = {
        "reason": reason,
        "quarantine_at": _now_iso(),
        "quarantine_at_ts": now,
        "recover_after_sec": recover,
        "hold_until_ts": now + recover,
        "retest_count": retest_count,
        "last_status": reason,
    }
    qdir = _quarantine_dir(root)
    name = _file_name(
        str(entry.get("email") or ""),
        str(entry.get("sub") or ""),
    )
    target = qdir / name
    # avoid overwriting live file
    if (root / "cpa_auths" / name).exists() and not target.exists():
        # keep both by timestamp suffix
        target = qdir / f"{target.stem}-{int(now)}{target.suffix}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target


def discard_auth(
    auth: dict[str, Any],
    *,
    root: Path,
    reason: str,
) -> Path:
    """Move an auth to discarded (hard failure like 401/429)."""
    ddir = _discarded_dir(root)
    entry = dict(auth)
    entry.setdefault("_discarded", {})["reason"] = reason
    entry["_discarded"]["discarded_at"] = _now_iso()
    name = _file_name(
        str(entry.get("email") or ""),
        str(entry.get("sub") or ""),
    )
    target = ddir / name
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target


def iter_quarantined(root: Path):
    """Yield (path, auth_dict) for quarantined files ready to retest.

    Note: soft holds may have ``disabled: true`` (CLIProxy skip flag) while
    sitting in quarantine — still retest them after hold_until_ts. Only skip
    terminal reasons that retest cannot revive.
    """
    qdir = _quarantine_dir(root)
    now = time.time()
    _skip_reasons = frozenset({
        "refresh_revoked",
        "invalid_grant",
        "missing_refresh_token",
        "missing_access_token",
        "anti-bot",
        "anti_bot",
    })
    for p in sorted(qdir.glob("xai-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        q = data.get("_quarantine") or {}
        reason = str(q.get("reason") or q.get("last_status") or "").lower()
        if reason in _skip_reasons:
            continue
        hold = q.get("hold_until_ts") or 0
        if now >= hold:
            yield p, data


def move_to_live(
    src: Path,
    auth: dict[str, Any],
    *,
    root: Path,
) -> Path:
    """Move a retested auth from quarantine back to cpa_auths/."""
    live_dir = root / "cpa_auths"
    live_dir.mkdir(parents=True, exist_ok=True)
    entry = dict(auth)
    entry.pop("_quarantine", None)
    # Soft holds often carry disabled=true; clear so CLIProxy re-admits.
    if entry.get("disabled"):
        entry["disabled"] = False
    qs = entry.get("quota_state")
    if isinstance(qs, dict) and qs.get("reason") in (
        "free-usage-exhausted",
        "quota_exhausted",
        "permission-denied",
        "permission_denied",
    ):
        # Drop exhausted mark only when retest said live; real traffic may re-mark.
        entry.pop("quota_state", None)
    entry["recovered_from_quarantine_at"] = _now_iso()
    name = _file_name(
        str(entry.get("email") or ""),
        str(entry.get("sub") or ""),
    )
    target = live_dir / name
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    if src.exists():
        src.unlink()
    return target


def update_hold(
    src: Path,
    auth: dict[str, Any],
    *,
    recover_after_sec: float | None = None,
    new_status: str = "",
) -> None:
    """Extend hold after a failed retest."""
    recover = DEFAULT_RECOVER_AFTER_SEC if recover_after_sec is None else max(0.0, float(recover_after_sec))
    now = time.time()
    q = auth.get("_quarantine") or {}
    q["retest_count"] = int(q.get("retest_count", 0)) + 1
    q["hold_until_ts"] = now + recover
    q["last_status"] = new_status or q.get("reason", "")
    q["last_retest_at"] = _now_iso()
    auth["_quarantine"] = q
    tmp = src.with_suffix(src.suffix + ".tmp")
    tmp.write_text(json.dumps(auth, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, src)
