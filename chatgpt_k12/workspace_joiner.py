# -*- coding: utf-8 -*-
"""K12 workspace joiner — make a registered account join a K12 parent workspace.

Mechanism: POST /backend-api/accounts/{workspace_id}/invites/request
The K12 parent workspace is assumed to auto-accept membership requests.

If 'request' route fails (e.g. requires approval), try 'accept' route which
hits /backend-api/accounts/{workspace_id}/invites/accept (for pre-invited accounts).
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def join_workspace(
    access_token: str,
    workspace_id: str,
    *,
    chatgpt_api: str = "https://chatgpt.com/backend-api",
    route: str = "request",
    proxy_url: str = "",
    max_retries: int = 3,
    retry_backoff_ms: int = 5000,
    log: Any = None,
) -> dict[str, Any]:
    """Join a K12 workspace.

    Args:
        access_token: Bearer token (personal scope) from registration.
        workspace_id: K12 parent workspace UUID.
        route: "request" (child asks to join) or "accept" (accept existing invite).
        proxy_url: Optional HTTP proxy.

    Returns:
        {"status": "ok"|"error", "http": int, "body": str, "workspace_id": str}
    """
    from curl_cffi import requests as cffi_requests

    log = log or print
    device_id = str(uuid.uuid4())

    path = "invites/request" if route == "request" else "invites/accept"
    url = f"{chatgpt_api}/accounts/{workspace_id}/{path}"

    session = cffi_requests.Session(impersonate="chrome")
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "oai-device-id": device_id,
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    }

    last_result = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.post(url, headers=headers, json={}, timeout=30)
            body = resp.text[:500]

            if resp.status_code in (200, 201, 204):
                log(f"  ✓ joined workspace {workspace_id[:8]}... (route={route})")
                return {
                    "status": "ok",
                    "http": resp.status_code,
                    "body": body,
                    "workspace_id": workspace_id,
                }

            if resp.status_code in (401, 403):
                log(f"  ✗ join failed: {resp.status_code} (token expired or unauthorized)")
                return {
                    "status": "error",
                    "http": resp.status_code,
                    "body": body,
                    "workspace_id": workspace_id,
                    "reason": "unauthorized",
                }

            log(f"  ⚠ join attempt {attempt}/{max_retries}: HTTP {resp.status_code}")
            last_result = {
                "status": "error",
                "http": resp.status_code,
                "body": body,
                "workspace_id": workspace_id,
            }
        except Exception as exc:
            log(f"  ⚠ join attempt {attempt}/{max_retries}: {exc}")
            last_result = {
                "status": "error",
                "http": 0,
                "body": str(exc),
                "workspace_id": workspace_id,
            }

        if attempt < max_retries:
            time.sleep(retry_backoff_ms / 1000)

    return last_result or {"status": "error", "http": 0, "body": "unknown", "workspace_id": workspace_id}
