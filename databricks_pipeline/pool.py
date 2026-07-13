#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Filesystem pool for Databricks credentials."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ROOT, get_databricks_section, resolve_path
from .schema import (
    iso_now,
    is_expired,
    safe_filename_from_email,
    selectable,
    validate_credential,
)


def auth_dir(cfg: Optional[Dict[str, Any]] = None) -> Path:
    cfg = cfg or get_databricks_section()
    return resolve_path(str(cfg.get("auth_dir") or "databricks_auths"), ROOT)


def dead_dir(cfg: Optional[Dict[str, Any]] = None) -> Path:
    cfg = cfg or get_databricks_section()
    return resolve_path(str(cfg.get("dead_dir") or "databricks_auths_dead"), ROOT)


def ensure_dirs(cfg: Optional[Dict[str, Any]] = None) -> None:
    auth_dir(cfg).mkdir(parents=True, exist_ok=True)
    dead_dir(cfg).mkdir(parents=True, exist_ok=True)


def _index_path(cfg: Optional[Dict[str, Any]] = None) -> Path:
    return auth_dir(cfg) / "pool_index.json"


def _daily_path(cfg: Optional[Dict[str, Any]] = None, day: Optional[str] = None) -> Path:
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    return auth_dir(cfg) / f".daily_count-{day}"


def credential_path(data: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Path:
    name = f"dbx-{safe_filename_from_email(str(data.get('email') or data.get('id')))}.json"
    return auth_dir(cfg) / name


def load_credential(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_credential(data: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Path:
    ensure_dirs(cfg)
    errs = validate_credential(data)
    # allow incomplete/needs_human without live checks
    if data.get("status") == "live" and errs:
        raise ValueError("invalid live credential: " + "; ".join(errs))
    data = dict(data)
    data["updated_at"] = iso_now()
    path = credential_path(data, cfg)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    rebuild_index(cfg)
    return path


def list_credentials(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    ensure_dirs(cfg)
    out: List[Dict[str, Any]] = []
    for p in sorted(auth_dir(cfg).glob("dbx-*.json")):
        try:
            out.append(load_credential(p))
        except Exception:
            continue
    return out


def get_by_id(cred_id: str, cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    for c in list_credentials(cfg):
        if c.get("id") == cred_id:
            return c
    return None


def rebuild_index(cfg: Optional[Dict[str, Any]] = None) -> Path:
    ensure_dirs(cfg)
    rows = []
    for c in list_credentials(cfg):
        rows.append(
            {
                "id": c.get("id"),
                "email": c.get("email"),
                "status": c.get("status"),
                "host": c.get("host"),
                "trial_expires_at": c.get("trial_expires_at"),
                "path": str(credential_path(c, cfg).name),
                "models_ok": [
                    k
                    for k, v in (c.get("models") or {}).items()
                    if isinstance(v, dict) and v.get("ok")
                ],
            }
        )
    path = _index_path(cfg)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"updated_at": iso_now(), "items": rows}, f, ensure_ascii=False, indent=2)
    return path


def soft_disable(
    cred_id: str,
    reason: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = get_by_id(cred_id, cfg)
    if not data:
        raise KeyError(cred_id)
    data["status"] = "soft_disabled"
    data["disable_reason"] = reason
    save_credential(data, cfg)
    return data


def mark_dead(
    cred_id: str,
    reason: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> Path:
    data = get_by_id(cred_id, cfg)
    if not data:
        raise KeyError(cred_id)
    data["status"] = "dead"
    data["disable_reason"] = reason
    src = credential_path(data, cfg)
    ensure_dirs(cfg)
    # save then move
    save_credential(data, cfg)
    dest = dead_dir(cfg) / src.name
    if src.is_file():
        shutil.move(str(src), str(dest))
    rebuild_index(cfg)
    return dest


def list_selectable(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    live = []
    for c in list_credentials(cfg):
        if is_expired(c, now=now) and c.get("status") == "live":
            c["status"] = "soft_disabled"
            c["disable_reason"] = "trial_expired"
            save_credential(c, cfg)
            continue
        if selectable(c, now=now):
            live.append(c)
    return live


def get_daily_count(cfg: Optional[Dict[str, Any]] = None) -> int:
    ensure_dirs(cfg)
    p = _daily_path(cfg)
    if not p.is_file():
        return 0
    try:
        return int(p.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        return 0


def incr_daily_count(cfg: Optional[Dict[str, Any]] = None) -> int:
    ensure_dirs(cfg)
    n = get_daily_count(cfg) + 1
    _daily_path(cfg).write_text(str(n), encoding="utf-8")
    return n


def can_register_more(cfg: Optional[Dict[str, Any]] = None, n: int = 1) -> bool:
    cfg = cfg or get_databricks_section()
    cap = int(cfg.get("max_per_day") or 5)
    return get_daily_count(cfg) + n <= cap
