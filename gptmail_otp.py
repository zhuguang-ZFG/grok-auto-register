#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPTMail temporary email OTP provider (buffer only).

Docs: https://mail.chatgpt.org.uk/api
       https://www.chatgpt.org.uk/2025/11/gptmailapiapi.html

Base: https://mail.chatgpt.org.uk
Auth: X-API-Key: <key>  (or ?api_key=)

Public test key historically ``gpt-test`` (daily ~200k). As of 2026-07
live probes may return 401 Invalid API key / public key unavailable —
set ``gptmail_api_key`` to a working key (domain-submit or LDC store).

    GET  /api/generate-email
    GET  /api/emails?email=
    GET  /api/email/{id}

dev_token JSON::

    {"provider":"gptmail","email":"...","base":"https://mail.chatgpt.org.uk","api_key":"..."}

Config::

    gptmail_base / gptmail_api_base
    gptmail_api_key
    gptmail_proxy / proxy
    email_mix_gptmail / email_mix_gptmail_ratio
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable
from urllib.parse import quote

DEFAULT_BASE = "https://mail.chatgpt.org.uk"
# Documented public test key — may be rotated/disabled by upstream.
DEFAULT_API_KEY = "gpt-test"
PROVIDER = "gptmail"

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]
ResendFn = Callable[[], None]

_CODE_RE_XAI = re.compile(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", re.I)
_CODE_RE_NUM = re.compile(
    r"(?:verification\s+code|your\s+code|confirm(?:ation)?\s+code)[:\s]+(\d{4,8})",
    re.I,
)


def _extract_code(text: str, subject: str = "") -> str | None:
    if subject:
        m = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.I)
        if m:
            return m.group(1)
        m = _CODE_RE_XAI.search(subject)
        if m:
            return m.group(1)
    blob = text or ""
    m = _CODE_RE_XAI.search(blob)
    if m:
        return m.group(1)
    m = _CODE_RE_NUM.search(blob)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{6})\b", blob)
    if m:
        return m.group(1)
    return None


def _session(proxy: str | None = None):
    try:
        from curl_cffi import requests as cf_requests
    except ImportError as e:
        raise RuntimeError("curl_cffi required for gptmail_otp") from e
    s = cf_requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _base(cfg: dict[str, Any]) -> str:
    return str(cfg.get("gptmail_base") or cfg.get("gptmail_api_base") or DEFAULT_BASE).rstrip("/")


def _api_key(cfg: dict[str, Any]) -> str:
    return str(cfg.get("gptmail_api_key") or DEFAULT_API_KEY).strip()


def _proxy(cfg: dict[str, Any]) -> str | None:
    return str(cfg.get("gptmail_proxy") or cfg.get("proxy") or "").strip() or None


def _headers(cfg: dict[str, Any], *, json_body: bool = False) -> dict[str, str]:
    h = {
        "User-Agent": "grok-auto-register/gptmail_otp",
        "Accept": "application/json",
        "X-API-Key": _api_key(cfg),
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def is_gptmail_token(dev_token: str | None) -> bool:
    tok = str(dev_token or "").strip()
    if not tok.startswith("{"):
        return False
    try:
        obj = json.loads(tok)
    except Exception:
        return False
    return isinstance(obj, dict) and str(obj.get("provider") or "").lower() in (
        PROVIDER,
        "gpt_mail",
        "gpt-mail",
        "chatgpt_org_uk",
    )


def parse_token(dev_token: str) -> dict[str, Any]:
    obj = json.loads(str(dev_token or "").strip())
    if not isinstance(obj, dict):
        raise ValueError("gptmail token not object")
    return obj


def _raise_api(data: Any, http: int, raw: str = "") -> None:
    if isinstance(data, dict) and data.get("success") is False:
        err = str(data.get("error") or raw or "unknown")[:200]
        raise RuntimeError(f"gptmail API error HTTP {http}: {err}")
    if http >= 400:
        raise RuntimeError(f"gptmail HTTP {http}: {(raw or str(data))[:200]}")


def probe_key(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return {ok, http, error?, usage?} without creating side effects beyond generate."""
    cfg = cfg or {}
    try:
        email, _tok = create_inbox(cfg)
        return {"ok": True, "email": email}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def create_inbox(cfg: dict[str, Any] | None = None) -> tuple[str, str]:
    """Generate mailbox → (email, session_json)."""
    cfg = cfg or {}
    base = _base(cfg)
    key = _api_key(cfg)
    if not key:
        raise RuntimeError("gptmail_api_key empty")
    s = _session(_proxy(cfg))
    # Prefer GET random generate (docs)
    r = s.get(
        f"{base}/api/generate-email",
        headers=_headers(cfg),
        impersonate="chrome",
        timeout=30,
    )
    raw = r.text or ""
    try:
        data = r.json() if raw else {}
    except Exception:
        data = {}
    if r.status_code >= 400 or (isinstance(data, dict) and data.get("success") is False):
        # retry POST empty body
        r2 = s.post(
            f"{base}/api/generate-email",
            json={},
            headers=_headers(cfg, json_body=True),
            impersonate="chrome",
            timeout=30,
        )
        raw = r2.text or ""
        try:
            data = r2.json() if raw else {}
        except Exception:
            data = {}
        _raise_api(data, int(r2.status_code or 0), raw)
    else:
        if not isinstance(data, dict) or not data.get("success"):
            _raise_api(data, int(r.status_code or 0), raw)

    payload = (data or {}).get("data") or {}
    email = str(payload.get("email") or "").strip()
    if not email or "@" not in email:
        raise RuntimeError(f"gptmail missing email: {data}")
    blob = json.dumps(
        {
            "provider": PROVIDER,
            "email": email,
            "base": base,
            "api_key": key,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return email, blob


def wait_code(
    dev_token: str,
    email: str = "",
    *,
    cfg: dict[str, Any] | None = None,
    timeout: float = 150,
    poll_interval: float = 2.0,
    log: LogFn | None = None,
    cancel: CancelFn | None = None,
    resend: ResendFn | None = None,
) -> str:
    cfg = cfg or {}
    log = log or (lambda _m: None)
    if is_gptmail_token(dev_token):
        sess = parse_token(dev_token)
    else:
        raise RuntimeError("gptmail wait_code: invalid session token")
    mailbox = str(sess.get("email") or email or "").strip()
    if not mailbox:
        raise RuntimeError("gptmail wait_code: missing email")
    base = str(sess.get("base") or _base(cfg)).rstrip("/")
    local = dict(cfg)
    if sess.get("api_key"):
        local["gptmail_api_key"] = sess["api_key"]
    s = _session(_proxy(local))
    deadline = time.time() + max(15.0, float(timeout))
    interval = max(0.5, float(poll_interval or 2.0))
    next_resend = time.time() + 35
    seen: set[str] = set()
    q = quote(mailbox, safe="@._+-")

    while time.time() < deadline:
        if cancel and cancel():
            raise TimeoutError("gptmail wait cancelled")
        if resend and time.time() >= next_resend:
            try:
                resend()
            except Exception:
                pass
            next_resend = time.time() + 35
        try:
            r = s.get(
                f"{base}/api/emails?email={q}",
                headers=_headers(local),
                impersonate="chrome",
                timeout=20,
            )
            raw = r.text or ""
            data = r.json() if raw else {}
        except Exception as exc:
            log(f"[gptmail] poll err: {exc}")
            time.sleep(interval)
            continue
        if r.status_code >= 400 or (isinstance(data, dict) and data.get("success") is False):
            err = ""
            if isinstance(data, dict):
                err = str(data.get("error") or "")
            log(f"[gptmail] list HTTP {r.status_code} {err[:80]}")
            if r.status_code in (401, 403) or "api key" in err.lower() or "quota" in err.lower():
                raise RuntimeError(f"gptmail auth/quota: {err or r.status_code}")
            time.sleep(interval)
            continue
        emails = []
        if isinstance(data, dict):
            payload = data.get("data") or {}
            if isinstance(payload, dict):
                emails = payload.get("emails") or []
            elif isinstance(payload, list):
                emails = payload
        if not isinstance(emails, list):
            emails = []
        for msg in emails:
            if not isinstance(msg, dict):
                continue
            mid = str(msg.get("id") or "")
            if mid in seen:
                continue
            seen.add(mid)
            subject = str(msg.get("subject") or "")
            parts: list[str] = []
            for field in ("content", "html_content", "text", "body"):
                v = msg.get(field)
                if isinstance(v, str) and v.strip():
                    if "html" in field:
                        parts.append(re.sub(r"<[^>]+>", " ", v))
                    else:
                        parts.append(v)
            code = _extract_code("\n".join(parts), subject)
            if code:
                log(f"[gptmail] code found email={mailbox} subject={subject[:60]!r}")
                return code
            # detail fetch if list body thin
            if mid and not parts:
                try:
                    dr = s.get(
                        f"{base}/api/email/{quote(mid, safe='')}",
                        headers=_headers(local),
                        impersonate="chrome",
                        timeout=20,
                    )
                    if dr.status_code < 400:
                        detail = (dr.json() or {}).get("data") or {}
                        if isinstance(detail, dict):
                            subject = str(detail.get("subject") or subject)
                            blob_parts = []
                            for field in ("content", "html_content", "raw_content"):
                                v = detail.get(field)
                                if isinstance(v, str) and v.strip():
                                    blob_parts.append(
                                        re.sub(r"<[^>]+>", " ", v) if "html" in field else v
                                    )
                            code = _extract_code("\n".join(blob_parts), subject)
                            if code:
                                log(f"[gptmail] code found(detail) email={mailbox}")
                                return code
                except Exception as exc:
                    log(f"[gptmail] detail err: {exc}")
        time.sleep(interval)
    raise TimeoutError(f"gptmail wait code timeout email={mailbox}")
