#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Yunmeng (云梦无限邮箱) OTP provider — public temp-mail buffer channel.

API (docs: https://ymmail.ymmynb.com/ ; live base https://ym-mail.ymmynb.com):

    Header: x-api-version: 1.4
    GET  /api/version
    GET  /api/domains
    POST /api/mailboxes   {"prefix","domainName"?} → mailbox.fullAddress
    GET  /api/emails?mailbox=...&limit=...

No login / JWT / Turnstile. Shared public domains — use as **buffer only**,
not defaultDomains / own-pool waterline.

dev_token is a JSON blob so mixed CF+yunmeng routing can poll the right API::

    {"provider":"yunmeng","mailbox":"u@domain","base":"https://ym-mail.ymmynb.com",
     "api_version":"1.4","domain":"mail.jijiu6.xyz"}

Config keys::

    yunmeng_base / yunmeng_api_base   default https://ym-mail.ymmynb.com
    yunmeng_api_version               default 1.4
    yunmeng_domain / yunmeng_domains  optional preferred domain(s)
    yunmeng_prefix_len                default 12
    yunmeng_proxy / proxy             optional
    email_mix_yunmeng / email_mix_yunmeng_ratio  (wired in grok_register_ttk)
"""
from __future__ import annotations

import json
import re
import secrets
import string
import time
from typing import Any, Callable
from urllib.parse import quote

DEFAULT_BASE = "https://ym-mail.ymmynb.com"
DEFAULT_API_VERSION = "1.4"
PROVIDER = "yunmeng"

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
        raise RuntimeError("curl_cffi required for yunmeng_mail_otp") from e
    s = cf_requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _cfg_base(cfg: dict[str, Any]) -> str:
    return str(
        cfg.get("yunmeng_base")
        or cfg.get("yunmeng_api_base")
        or DEFAULT_BASE
    ).rstrip("/")


def _cfg_version(cfg: dict[str, Any]) -> str:
    return str(cfg.get("yunmeng_api_version") or DEFAULT_API_VERSION).strip() or DEFAULT_API_VERSION


def _cfg_proxy(cfg: dict[str, Any]) -> str | None:
    return (
        str(cfg.get("yunmeng_proxy") or cfg.get("proxy") or "").strip() or None
    )


def _headers(cfg: dict[str, Any], *, json_body: bool = False) -> dict[str, str]:
    h = {
        "User-Agent": "grok-auto-register/yunmeng_mail_otp",
        "Accept": "application/json",
        "x-api-version": _cfg_version(cfg),
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _normalize_domain_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = re.split(r"[,;\s]+", raw.strip())
        return [p.lstrip("@").strip().lower() for p in parts if p.strip()]
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for x in raw:
            s = str(x or "").lstrip("@").strip().lower()
            if s:
                out.append(s)
        return out
    return []


def is_yunmeng_token(dev_token: str | None) -> bool:
    tok = str(dev_token or "").strip()
    if not tok.startswith("{"):
        return False
    try:
        obj = json.loads(tok)
    except Exception:
        return False
    return isinstance(obj, dict) and str(obj.get("provider") or "").lower() in (
        PROVIDER,
        "ym",
        "yunmeng_mail",
        "ymmail",
    )


def parse_token(dev_token: str) -> dict[str, Any]:
    obj = json.loads(str(dev_token or "").strip())
    if not isinstance(obj, dict):
        raise ValueError("yunmeng token not object")
    return obj


def list_domains(cfg: dict[str, Any] | None = None) -> tuple[list[str], str]:
    """Return (enabled domain names, defaultDomain)."""
    cfg = cfg or {}
    base = _cfg_base(cfg)
    s = _session(_cfg_proxy(cfg))
    r = s.get(
        f"{base}/api/domains",
        headers=_headers(cfg),
        impersonate="chrome",
        timeout=20,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"yunmeng domains HTTP {r.status_code}: {(r.text or '')[:200]}")
    data = r.json() if r.text else {}
    if not isinstance(data, dict) or not data.get("success"):
        raise RuntimeError(f"yunmeng domains bad body: {str(data)[:200]}")
    payload = data.get("data") or {}
    domains_raw = payload.get("domains") or []
    names: list[str] = []
    for d in domains_raw:
        if isinstance(d, dict):
            if d.get("enabled") is False:
                continue
            n = str(d.get("name") or "").strip().lower()
        else:
            n = str(d or "").strip().lower()
        if n:
            names.append(n)
    default = str(payload.get("defaultDomain") or (names[0] if names else "")).strip().lower()
    return names, default


def _pick_domain(cfg: dict[str, Any]) -> str:
    preferred = _normalize_domain_list(
        cfg.get("yunmeng_domains") or cfg.get("yunmeng_domain") or ""
    )
    try:
        available, default = list_domains(cfg)
    except Exception:
        available, default = [], ""
    avail_set = set(available)
    for d in preferred:
        if not available or d in avail_set:
            return d
    if default:
        return default
    if available:
        return available[0]
    if preferred:
        return preferred[0]
    return ""


def _random_prefix(cfg: dict[str, Any]) -> str:
    try:
        n = int(cfg.get("yunmeng_prefix_len") or 12)
    except Exception:
        n = 12
    n = max(8, min(24, n))
    alphabet = string.ascii_lowercase + string.digits
    return "u" + "".join(secrets.choice(alphabet) for _ in range(n - 1))


def create_inbox(cfg: dict[str, Any] | None = None) -> tuple[str, str]:
    """Create mailbox → (fullAddress, session_json)."""
    cfg = cfg or {}
    base = _cfg_base(cfg)
    domain = _pick_domain(cfg)
    prefix = str(cfg.get("yunmeng_prefix") or "").strip() or _random_prefix(cfg)
    # high-entropy prefix; server returns existing box if prefix collides
    body: dict[str, Any] = {"prefix": prefix}
    if domain:
        body["domainName"] = domain

    s = _session(_cfg_proxy(cfg))
    last_err = ""
    # one retry with new prefix on soft failure
    for attempt in range(3):
        if attempt:
            body["prefix"] = _random_prefix(cfg)
        r = s.post(
            f"{base}/api/mailboxes",
            json=body,
            headers=_headers(cfg, json_body=True),
            impersonate="chrome",
            timeout=30,
        )
        if r.status_code >= 400 and r.status_code not in (200, 201):
            last_err = f"HTTP {r.status_code}: {(r.text or '')[:200]}"
            continue
        data = r.json() if r.text else {}
        if not isinstance(data, dict) or not data.get("success"):
            last_err = f"bad body: {str(data)[:200]}"
            continue
        mb = (data.get("data") or {}).get("mailbox") or {}
        if not isinstance(mb, dict):
            last_err = "no mailbox object"
            continue
        address = str(mb.get("fullAddress") or "").strip()
        dom = str(mb.get("domain") or domain or "").strip()
        if not address and mb.get("prefix") and dom:
            address = f"{mb.get('prefix')}@{dom}"
        if not address or "@" not in address:
            last_err = f"no address in {mb}"
            continue
        token = json.dumps(
            {
                "provider": PROVIDER,
                "mailbox": address,
                "base": base,
                "api_version": _cfg_version(cfg),
                "domain": dom,
                "prefix": str(mb.get("prefix") or prefix),
                "id": str(mb.get("id") or mb.get("_id") or ""),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return address, token
    raise RuntimeError(f"yunmeng create_inbox failed: {last_err}")


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
    """Poll GET /api/emails until xAI/Grok code found."""
    cfg = cfg or {}
    log = log or (lambda _m: None)
    try:
        sess = parse_token(dev_token) if is_yunmeng_token(dev_token) else {}
    except Exception:
        sess = {}
    mailbox = str(
        sess.get("mailbox") or email or ""
    ).strip()
    if not mailbox:
        raise RuntimeError("yunmeng wait_code: missing mailbox")
    base = str(sess.get("base") or _cfg_base(cfg)).rstrip("/")
    # merge version from token
    local_cfg = dict(cfg)
    if sess.get("api_version"):
        local_cfg["yunmeng_api_version"] = sess["api_version"]

    s = _session(_cfg_proxy(local_cfg))
    deadline = time.time() + max(15.0, float(timeout))
    interval = max(0.5, float(poll_interval or 2.0))
    next_resend = time.time() + 35
    seen: set[str] = set()
    mb_q = quote(mailbox, safe="@._+-")

    while time.time() < deadline:
        if cancel and cancel():
            raise TimeoutError("yunmeng wait cancelled")
        if resend and time.time() >= next_resend:
            try:
                resend()
            except Exception:
                pass
            next_resend = time.time() + 35
        try:
            r = s.get(
                f"{base}/api/emails?mailbox={mb_q}&limit=20",
                headers=_headers(local_cfg),
                impersonate="chrome",
                timeout=20,
            )
            if r.status_code >= 400:
                log(f"[yunmeng] poll HTTP {r.status_code}")
                time.sleep(interval)
                continue
            data = r.json() if r.text else {}
        except Exception as exc:
            log(f"[yunmeng] poll err: {exc}")
            time.sleep(interval)
            continue

        if not isinstance(data, dict):
            time.sleep(interval)
            continue
        emails = (data.get("data") or {}).get("emails") or data.get("emails") or []
        if not isinstance(emails, list):
            emails = []

        for msg in emails:
            if not isinstance(msg, dict):
                continue
            key = "|".join(
                [
                    str(msg.get("_id") or msg.get("id") or ""),
                    str(msg.get("receivedAt") or msg.get("createdAt") or ""),
                    str(msg.get("subject") or ""),
                    str(msg.get("from") or ""),
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            subject = str(msg.get("subject") or "")
            parts: list[str] = []
            for field in ("text", "body", "content", "html", "snippet", "intro"):
                v = msg.get(field)
                if isinstance(v, str) and v.strip():
                    if field == "html":
                        parts.append(re.sub(r"<[^>]+>", " ", v))
                    else:
                        parts.append(v)
            # some APIs nest body
            code = _extract_code("\n".join(parts), subject)
            if code:
                log(f"[yunmeng] code found mailbox={mailbox} subject={subject[:60]!r}")
                return code

        time.sleep(interval)
    raise TimeoutError(f"yunmeng wait code timeout mailbox={mailbox}")
