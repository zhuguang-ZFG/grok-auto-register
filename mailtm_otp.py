#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mail.tm / mail.gw style temporary email OTP provider.

Docs: https://docs.mail.tm/

    GET  /domains
    POST /accounts  {address, password}
    POST /token     {address, password} → {token}
    GET  /messages  Authorization: Bearer <token>
    GET  /messages/{id}

Buffer channel only — public disposable domains. Not own-pool waterline.

dev_token JSON::

    {"provider":"mailtm","address":"...","password":"...","jwt":"...","base":"https://api.mail.tm"}

Config::

    mailtm_api_base          default https://api.mail.tm (or https://api.mail.gw)
    mailtm_domain            optional preferred domain
    mailtm_proxy / proxy
    email_mix_mailtm / email_mix_mailtm_ratio
"""
from __future__ import annotations

import json
import re
import secrets
import string
import time
from typing import Any, Callable

DEFAULT_BASE = "https://api.mail.tm"
PROVIDER = "mailtm"

# Community-known domains that x.ai CreateEmailValidationCode rejects upfront.
# Users can extend via config ``mailtm_banned_domains``.
_DEFAULT_BANNED_DOMAINS = {
    "duckmail.sbs",
    "web-library.net",
    "mail.tm",
    "mail.gw",
    "baldur.edu.kg",
}


def _banned_domains(cfg: dict[str, Any]) -> set[str]:
    cfg_list = cfg.get("mailtm_banned_domains") or []
    user_banned = {str(d).strip().lower() for d in cfg_list if d}
    return _DEFAULT_BANNED_DOMAINS | user_banned

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
        raise RuntimeError("curl_cffi required for mailtm_otp") from e
    s = cf_requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    # mail.tm defaults to XML/hydra without Accept: application/ld+json|json
    s.headers.update(
        {
            "Accept": "application/json, application/ld+json",
            "Content-Type": "application/json",
            "User-Agent": "grok-auto-register/mailtm_otp",
        }
    )
    return s


def _base(cfg: dict[str, Any]) -> str:
    return str(cfg.get("mailtm_api_base") or cfg.get("mailtm_base") or DEFAULT_BASE).rstrip("/")


def _bases(cfg: dict[str, Any]) -> list[str]:
    """Return ordered list of API bases to try (primary + fallbacks)."""
    primary = _base(cfg)
    seen = {primary}
    out = [primary]
    for b in cfg.get("mailtm_fallback_bases") or []:
        bs = str(b).rstrip("/")
        if bs and bs not in seen:
            out.append(bs)
            seen.add(bs)
    return out


def _proxy(cfg: dict[str, Any]) -> str | None:
    return str(cfg.get("mailtm_proxy") or cfg.get("proxy") or "").strip() or None


def is_mailtm_token(dev_token: str | None) -> bool:
    tok = str(dev_token or "").strip()
    if not tok.startswith("{"):
        return False
    try:
        obj = json.loads(tok)
    except Exception:
        return False
    return isinstance(obj, dict) and str(obj.get("provider") or "").lower() in (
        PROVIDER,
        "mail.tm",
        "mail_tm",
        "mailgw",
        "mail.gw",
    )


def parse_token(dev_token: str) -> dict[str, Any]:
    obj = json.loads(str(dev_token or "").strip())
    if not isinstance(obj, dict):
        raise ValueError("mailtm token not object")
    return obj


def _hydra_list(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("hydra:member", "member", "items", "domains", "messages"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def list_domains(cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or {}
    s = _session(_proxy(cfg))
    r = s.get(f"{_base(cfg)}/domains", impersonate="chrome", timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"mailtm domains HTTP {r.status_code}: {(r.text or '')[:160]}")
    data = r.json() if r.text else {}
    out: list[str] = []
    for item in _hydra_list(data):
        if isinstance(item, dict):
            if item.get("isActive") is False or item.get("active") is False:
                continue
            d = str(item.get("domain") or "").strip().lower()
        else:
            d = str(item or "").strip().lower()
        if d:
            out.append(d)
    return out


def _pick_domain(cfg: dict[str, Any]) -> str:
    preferred = str(cfg.get("mailtm_domain") or "").strip().lower().lstrip("@")
    banned = _banned_domains(cfg)
    try:
        available = [d for d in list_domains(cfg) if d not in banned]
    except Exception:
        available = []
    if preferred and preferred not in banned and (not available or preferred in available):
        return preferred
    if available:
        return available[0]
    if preferred:
        return preferred
    raise RuntimeError("mailtm: no domains available")


def create_inbox(cfg: dict[str, Any] | None = None) -> tuple[str, str]:
    """Create account → (address, session_json).

    Tries the configured primary base first, then any ``mailtm_fallback_bases``
    (e.g. api.mail.gw / api.duckmail.sbs) so a single dead endpoint does not
    kill the whole mail.tm family.
    """
    cfg = cfg or {}
    last_err = ""
    for base in _bases(cfg):
        ccfg = {**cfg, "mailtm_api_base": base}
        try:
            domain = _pick_domain(ccfg)
            prefix = "u" + "".join(
                secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10)
            )
            address = f"{prefix}@{domain}"
            password = secrets.token_urlsafe(14)
            s = _session(_proxy(ccfg))

            for _ in range(3):
                r = s.post(
                    f"{base}/accounts",
                    json={"address": address, "password": password},
                    impersonate="chrome",
                    timeout=30,
                )
                if r.status_code in (200, 201):
                    break
                # address taken → new prefix
                last_err = f"accounts HTTP {r.status_code}: {(r.text or '')[:160]}"
                prefix = "u" + "".join(
                    secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10)
                )
                address = f"{prefix}@{domain}"
            else:
                raise RuntimeError(f"mailtm create failed: {last_err}")

            tr = s.post(
                f"{base}/token",
                json={"address": address, "password": password},
                impersonate="chrome",
                timeout=30,
            )
            if tr.status_code >= 400:
                raise RuntimeError(f"mailtm token HTTP {tr.status_code}: {(tr.text or '')[:160]}")
            tdata = tr.json() if tr.text else {}
            jwt = str((tdata or {}).get("token") or "").strip()
            if not jwt:
                raise RuntimeError(f"mailtm missing jwt: {tdata}")

            blob = json.dumps(
                {
                    "provider": PROVIDER,
                    "address": address,
                    "password": password,
                    "jwt": jwt,
                    "base": base,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return address, blob
        except Exception as exc:
            last_err = f"{base}: {exc}"
            continue
    raise RuntimeError(f"mailtm create failed on all bases: {last_err}")


def _refresh_jwt(sess: dict[str, Any], cfg: dict[str, Any]) -> str:
    base = str(sess.get("base") or _base(cfg)).rstrip("/")
    s = _session(_proxy(cfg))
    tr = s.post(
        f"{base}/token",
        json={"address": sess.get("address"), "password": sess.get("password")},
        impersonate="chrome",
        timeout=30,
    )
    if tr.status_code >= 400:
        raise RuntimeError(f"mailtm re-token HTTP {tr.status_code}")
    jwt = str((tr.json() or {}).get("token") or "").strip()
    if not jwt:
        raise RuntimeError("mailtm re-token empty")
    return jwt


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
    if is_mailtm_token(dev_token):
        sess = parse_token(dev_token)
    else:
        raise RuntimeError("mailtm wait_code: invalid session token")
    base = str(sess.get("base") or _base(cfg)).rstrip("/")
    jwt = str(sess.get("jwt") or "").strip()
    address = str(sess.get("address") or email or "")
    s = _session(_proxy(cfg))
    deadline = time.time() + max(15.0, float(timeout))
    interval = max(0.5, float(poll_interval or 2.0))
    next_resend = time.time() + 35
    seen: set[str] = set()

    while time.time() < deadline:
        if cancel and cancel():
            raise TimeoutError("mailtm wait cancelled")
        if resend and time.time() >= next_resend:
            try:
                resend()
            except Exception:
                pass
            next_resend = time.time() + 35
        try:
            r = s.get(
                f"{base}/messages",
                headers={"Authorization": f"Bearer {jwt}"},
                impersonate="chrome",
                timeout=20,
            )
            if r.status_code in (401, 403):
                jwt = _refresh_jwt(sess, cfg)
                time.sleep(interval)
                continue
            if r.status_code >= 400:
                log(f"[mailtm] list HTTP {r.status_code}")
                time.sleep(interval)
                continue
            data = r.json() if r.text else {}
        except Exception as exc:
            log(f"[mailtm] poll err: {exc}")
            time.sleep(interval)
            continue

        messages = _hydra_list(data)
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            mid = str(msg.get("id") or msg.get("@id") or "")
            if mid in seen:
                continue
            seen.add(mid)
            subject = str(msg.get("subject") or "")
            # list endpoint often has intro only — fetch detail
            parts: list[str] = []
            intro = msg.get("intro") or msg.get("text") or ""
            if isinstance(intro, str):
                parts.append(intro)
            if mid:
                try:
                    # id may be full URI path
                    path = mid if str(mid).startswith("http") else f"{base}/messages/{mid.split('/')[-1]}"
                    if not str(mid).startswith("http"):
                        path = f"{base}/messages/{mid.split('/')[-1]}"
                    else:
                        path = mid if "/messages/" in mid else f"{base}/messages/{mid}"
                    # normalize
                    if mid.startswith("/"):
                        path = base + mid
                    elif mid.startswith("http"):
                        path = mid
                    else:
                        path = f"{base}/messages/{mid}"
                    dr = s.get(
                        path,
                        headers={"Authorization": f"Bearer {jwt}"},
                        impersonate="chrome",
                        timeout=20,
                    )
                    if dr.status_code < 400:
                        detail = dr.json() if dr.text else {}
                        if isinstance(detail, dict):
                            subject = str(detail.get("subject") or subject)
                            for field in ("text", "html", "intro"):
                                v = detail.get(field)
                                if isinstance(v, list):
                                    parts.extend(str(x) for x in v if x)
                                elif isinstance(v, str) and v.strip():
                                    if field == "html":
                                        parts.append(re.sub(r"<[^>]+>", " ", v))
                                    else:
                                        parts.append(v)
                except Exception as exc:
                    log(f"[mailtm] detail err: {exc}")
            code = _extract_code("\n".join(parts), subject)
            if code:
                log(f"[mailtm] code found address={address} subject={subject[:60]!r}")
                return code
        time.sleep(interval)
    raise TimeoutError(f"mailtm wait code timeout address={address}")
