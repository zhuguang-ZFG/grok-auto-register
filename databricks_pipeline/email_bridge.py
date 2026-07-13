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
_CODE = re.compile(r"\b(\d{6})\b")


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


def _extract_verify(subject: str, body: str) -> Tuple[Optional[str], Optional[str]]:
    blob = f"{subject}\n{body}"
    m = _VERIFY_HREF.search(blob)
    if m:
        return m.group(0).rstrip(").,]}>\"'"), None
    # any databricks-looking link
    for m in _ANY_HREF.finditer(blob):
        url = m.group(0).rstrip(").,]}>\"'")
        if "databricks" in url.lower() or "auth" in url.lower():
            return url, None
    cm = _CODE.search(blob)
    if cm:
        return None, cm.group(1)
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

    # cloudflare
    import cf_mail_debug as cf

    api_base = str(raw.get("cloudflare_api_base") or "").rstrip("/")
    path = str(raw.get("cloudflare_path_messages") or "/api/mails")
    seen = set()
    while time.time() < deadline:
        mails = cf.fetch_box(api_base, secret, path, {"limit": 20, "offset": 0})
        for item in mails:
            mid = item.get("id") or item.get("mail_id")
            key = str(mid or item.get("subject"))
            if key in seen:
                continue
            seen.add(key)
            detail = cf.get_detail(api_base, secret, mid) if mid else {}
            subject, text = cf.flatten_mail_text(item, detail)
            # prefer databricks-related
            blob = f"{subject}\n{text}".lower()
            if "databricks" not in blob and "verify" not in blob and "confirm" not in blob:
                # still try extract
                pass
            link, code = _extract_verify(subject, text)
            if link:
                _log(log, f"[email] verify link subject={subject[:50]!r}")
                return "link", link
            if code:
                _log(log, f"[email] verify code subject={subject[:50]!r}")
                return "code", code
        time.sleep(poll_interval)
    raise TimeoutError(f"no databricks mail for {email} within {timeout}s")
