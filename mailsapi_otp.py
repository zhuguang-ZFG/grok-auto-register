#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fixed-inbox OTP via mailsapi-style get-code URLs (backup channel).

NOT a bulk email provider. Use for dedicated addresses of the form::

    email----https://gapi.mailsapi.com/api/get-code?uid=...

Config (any one is enough)::

    mailsapi_entries: [{"email": "...", "url": "..."}, ...]
    mailsapi_lines: ["email----url", ...]
    mailsapi_email + mailsapi_get_code_url
    mailsapi_credentials_file: path to lines (default mail_credentials.txt)

Main register path stays Cloudflare / duckmail / yyds. This module only:

- supplies a fixed (email, url) when email_provider == mailsapi
- polls data.code for that URL when wait_code is used or email is in the map
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]
ResendFn = Callable[[], None]

DEFAULT_CREDENTIALS_FILE = "mail_credentials.txt"

# Grok / xAI codes are usually 6 digits or XXX-XXX; mailsapi often returns digits only.
_CODE_RE = re.compile(r"^[A-Za-z0-9-]{4,16}$")


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _norm_url(url: str) -> str:
    return (url or "").strip()


def parse_credential_line(line: str) -> tuple[str, str] | None:
    """Parse ``email----url`` (4 dashes) or ``email|url`` / ``email,url``."""
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    for sep in ("----", "|", "\t", ","):
        if sep in raw:
            left, right = raw.split(sep, 1)
            email, url = _norm_email(left), _norm_url(right)
            if email and "@" in email and url.startswith("http"):
                return email, url
    return None


def load_entries(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> list[dict[str, str]]:
    """Load fixed OTP inboxes. Later sources override earlier by email."""
    cfg = cfg or {}
    root = root or Path.cwd()
    by_email: dict[str, str] = {}

    def put(email: str, url: str) -> None:
        e, u = _norm_email(email), _norm_url(url)
        if e and u.startswith("http"):
            by_email[e] = u

    # 1) credentials file (gitignored)
    rel = str(cfg.get("mailsapi_credentials_file") or DEFAULT_CREDENTIALS_FILE).strip()
    path = Path(rel)
    if not path.is_absolute():
        path = root / path
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            parsed = parse_credential_line(line)
            if parsed:
                put(*parsed)

    # 2) mailsapi_lines
    lines = cfg.get("mailsapi_lines") or []
    if isinstance(lines, str):
        lines = lines.splitlines()
    if isinstance(lines, list):
        for item in lines:
            if isinstance(item, str):
                parsed = parse_credential_line(item)
                if parsed:
                    put(*parsed)

    # 3) mailsapi_entries
    entries = cfg.get("mailsapi_entries") or []
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            put(str(item.get("email") or ""), str(item.get("url") or item.get("get_code_url") or ""))

    # 4) single pair
    put(str(cfg.get("mailsapi_email") or ""), str(cfg.get("mailsapi_get_code_url") or ""))

    return [{"email": e, "url": u} for e, u in by_email.items()]


def resolve_url(email: str, cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> str | None:
    email = _norm_email(email)
    if not email:
        return None
    for item in load_entries(cfg, root=root):
        if item["email"] == email:
            return item["url"]
    return None


def pick_inbox(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> tuple[str, str]:
    """Return (email, get_code_url) for mailsapi provider mode."""
    entries = load_entries(cfg, root=root)
    if not entries:
        raise RuntimeError(
            "mailsapi: no fixed inbox configured "
            "(mail_credentials.txt / mailsapi_entries / mailsapi_email+url)"
        )
    # Prefer explicit mailsapi_email if present
    cfg = cfg or {}
    prefer = _norm_email(str(cfg.get("mailsapi_email") or ""))
    for item in entries:
        if prefer and item["email"] == prefer:
            return item["email"], item["url"]
    return entries[0]["email"], entries[0]["url"]


def _with_cache_buster(url: str) -> str:
    parts = urlparse(url)
    q = parse_qs(parts.query, keep_blank_values=True)
    q["_ts"] = [str(int(time.time() * 1000))]
    flat = []
    for k, vals in q.items():
        for v in vals:
            flat.append((k, v))
    new_query = urlencode(flat)
    return urlunparse(parts._replace(query=new_query))


def fetch_code(url: str, *, proxy: str | None = None, timeout: float = 20.0) -> str | None:
    """GET get-code URL → data.code or None."""
    url = _norm_url(url)
    if not url:
        return None
    try:
        import requests
    except ImportError as e:
        raise RuntimeError("requests required for mailsapi_otp") from e

    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "User-Agent": "grok-auto-register/mailsapi_otp",
        "Accept": "application/json",
    }
    target = _with_cache_buster(url)
    try:
        resp = requests.get(target, headers=headers, proxies=proxies, timeout=timeout)
    except Exception:
        # Direct retry without proxy (API often mainland-reachable)
        try:
            resp = requests.get(target, headers=headers, proxies={}, timeout=timeout)
        except Exception:
            return None
    if resp.status_code != 200:
        return None
    try:
        body = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        return text if _CODE_RE.match(text) else None

    if not isinstance(body, dict):
        return None
    # {code:0, data:{code:"080782"}}
    data = body.get("data")
    if isinstance(data, dict) and data.get("code") is not None:
        code = str(data.get("code")).strip()
        return code if code and code.lower() not in ("null", "none") else None
    if body.get("code") is not None and not isinstance(body.get("code"), int):
        code = str(body.get("code")).strip()
        return code if code else None
    # nested message fields
    for key in ("verification_code", "otp", "msg", "message"):
        if key in body and isinstance(body[key], str) and _CODE_RE.match(body[key].strip()):
            return body[key].strip()
    return None


def wait_code(
    dev_token: str,
    email: str = "",
    *,
    cfg: dict[str, Any] | None = None,
    timeout: float = 180,
    poll_interval: float = 3.0,
    log: LogFn | None = None,
    cancel: CancelFn | None = None,
    resend: ResendFn | None = None,
    root: Path | None = None,
) -> str:
    """Poll until a *new* code appears (differs from baseline at start).

    ``dev_token`` is the get-code URL (from pick_inbox) or empty if resolvable via email map.
    """
    cfg = cfg or {}
    url = _norm_url(dev_token) if str(dev_token or "").startswith("http") else ""
    if not url:
        url = resolve_url(email, cfg, root=root) or ""
    if not url:
        raise RuntimeError(f"mailsapi: no get-code URL for {email or '(empty)'}")

    proxy = (
        str(cfg.get("mailsapi_proxy") or "").strip()
        or (None if cfg.get("mailsapi_direct", True) else str(cfg.get("proxy") or "").strip() or None)
    )
    accept_cached = bool(cfg.get("mailsapi_accept_cached_code", False))
    resend_after = float(cfg.get("mailsapi_resend_after_sec") or 45)
    poll_interval = max(0.5, float(poll_interval or 3))
    deadline = time.time() + max(5.0, float(timeout or 180))

    def _log(msg: str) -> None:
        if log:
            log(msg)

    baseline = fetch_code(url, proxy=proxy)
    if baseline:
        _log(f"[mailsapi] baseline code cached={baseline!r} (wait for change unless accept_cached)")
    else:
        _log("[mailsapi] no baseline code yet; waiting for first code")

    started = time.time()
    resent = False
    last_err = ""

    while time.time() < deadline:
        if cancel and cancel():
            raise RuntimeError("mailsapi: cancelled")
        code = fetch_code(url, proxy=proxy)
        if code:
            if accept_cached:
                _log(f"[mailsapi] got code={code!r} (accept_cached=true)")
                return code
            if baseline is None or code != baseline:
                _log(f"[mailsapi] got new code={code!r} (was {baseline!r})")
                return code
        if resend and not resent and (time.time() - started) >= resend_after:
            try:
                resend()
                resent = True
                # After resend, treat previous code as baseline again
                if code:
                    baseline = code
                _log("[mailsapi] requested resend; waiting for new code")
            except Exception as exc:
                last_err = str(exc)
                _log(f"[mailsapi] resend failed: {exc}")
        time.sleep(poll_interval)

    hint = f" last_err={last_err}" if last_err else ""
    raise TimeoutError(
        f"mailsapi: timeout waiting for code email={email or '?'} baseline={baseline!r}{hint}"
    )


def dump_status(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> str:
    entries = load_entries(cfg, root=root)
    return json.dumps({"count": len(entries), "emails": [e["email"] for e in entries]}, ensure_ascii=False)
