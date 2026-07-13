#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe Databricks Foundation Model serving endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import ROOT, get_databricks_section, resolve_path
from .schema import iso_now


def load_catalog(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or get_databricks_section()
    path = resolve_path(
        str(cfg.get("models_catalog_file") or "databricks_pipeline/models_catalog.yaml"),
        ROOT,
    )
    if not path.is_file():
        return {"aliases": {}}
    text = path.read_text(encoding="utf-8")
    # minimal YAML subset: key: value lines under aliases:
    aliases: Dict[str, str] = {}
    section = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.endswith(":") and not s.startswith("-"):
            section = s[:-1].strip()
            continue
        if section == "aliases" and ":" in s:
            k, v = s.split(":", 1)
            aliases[k.strip()] = v.strip()
    return {"aliases": aliases, "path": str(path)}


def resolve_model_name(name: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    cat = load_catalog(cfg)
    aliases = cat.get("aliases") or {}
    return aliases.get(name, name)


def _invocations_url(host: str, endpoint: str) -> str:
    host = host.rstrip("/")
    return f"{host}/serving-endpoints/{endpoint}/invocations"


def _chat_payload(message: str = "ping") -> Dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": message}],
        "max_tokens": 16,
        "temperature": 0,
    }


def probe_endpoint(
    host: str,
    token: str,
    endpoint: str,
    *,
    timeout: float = 60,
    proxy: Optional[str] = None,
) -> Tuple[bool, str, Optional[str]]:
    """
    Returns (ok, error_or_empty, api_shape).
    api_shape is 'invocations_chat' when success.
    """
    url = _invocations_url(host, endpoint)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.post(
            url,
            headers=headers,
            data=json.dumps(_chat_payload()),
            timeout=timeout,
            proxies=proxies,
        )
    except requests.RequestException as exc:
        return False, f"network: {exc}", None

    if resp.status_code == 200:
        try:
            body = resp.json()
        except Exception:
            body = {}
        # OpenAI-like or raw text
        if isinstance(body, dict):
            if body.get("choices") or body.get("predictions") or body.get("data"):
                return True, "", "invocations_chat"
            # some endpoints return candidates
            if body.get("outputs") is not None:
                return True, "", "invocations_chat"
        if resp.text.strip():
            return True, "", "invocations_raw"
        return True, "", "invocations_chat"

    err = f"http {resp.status_code}: {resp.text[:300]}"
    return False, err, None


def probe_credential(
    data: Dict[str, Any],
    cfg: Optional[Dict[str, Any]] = None,
    *,
    models: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Mutate and return credential with updated models + status."""
    cfg = cfg or get_databricks_section()
    host = str(data.get("host") or "").rstrip("/")
    token = str(data.get("token") or "")
    if not host or not token:
        data["status"] = "incomplete"
        data["disable_reason"] = "missing_host_or_token"
        return data

    model_list = models or list(cfg.get("probe_models") or [])
    timeout = float(cfg.get("probe_timeout_sec") or 60)
    proxy = str(cfg.get("proxy") or (cfg.get("_raw") or {}).get("proxy") or "") or None
    catalog = load_catalog(cfg)
    aliases = dict(catalog.get("aliases") or {})

    model_state: Dict[str, Any] = dict(data.get("models") or {})
    any_ok = False
    auth_dead = False
    quota = False

    for raw_name in model_list:
        endpoint = resolve_model_name(raw_name, cfg)
        ok, err, shape = probe_endpoint(
            host, token, endpoint, timeout=timeout, proxy=proxy
        )
        model_state[endpoint] = {
            "ok": ok,
            "last_probe_at": iso_now(),
            "last_error": err or None,
            "api_shape": shape,
        }
        if ok:
            any_ok = True
        elif "http 401" in err or "http 403" in err:
            auth_dead = True
        elif "http 402" in err:
            quota = True

    data["models"] = model_state
    # merge default reverse aliases for ok models
    data_aliases = dict(data.get("aliases") or {})
    for a, e in aliases.items():
        data_aliases[a] = e
    data["aliases"] = data_aliases
    data["updated_at"] = iso_now()

    if any_ok:
        data["status"] = "live"
        data["disable_reason"] = None
    elif auth_dead:
        data["status"] = "dead"
        data["disable_reason"] = "auth_failed"
    elif quota:
        data["status"] = "soft_disabled"
        data["disable_reason"] = "quota"
    else:
        data["status"] = "soft_disabled"
        data["disable_reason"] = "probe_all_failed"
    return data


def forward_chat(
    data: Dict[str, Any],
    model: str,
    messages: List[Dict[str, Any]],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    max_tokens: int = 256,
    temperature: float = 0.7,
) -> Tuple[int, Dict[str, Any]]:
    """Forward OpenAI-style chat to serving endpoint. Returns (status_code, body)."""
    cfg = cfg or get_databricks_section()
    endpoint = resolve_model_name(model, cfg)
    # also check credential aliases
    for a, e in (data.get("aliases") or {}).items():
        if a == model or e == model:
            endpoint = e
            break
    host = str(data.get("host") or "").rstrip("/")
    token = str(data.get("token") or "")
    url = _invocations_url(host, endpoint)
    proxy = str(cfg.get("proxy") or (cfg.get("_raw") or {}).get("proxy") or "") or None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    payload = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=float(cfg.get("probe_timeout_sec") or 60),
        proxies=proxies,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"error": resp.text[:500], "status_code": resp.status_code}
    if resp.status_code == 200 and isinstance(body, dict) and "choices" not in body:
        # normalize minimal OpenAI shape if raw text
        text = body.get("text") or body.get("output") or json.dumps(body)[:2000]
        if isinstance(body.get("predictions"), list) and body["predictions"]:
            text = str(body["predictions"][0])
        body = {
            "id": "dbx-proxy",
            "object": "chat.completion",
            "model": endpoint,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        }
    return resp.status_code, body if isinstance(body, dict) else {"raw": body}
