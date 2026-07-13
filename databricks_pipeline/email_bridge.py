#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mailbox create + wait for Databricks verification mail."""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from .config import ROOT, get_databricks_section

LogFn = Callable[[str], None]

_VERIFY_HREF = re.compile(
    r"https?://[^\s\"'<>]+(?:databricks|auth0|verify|confirm|activate)[^\s\"'<>]*",
    re.I,
)
_ANY_HREF = re.compile(r"https?://[^\s\"'<>]+", re.I)
# SISU 2026: subject "Your verification code is Q6Y-HMB" (alnum + hyphen)
_DBX_CODE = re.compile(
    r"(?:verification\s+code\s+is|your\s+code\s+is|code\s+is)\s*[:\s]*([A-Za-z0-9]{3,4}[- ]?[A-Za-z0-9]{3,4})",
    re.I,
)
_CODE = re.compile(r"\b(\d{6})\b")
_CODE_LABELED = re.compile(
    r"(?:code|verification|one[-\s]?time|otp)[^\d]{0,40}(\d{6})",
    re.I,
)


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)


def create_mailbox(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    log: Optional[LogFn] = None,
) -> Tuple[str, str, str]:
    """
    Create inbox.

    Returns (email, token_or_jwt, provider) where provider is cloudflare|cloud_mail.
    """
    cfg = cfg or get_databricks_section()
    raw = cfg.get("_raw") or {}
    provider = str(cfg.get("email_provider") or raw.get("email_provider") or "cloudflare").lower()

    if provider in ("cloud_mail", "vip0", "cloudmail"):
        import cloud_mail_otp as cm

        email, dev = cm.create_inbox(raw if cfg.get("use_repo_email_settings") else raw, root=ROOT)
        _log(log, f"[email] cloud_mail {email}")
        return email, dev, "cloud_mail"

    # default cloudflare via cf_mail_debug helpers
    import cf_mail_debug as cf

    api_base = str(raw.get("cloudflare_api_base") or "").rstrip("/")
    if not api_base:
        raise RuntimeError("cloudflare_api_base missing in config.json")
    auth_mode = str(raw.get("cloudflare_auth_mode") or "none")
    api_key = str(raw.get("cloudflare_api_key") or "")
    create_path = str(raw.get("cloudflare_path_accounts") or "/api/new_address")
    domain = ""
    dd = raw.get("defaultDomains") or ""
    if isinstance(dd, list) and dd:
        domain = str(dd[0])
    elif isinstance(dd, str) and dd.strip():
        domain = dd.split(",")[0].strip()
    address, jwt = cf.create_address(
        api_base,
        auth_mode=auth_mode,
        api_key=api_key,
        create_path=create_path,
        domain=domain,
    )
    _log(log, f"[email] cloudflare {address}")
    return address, jwt, "cloudflare"


_BAD_CODES = frozenset(
    {
        "000000",
        "111111",
        "123456",
        "654321",
        "999999",
        "123123",
        "000001",
        "959595",
        "380135",
    }
)


def _normalize_dbx_code(raw: str) -> str:
    """Return 6-char code without hyphen/space for box fill (Q6Y-HMB -> Q6YHMB)."""
    s = re.sub(r"[^A-Za-z0-9]", "", (raw or "").strip().upper())
    return s


def _plausible_otp(code: str) -> bool:
    """Accept 6-digit numeric or 6-char alphanumeric SISU codes."""
    if not code:
        return False
    norm = _normalize_dbx_code(code)
    if len(norm) != 6:
        return False
    if norm in _BAD_CODES or len(set(norm)) == 1:
        return False
    # pure hex colors / css noise often all digits with repeated patterns — still allow mixed
    if norm.isdigit() and norm in _BAD_CODES:
        return False
    return True


def _extract_verify(subject: str, body: str) -> Tuple[Optional[str], Optional[str]]:
    """Prefer SISU alphanumeric OTP, then numeric, then verify links."""
    blob = f"{subject}\n{body}"
    # Subject is the most reliable for Databricks: "... code is Q6Y-HMB"
    for src in (subject, blob):
        m = _DBX_CODE.search(src or "")
        if m and _plausible_otp(m.group(1)):
            return None, _normalize_dbx_code(m.group(1))
    low = blob.lower()
    looks_dbx = "databricks" in low or "verification code" in low
    if looks_dbx:
        for lm in _CODE_LABELED.finditer(blob):
            code = lm.group(1)
            if _plausible_otp(code):
                return None, code
        for cm in _CODE.finditer(blob):
            code = cm.group(1)
            if _plausible_otp(code):
                return None, code
    m = _VERIFY_HREF.search(blob)
    if m:
        return m.group(0).rstrip(").,]}>\"'"), None
    for m in _ANY_HREF.finditer(blob):
        url = m.group(0).rstrip(").,]}>\"'")
        if "databricks" in url.lower() and "verify" in url.lower():
            return url, None
    return None, None


def wait_verification(
    email: str,
    secret: str,
    provider: str,
    *,
    cfg: Optional[Dict[str, Any]] = None,
    timeout: float = 180,
    poll_interval: float = 3.0,
    log: Optional[LogFn] = None,
) -> Tuple[str, str]:
    """
    Wait for verify link or code.

    Returns (kind, value) where kind is 'link' or 'code'.
    """
    cfg = cfg or get_databricks_section()
    raw = cfg.get("_raw") or {}
    deadline = time.time() + max(30.0, float(timeout))
    provider = provider.lower()

    if provider == "cloud_mail":
        import cloud_mail_otp as cm

        while time.time() < deadline:
            try:
                messages = cm.list_messages(secret, cfg=raw)
            except Exception as exc:
                _log(log, f"[email] poll err: {exc}")
                time.sleep(poll_interval)
                continue
            for msg in messages:
                subject = str(msg.get("subject") or "")
                parts = []
                for field in ("text", "content", "message", "code", "html"):
                    v = msg.get(field)
                    if isinstance(v, str) and v.strip():
                        parts.append(v)
                body = re.sub(r"<[^>]+>", " ", "\n".join(parts))
                link, code = _extract_verify(subject, body)
                if link:
                    _log(log, f"[email] verify link subject={subject[:50]!r}")
                    return "link", link
                if code:
                    _log(log, f"[email] verify code subject={subject[:50]!r}")
                    return "code", code
            time.sleep(poll_interval)
        raise TimeoutError(f"no databricks mail for {email} within {timeout}s")

    # cloudflare — prefer Subject from raw MIME (Databricks puts code there)
    import cf_mail_debug as cf

    api_base = str(raw.get("cloudflare_api_base") or "").rstrip("/")
    path = str(raw.get("cloudflare_path_messages") or "/api/mails")
    seen = set()
    while time.time() < deadline:
        mails = cf.fetch_box(api_base, secret, path, {"limit": 20, "offset": 0})
        for item in mails:
            mid = item.get("id") or item.get("mail_id")
            key = str(mid or item.get("subject") or item.get("message_id") or id(item))
            if key in seen:
                continue
            seen.add(key)
            detail = cf.get_detail(api_base, secret, mid) if mid else {}
            subject, text = cf.flatten_mail_text(item, detail)
            raw_mime = ""
            if isinstance(detail, dict):
                raw_mime = str(detail.get("raw") or "")
            if not raw_mime and isinstance(item, dict):
                raw_mime = str(item.get("raw") or "")
            if raw_mime:
                sm = re.search(r"^Subject:\s*(.+)$", raw_mime, re.M | re.I)
                if sm:
                    # unfold simple subject
                    subject = sm.group(1).strip()
                # include raw for pattern match (header is enough)
                text = f"{text}\n{raw_mime[:8000]}"
            link, code = _extract_verify(subject, text)
            if code:
                _log(log, f"[email] verify code subject={subject[:80]!r} code={code}")
                return "code", code
            if link:
                _log(log, f"[email] verify link subject={subject[:50]!r}")
                return "link", link
        time.sleep(poll_interval)
    raise TimeoutError(f"no databricks mail for {email} within {timeout}s")
