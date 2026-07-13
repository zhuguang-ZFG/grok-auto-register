#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily-capped remint budget for Dahl free keys (not infinite)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE = ROOT / "dahl_keys" / "remint_state.json"
_lock = threading.RLock()


def _day_key(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%d")


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or DEFAULT_STATE
    if not path.is_file():
        return {"day": _day_key(), "count": 0, "events": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"day": _day_key(), "count": 0, "events": []}
        return data
    except Exception:
        return {"day": _day_key(), "count": 0, "events": []}


def save_state(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or DEFAULT_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_daily_count(path: Optional[Path] = None, now: Optional[datetime] = None) -> int:
    with _lock:
        st = load_state(path)
        day = _day_key(now)
        if st.get("day") != day:
            return 0
        return int(st.get("count") or 0)


def can_remint(
    max_per_day: int = 5,
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> bool:
    max_per_day = max(0, int(max_per_day))
    if max_per_day == 0:
        return False
    return get_daily_count(path, now=now) < max_per_day


def record_remint(
    reason: str,
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> int:
    """Increment today's remint count; return new count."""
    with _lock:
        now = now or datetime.now(timezone.utc)
        day = _day_key(now)
        st = load_state(path)
        if st.get("day") != day:
            st = {"day": day, "count": 0, "events": []}
        st["count"] = int(st.get("count") or 0) + 1
        events = list(st.get("events") or [])
        events.append({"at": now.isoformat(), "reason": reason})
        st["events"] = events[-50:]
        save_state(st, path)
        return int(st["count"])


def is_quota_error(status: int, body: str) -> bool:
    """Heuristic: upstream says out of credit / forbidden quota."""
    if status in (402, 429):
        return True
    low = (body or "").lower()
    keys = (
        "insufficient",
        "quota",
        "out of tokens",
        "no tokens",
        "available_tokens",
        "rate limit",
        "credit",
        "balance",
        "exceeded",
    )
    if status in (403, 400, 401) and any(k in low for k in keys):
        return True
    # plain 401 often invalid key — treat as remint-eligible too (caller decides)
    return False


def status_snapshot(
    max_per_day: int = 5,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    used = get_daily_count(path)
    return {
        "day_utc": _day_key(),
        "remint_used_today": used,
        "remint_max_per_day": int(max_per_day),
        "remint_remaining_today": max(0, int(max_per_day) - used),
    }
