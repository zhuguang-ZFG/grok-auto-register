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


def _probe_chat_payload(
    payload: dict[str, Any],
    *,
    proxy: str | None,
    timeout: float,
) -> tuple[str, str]:
    """Probe real chat capability; /models alone cannot detect permission-denied."""
    import urllib.error
    import urllib.request

    from scripts.import_cpa_with_probe import DEFAULT_BASE, DEFAULT_HEADERS, classify_chat_result

    token = str(payload.get("access_token") or "").strip()
    if not token:
        return "unauthorized", "missing access_token"
    headers = dict(DEFAULT_HEADERS)
    if isinstance(payload.get("headers"), dict):
        headers.update({str(k): str(v) for k, v in payload["headers"].items()})
    headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    base = str(payload.get("base_url") or DEFAULT_BASE).rstrip("/")
    body = json.dumps(
        {
            "model": "grok-4.5",
            "messages": [{"role": "user", "content": "Reply OK."}],
            "max_tokens": 4,
        }
    ).encode()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}) if proxy else urllib.request.ProxyHandler({})
    )
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        raw = exc.read().decode("utf-8", "ignore")
    except Exception as exc:
        return "network_error", str(exc)[:160]
    return classify_chat_result(status, raw), raw


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
    quarantine: bool = False,
) -> dict[str, Any]:
    """Probe real chat capability and optionally isolate failed credentials.

    ``quarantine=True`` marks permission-denied permanently disabled and puts
    quota-exhausted accounts on the recoverable rolling-window hold.
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
    if proxy is None:
        proxy = str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None

    live = dead = 0
    details = []
    for p in pick:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            status, reason = _probe_chat_payload(data, proxy=proxy, timeout=timeout)
        except Exception as exc:
            status, reason = "network_error", str(exc)[:120]
        ok = status == "chat_ok"
        if ok:
            live += 1
        else:
            dead += 1
            if quarantine:
                if status == "permission_denied":
                    from cpa_xai.usage import mark_account_permission_denied

                    mark_account_permission_denied(p, error=reason)
                elif status == "quota_exhausted":
                    from cpa_xai.usage import mark_account_exhausted

                    mark_account_exhausted(p, disable_for_proxy=True)
        details.append(
            {"file": p.name, "ok": ok, "status": status, "reason": reason[:160]}
        )

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
