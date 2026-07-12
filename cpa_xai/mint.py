#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""High-level: mint CPA xai-*.json for one free registered account.

Protocol-first (community): if SSO cookie is available, mint via pure HTTP
Device Flow (no casting browser). Fall back to browser mint on failure.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .browser_confirm import mint_with_browser
from .egress_rotate import rotate_mint_egress
from .probe import probe_mini_response, probe_models
from .protocol_mint import (
    ProtocolMintError,
    _is_transient_tls_error,
    extract_sso_from_cookies,
    mint_with_sso_protocol,
)
from .proxyutil import proxy_log_label, resolve_proxy, set_runtime_proxy
from .schema import DEFAULT_BASE_URL, build_cpa_xai_auth
from .writer import write_cpa_xai_auth

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _apply_proxy(proxy: str | None) -> str | None:
    resolved = resolve_proxy(proxy)
    set_runtime_proxy(resolved or None)
    return resolved or None


def mint_and_export(
    *,
    email: str,
    password: str,
    auth_dir: str | Path,
    page: Any | None = None,
    proxy: str | None = None,
    headless: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    headers: dict[str, str] | None = None,
    probe: bool = True,
    probe_chat: bool = False,
    browser_timeout_sec: float = 240.0,
    force_standalone: bool = False,
    cookies: Any | None = None,
    sso: str | None = None,
    reuse_browser: bool = True,
    recycle_every: int = 15,
    prefer_protocol: bool = True,
    protocol_only: bool = False,
    protocol_poll_timeout_sec: float = 90.0,
    protocol_attempts: int = 2,
    rotate_egress_before: bool = True,
    rotate_egress_on_tls: bool = True,
    log: LogFn | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Full pipeline: protocol Device Flow (preferred) | browser → write CPA → probe.

    When ``prefer_protocol`` and an SSO cookie is present, mint over pure HTTP.
    Transient TLS failures retry the full protocol flow (``protocol_attempts``),
    optionally rotating Clash/HTTP egress between attempts.
    On remaining ``ProtocolMintError`` fall back to browser unless ``protocol_only``.

    Returns dict with keys: ok, path, email, probe*, error?, mint_method?
    """
    log = log or _noop
    email = (email or "").strip()
    password = password or ""
    sso_val = (sso or "").strip() or extract_sso_from_cookies(cookies)

    if not email:
        return {"ok": False, "email": email, "error": "missing email"}
    if not password and not sso_val:
        return {"ok": False, "email": email, "error": "missing email/password/sso"}

    # Config/explicit proxy wins over shell https_proxy (common 7890 trap).
    # Thread-local pin — safe under concurrent mint workers.
    resolved = _apply_proxy(proxy)
    attempts = max(1, min(int(protocol_attempts or 1), 4))

    if rotate_egress_before:
        eg = rotate_mint_egress(log)
        # Clash keeps local URL; HTTP list may replace proxy URL.
        if eg.get("proxy"):
            resolved = _apply_proxy(str(eg.get("proxy")))
        elif eg.get("ok"):
            # node changed behind same local port — re-pin current config proxy
            resolved = _apply_proxy(proxy if proxy is not None else resolved)

    log(
        f"mint start: {email} proxy={proxy_log_label(resolved) or '(none)'} "
        f"prefer_protocol={prefer_protocol} sso={'yes' if sso_val else 'no'} "
        f"protocol_attempts={attempts} rotate_before={rotate_egress_before}"
    )

    tokens: dict[str, Any] | None = None
    protocol_err: str | None = None

    if prefer_protocol and sso_val:
        for attempt in range(1, attempts + 1):
            if cancel and cancel():
                return {"ok": False, "email": email, "error": "cancelled"}
            try:
                tokens = mint_with_sso_protocol(
                    sso_cookie=sso_val,
                    email=email,
                    proxy=resolved or None,
                    poll_timeout_sec=protocol_poll_timeout_sec,
                    log=log,
                    cancel=cancel,
                )
                log(f"protocol mint ok: {email} attempt={attempt}/{attempts}")
                break
            except ProtocolMintError as e:
                protocol_err = str(e)
                transient = _is_transient_tls_error(e)
                log(
                    f"protocol mint failed attempt={attempt}/{attempts} "
                    f"transient={transient}: {e}"
                )
                if attempt < attempts and transient:
                    if rotate_egress_on_tls:
                        eg = rotate_mint_egress(log)
                        if eg.get("proxy"):
                            resolved = _apply_proxy(str(eg.get("proxy")))
                        elif eg.get("ok"):
                            resolved = _apply_proxy(proxy if proxy is not None else resolved)
                    time.sleep(1.0 * attempt)
                    continue
                if protocol_only:
                    return {
                        "ok": False,
                        "email": email,
                        "error": f"protocol_only: {e}",
                        "mint_method": "protocol",
                    }
                break
            except Exception as e:  # noqa: BLE001
                protocol_err = str(e)
                transient = _is_transient_tls_error(e)
                log(
                    f"protocol mint error attempt={attempt}/{attempts} "
                    f"transient={transient}: {e}"
                )
                if attempt < attempts and transient:
                    if rotate_egress_on_tls:
                        eg = rotate_mint_egress(log)
                        if eg.get("proxy"):
                            resolved = _apply_proxy(str(eg.get("proxy")))
                        elif eg.get("ok"):
                            resolved = _apply_proxy(proxy if proxy is not None else resolved)
                    time.sleep(1.0 * attempt)
                    continue
                if protocol_only:
                    return {
                        "ok": False,
                        "email": email,
                        "error": f"protocol_only: {e}",
                        "mint_method": "protocol",
                    }
                break
    elif prefer_protocol and not sso_val:
        log("protocol mint skipped: no sso cookie")
        if protocol_only:
            return {
                "ok": False,
                "email": email,
                "error": "protocol_only but no sso",
                "mint_method": "protocol",
            }

    if tokens is None:
        if not password:
            return {
                "ok": False,
                "email": email,
                "error": protocol_err or "protocol failed and no password for browser fallback",
                "protocol_error": protocol_err,
            }
        try:
            tokens = mint_with_browser(
                email=email,
                password=password,
                page=page,
                proxy=resolved or None,
                headless=headless,
                browser_timeout_sec=browser_timeout_sec,
                force_standalone=force_standalone,
                cookies=cookies,
                reuse_browser=reuse_browser,
                recycle_every=recycle_every,
                poll_log=log,
                cancel=cancel,
            )
            tokens["mint_method"] = "browser"
            if protocol_err:
                tokens["protocol_error"] = protocol_err
            log(f"browser mint ok: {email}")
        except Exception as e:  # noqa: BLE001
            log(f"mint failed: {e}")
            err = str(e)
            if protocol_err:
                err = f"{err} (protocol: {protocol_err})"
            return {
                "ok": False,
                "email": email,
                "error": err,
                "protocol_error": protocol_err,
            }

    payload = build_cpa_xai_auth(
        email=email,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        id_token=tokens.get("id_token"),
        expires_in=tokens.get("expires_in"),
        base_url=base_url,
        headers=headers,
    )
    path = write_cpa_xai_auth(auth_dir, payload)
    log(f"wrote {path}")

    method = tokens.get("mint_method") or "browser"
    result: dict[str, Any] = {
        "ok": True,
        "email": email,
        "path": str(path),
        "user_code": tokens.get("user_code"),
        "base_url": base_url,
        "proxy": proxy_log_label(resolved),
        "mint_method": method,
    }
    if protocol_err and method != "protocol":
        result["protocol_error"] = protocol_err
    log(f"mint done: {email} method={method} path={path}")

    if probe:
        pr = probe_models(tokens["access_token"], base_url=base_url, proxy=resolved or None)
        result["probe_models"] = pr
        log(
            f"probe models: ok={pr.get('ok')} has_grok_45={pr.get('has_grok_45')} "
            f"ids={pr.get('model_ids')}"
        )
        if not pr.get("has_grok_45"):
            result["ok"] = False
            result["error"] = "token ok but grok-4.5 not listed"
        if probe_chat and pr.get("has_grok_45"):
            ch = probe_mini_response(
                tokens["access_token"], base_url=base_url, proxy=resolved or None
            )
            result["probe_chat"] = ch
            log(f"probe chat: ok={ch.get('ok')} model={ch.get('model')} text={ch.get('text')!r}")
            if not ch.get("ok"):
                result["ok"] = False
                result["error"] = f"chat probe failed: {ch.get('error') or ch.get('status')}"
    return result
