#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Credential schema helpers for Databricks pool entries."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

STATUSES = frozenset(
    {"live", "soft_disabled", "dead", "needs_human", "incomplete"}
)

_EMAIL_SAFE = re.compile(r"[^a-zA-Z0-9._@+-]+")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


def trial_expiry_iso(started: Optional[datetime] = None, days: int = 14) -> str:
    base = started or utcnow()
    return (base + timedelta(days=days)).isoformat()


def new_id() -> str:
    return f"dbx-{uuid.uuid4()}"


def safe_filename_from_email(email: str) -> str:
    e = (email or "unknown").strip().lower()
    e = _EMAIL_SAFE.sub("_", e)
    return e[:120] or "unknown"


def new_credential(
    *,
    email: str,
    password: str = "",
    host: str = "",
    token: str = "",
    cloud: str = "aws",
    region: Optional[str] = None,
    status: str = "incomplete",
    aliases: Optional[Dict[str, str]] = None,
    models: Optional[Dict[str, Any]] = None,
    disable_reason: Optional[str] = None,
    needs_human_detail: Optional[str] = None,
    cred_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a new credential dict with required fields."""
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    now = iso_now()
    started = utcnow()
    return {
        "id": cred_id or new_id(),
        "email": email,
        "password": password,
        "host": host.rstrip("/") if host else "",
        "token": token,
        "cloud": cloud,
        "region": region,
        "trial_started_at": started.isoformat(),
        "trial_expires_at": trial_expiry_iso(started),
        "models": models or {},
        "aliases": aliases or {},
        "status": status,
        "disable_reason": disable_reason,
        "needs_human_detail": needs_human_detail,
        "created_at": now,
        "updated_at": now,
    }


def validate_credential(data: Dict[str, Any]) -> List[str]:
    """Return list of validation errors (empty if ok)."""
    errs: List[str] = []
    if not isinstance(data, dict):
        return ["not an object"]
    for key in ("id", "email", "status", "created_at", "updated_at"):
        if not data.get(key):
            errs.append(f"missing {key}")
    if data.get("status") and data["status"] not in STATUSES:
        errs.append(f"bad status {data.get('status')}")
    if data.get("status") == "live":
        if not data.get("host"):
            errs.append("live requires host")
        if not data.get("token"):
            errs.append("live requires token")
        models = data.get("models") or {}
        if not any(
            isinstance(v, dict) and v.get("ok") for v in models.values()
        ):
            errs.append("live requires at least one ok model probe")
    return errs


def is_expired(data: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    exp = data.get("trial_expires_at")
    if not exp:
        return False
    now = now or utcnow()
    try:
        exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    return exp_dt <= now


def selectable(data: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Whether proxy may pick this credential."""
    if data.get("status") != "live":
        return False
    if is_expired(data, now=now):
        return False
    if not data.get("host") or not data.get("token"):
        return False
    return True
