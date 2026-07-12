#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight random sample probe of cpa_auths for live-ratio water level.

Does not quarantine accounts (pool_health.py does full maintain). Used by
quota_watch to avoid treating JWT-valid-but-dead tokens as live capacity.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _load_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if cfg:
        return cfg
    path = ROOT / "config.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def list_candidate_files(cfg: dict[str, Any]) -> list[Path]:
    raw = str(cfg.get("cpa_auth_dir") or "cpa_auths")
    d = Path(raw)
    if not d.is_absolute():
        d = ROOT / d
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("xai-*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("disabled"):
            continue
        if not str(data.get("access_token") or "").strip():
            continue
        out.append(p)
    return out


def sample_probe(
    cfg: dict[str, Any] | None = None,
    *,
    sample_n: int = 3,
    proxy: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Probe up to sample_n random non-disabled CPA files via /models.

    Returns {sampled, live, dead, ratio, details[]}.
    """
    cfg = _load_cfg(cfg)
    sample_n = max(0, int(sample_n or 0))
    files = list_candidate_files(cfg)
    if sample_n <= 0 or not files:
        return {
            "sampled": 0,
            "live": 0,
            "dead": 0,
            "ratio": 1.0,
            "pool_size": len(files),
            "details": [],
            "ts": time.time(),
        }
    pick = files if len(files) <= sample_n else random.sample(files, sample_n)
    base = str(cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1")
    if proxy is None:
        proxy = str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None

    try:
        from pool_health import probe_access
    except Exception:
        # fallback import path
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "pool_health", ROOT / "pool_health.py"
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        probe_access = mod.probe_access

    live = dead = 0
    details = []
    for p in pick:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            token = str(data.get("access_token") or "").strip()
            ok, reason = probe_access(token, base, proxy, timeout=timeout)
        except Exception as exc:
            ok, reason = False, str(exc)[:120]
        if ok:
            live += 1
        else:
            dead += 1
        details.append({"file": p.name, "ok": ok, "reason": reason[:160]})

    sampled = live + dead
    ratio = (live / sampled) if sampled else 1.0
    return {
        "sampled": sampled,
        "live": live,
        "dead": dead,
        "ratio": ratio,
        "pool_size": len(files),
        "details": details,
        "ts": time.time(),
    }


def estimate_live_count(file_valid_n: int, sample: dict[str, Any]) -> int:
    """Scale file-count water level by sample live ratio (floor, min 0)."""
    ratio = float(sample.get("ratio") if sample else 1.0)
    if sample.get("sampled", 0) <= 0:
        return int(file_valid_n)
    # Don't over-trust tiny samples: blend with 1.0
    n = int(sample.get("sampled") or 0)
    if n < 2:
        blend = 0.5 * ratio + 0.5 * 1.0
    else:
        blend = ratio
    return max(0, int(round(file_valid_n * blend)))
