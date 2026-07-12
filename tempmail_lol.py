#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TempMail.lol provider (community-aligned, optional email backend).

Docs: https://tempmail.lol/en/api
Base: https://api.tempmail.lol/v2

- POST /inbox/create  → {address, token}
- GET  /inbox?token=  → {emails: [...], expired: bool}

Free tier needs no API key; inboxes expire ~1 hour.
"""

from __future__ import annotations

import re
import secrets
import string
import time
from typing import Any, Callable
from urllib.parse import quote

DEFAULT_API_BASE = "https://api.tempmail.lol/v2"

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]


def _extract_code(text: str, subject: str = "") -> str | None:
    if subject:
        m = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.I)
        if m:
            return m.group(1)
        m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", subject, re.I)
        if m:
            return m.group(1)
    m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text or "", re.I)
    if m:
        return m.group(1)
    for pat in (
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ):
        m = re.search(pat, text or "", re.I)
        if m:
            return m.group(1)
    return None


def _session(proxy: str | None = None):
    try:
        from curl_cffi import requests as cf_requests
    except ImportError as e:
        raise RuntimeError("curl_cffi required for TempMail.lol") from e
    s = cf_requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _headers(api_key: str = "", *, json_body: bool = False) -> dict[str, str]:
    h: dict[str, str] = {
        "User-Agent": "grok-auto-register/tempmail_lol",
        "Accept": "application/json",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def create_inbox(cfg: dict[str, Any] | None = None) -> tuple[str, str]:
    """Create inbox → (address, token)."""
    cfg = cfg or {}
    api_base = str(cfg.get("tempmail_lol_api_base") or DEFAULT_API_BASE).rstrip("/")
    api_key = str(cfg.get("tempmail_lol_api_key") or "").strip()
    proxy = (
        str(cfg.get("tempmail_lol_proxy") or cfg.get("proxy") or "").strip() or None
    )

    body: dict[str, Any] = {}
    domain = str(cfg.get("tempmail_lol_domain") or "").strip()
    prefix = str(cfg.get("tempmail_lol_prefix") or "").strip()
    if not prefix and bool(cfg.get("tempmail_lol_random_prefix", True)):
        prefix = "u" + "".join(
            secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10)
        )
    if domain:
        body["domain"] = domain
    if prefix:
        body["prefix"] = prefix
    community = cfg.get("tempmail_lol_community", None)
    if community is not None:
        body["community"] = bool(community)

    s = _session(proxy)
    r = s.post(
        f"{api_base}/inbox/create",
        json=body if body else {},
        headers=_headers(api_key, json_body=True),
        impersonate="chrome",
        timeout=30,
    )
    if r.status_code == 402:
        raise RuntimeError("TempMail.lol quota/duration insufficient (HTTP 402)")
    if r.status_code == 403 and api_key:
        raise RuntimeError("TempMail.lol API key invalid (HTTP 403)")
    if r.status_code >= 400:
        raise RuntimeError(
            f"TempMail.lol create failed HTTP {r.status_code}: {(r.text or '')[:200]}"
        )
    data = r.json() if r.text else {}
    if not isinstance(data, dict):
        raise RuntimeError(f"TempMail.lol bad create response: {data!r}")
    address = str(data.get("address") or "").strip()
    token = str(data.get("token") or "").strip()
    if not address or not token:
        raise RuntimeError(f"TempMail.lol missing address/token: {data}")
    return address, token


def wait_code(
    token: str,
    *,
    cfg: dict[str, Any] | None = None,
    timeout: float = 150,
    poll_interval: float = 0.5,
    log: LogFn | None = None,
    cancel: CancelFn | None = None,
    resend: Callable[[], None] | None = None,
) -> str:
    """Poll inbox until verification code found."""
    cfg = cfg or {}
    api_base = str(cfg.get("tempmail_lol_api_base") or DEFAULT_API_BASE).rstrip("/")
    api_key = str(cfg.get("tempmail_lol_api_key") or "").strip()
    proxy = (
        str(cfg.get("tempmail_lol_proxy") or cfg.get("proxy") or "").strip() or None
    )
    log = log or (lambda _m: None)

    s = _session(proxy)
    deadline = time.time() + max(10.0, float(timeout))
    interval = max(0.2, float(poll_interval or 0.5))
    next_resend = time.time() + 35
    seen: set[str] = set()
    token_q = quote(token, safe="")

    while time.time() < deadline:
        if cancel and cancel():
            raise TimeoutError("TempMail.lol wait cancelled")
        if resend and time.time() >= next_resend:
            try:
                resend()
            except Exception:
                pass
            next_resend = time.time() + 35
        try:
            r = s.get(
                f"{api_base}/inbox?token={token_q}",
                headers=_headers(api_key),
                impersonate="chrome",
                timeout=20,
            )
            if r.status_code >= 400:
                time.sleep(interval)
                continue
            data = r.json() if r.text else {}
        except Exception as exc:
            log(f"[tempmail_lol] poll err: {exc}")
            time.sleep(interval)
            continue

        if not isinstance(data, dict):
            time.sleep(interval)
            continue
        if data.get("expired") is True:
            raise TimeoutError("TempMail.lol inbox expired")

        emails = data.get("emails") or []
        if not isinstance(emails, list):
            emails = []

        for msg in emails:
            if not isinstance(msg, dict):
                continue
            key = "|".join(
                [
                    str(msg.get("date") or ""),
                    str(msg.get("subject") or ""),
                    str(msg.get("from") or msg.get("sender") or ""),
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            subject = str(msg.get("subject") or "")
            parts: list[str] = []
            body = msg.get("body") or msg.get("text") or ""
            if isinstance(body, str) and body.strip():
                parts.append(body)
            html = msg.get("html")
            if isinstance(html, str) and html.strip():
                parts.append(re.sub(r"<[^>]+>", " ", html))
            for field in ("raw", "content", "intro", "snippet"):
                v = msg.get(field)
                if isinstance(v, str) and v.strip():
                    parts.append(v)
            code = _extract_code("\n".join(parts), subject)
            if code:
                log(f"[tempmail_lol] code found subject={subject[:60]!r}")
                return code

        time.sleep(interval)
    raise TimeoutError("TempMail.lol wait code timeout")
