#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load repo config.json and merge databricks defaults."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DBX: Dict[str, Any] = {
    "enabled": True,
    "register_count": 1,
    "concurrent_count": 1,
    "max_per_day": 5,
    "min_interval_sec": 120,
    "signup_url": "https://login.databricks.com/signup",
    "prefer_express": True,
    "cloud_preference": "aws",
    "email_provider": None,  # None => use top-level email_provider
    "use_repo_email_settings": True,
    "human_gate_on_phone": True,
    # When captcha_provider solves fail, still stop (do not burn infinite CapSolver $)
    "human_gate_on_captcha": True,
    # capsolver | manual | off — default resolved in get_databricks_section
    "captcha_provider": "auto",
    "captcha_max_attempts": 2,
    "auth_dir": "databricks_auths",
    "dead_dir": "databricks_auths_dead",
    "screenshots_dir": "screenshots/databricks",
    "proxy_port": 8320,
    "proxy_api_key": "sk-local-databricks-pool",
    "probe_models": [
        "databricks-qwen35-122b-a10b",
        "databricks-gpt-oss-120b",
        "databricks-gemma-3-12b",
    ],
    "probe_timeout_sec": 60,
    "workspace_ready_timeout_sec": 600,
    "otp_timeout_sec": 180,
    "browser_headless": False,
    "selectors_file": "databricks_pipeline/selectors.yaml",
    "models_catalog_file": "databricks_pipeline/models_catalog.yaml",
}


def load_raw_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load JSON config from repo root (or path). Missing file => {}."""
    cfg_path = path or (ROOT / "config.json")
    if not cfg_path.is_file():
        example = ROOT / "config.example.json"
        if example.is_file():
            cfg_path = example
        else:
            return {}
    with cfg_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"config must be object: {cfg_path}")
    return data


def get_databricks_section(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return merged databricks config with defaults."""
    raw = raw if raw is not None else load_raw_config()
    section = deepcopy(DEFAULT_DBX)
    user = raw.get("databricks") if isinstance(raw.get("databricks"), dict) else {}
    section.update(user)
    if not section.get("email_provider"):
        section["email_provider"] = raw.get("email_provider") or "cloudflare"
    # surface top-level proxy for browser/http
    if "proxy" not in section and raw.get("proxy"):
        section["proxy"] = raw.get("proxy")
    if "browser_proxy" not in section and raw.get("browser_proxy"):
        section["browser_proxy"] = raw.get("browser_proxy")
    if raw.get("capsolver_api_key") and not section.get("capsolver_api_key"):
        section["capsolver_api_key"] = raw.get("capsolver_api_key")
    prov = str(section.get("captcha_provider") or "auto").lower()
    if prov == "auto":
        section["captcha_provider"] = (
            "capsolver" if section.get("capsolver_api_key") else "manual"
        )
    section["_raw"] = raw
    section["_root"] = str(ROOT)
    return section


def resolve_path(rel_or_abs: str, root: Optional[Path] = None) -> Path:
    """Resolve path relative to repo root unless absolute."""
    root = root or ROOT
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return (root / p).resolve()
