#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""High-level: mint CPA xai-*.json for one free registered account.

Protocol-first (community): if SSO cookie is available, mint via pure HTTP:
  1) Device Flow (protocol_mint)
  2) Authorization-code PKCE (authcode_mint) — community SSO→CPA fallback
  3) Browser mint on remaining failure
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
    prefer_authcode_fallback: bool = True,
    authcode_attempts: int = 1,
    rotate_egress_before: bool = True,
    rotate_egress_on_tls: bool = True,
    log: LogFn | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Full pipeline: Device Flow → authcode PKCE → browser → write CPA → probe.

    When ``prefer_protocol`` and an SSO cookie is present:
      1. Device Flow (protocol_mint)
      2. Authorization-code PKCE (authcode_mint) if device fails and
         ``prefer_authcode_fallback`` (community SSO→CPA path)
      3. Browser mint unless ``protocol_only``
    Transient TLS failures retry device flow (``protocol_attempts``), optionally
    rotating Clash/HTTP egress between attempts.

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
        f"protocol_attempts={attempts} authcode_fallback={prefer_authcode_fallback} "
        f"rotate_before={rotate_egress_before}"
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
                        # Prefer freshly rotated HTTP-list URL; Clash same-port re-pin.
                        new_proxy = eg.get("proxy")
                        if new_proxy:
                            resolved = _apply_proxy(str(new_proxy))
                        elif eg.get("ok"):
                            resolved = _apply_proxy(
                                proxy if proxy is not None else resolved
                            )
                    time.sleep(1.0 * attempt)
                    continue
                # Do not return on protocol_only yet — authcode fallback still counts as protocol.
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
                        new_proxy = eg.get("proxy")
                        if new_proxy:
                            resolved = _apply_proxy(str(new_proxy))
                        elif eg.get("ok"):
                            resolved = _apply_proxy(
                                proxy if proxy is not None else resolved
                            )
                    time.sleep(1.0 * attempt)
                    continue
                break
    elif prefer_protocol and not sso_val:
        log("protocol mint skipped: no sso cookie")

    # --- 2) Authorization-code PKCE fallback (community SSO→CPA) ---
    authcode_err: str | None = None
    if (
        tokens is None
        and prefer_protocol
        and prefer_authcode_fallback
        and sso_val
        and not (cancel and cancel())
    ):
        try:
            from .authcode_mint import mint_with_sso_authcode
        except Exception as e:  # noqa: BLE001
            log(f"authcode mint import failed: {e}")
            mint_with_sso_authcode = None  # type: ignore[assignment]

        if mint_with_sso_authcode is not None:
            ac_attempts = max(1, min(int(authcode_attempts or 1), 3))
            for attempt in range(1, ac_attempts + 1):
                if cancel and cancel():
                    return {"ok": False, "email": email, "error": "cancelled"}
                try:
                    tokens = mint_with_sso_authcode(
                        sso_cookie=sso_val,
                        email=email,
                        proxy=resolved or None,
                        log=log,
                        cancel=cancel,
                    )
                    log(f"authcode mint ok: {email} attempt={attempt}/{ac_attempts}")
                    break
                except ProtocolMintError as e:
                    authcode_err = str(e)
                    transient = _is_transient_tls_error(e)
                    log(
                        f"authcode mint failed attempt={attempt}/{ac_attempts} "
                        f"transient={transient}: {e}"
                    )
                    if attempt < ac_attempts and transient:
                        if rotate_egress_on_tls:
                            eg = rotate_mint_egress(log)
                            new_proxy = eg.get("proxy")
                            if new_proxy:
                                resolved = _apply_proxy(str(new_proxy))
                            elif eg.get("ok"):
                                resolved = _apply_proxy(
                                    proxy if proxy is not None else resolved
                                )
                        time.sleep(1.0 * attempt)
                        continue
                    break
                except Exception as e:  # noqa: BLE001
                    authcode_err = str(e)
                    log(f"authcode mint error attempt={attempt}/{ac_attempts}: {e}")
                    if attempt < ac_attempts and _is_transient_tls_error(e):
                        time.sleep(1.0 * attempt)
                        continue
                    break

    if tokens is None and protocol_only:
        return {
            "ok": False,
            "email": email,
            "error": (
                f"protocol_only: device=[{protocol_err or 'n/a'}] "
                f"authcode=[{authcode_err or 'n/a'}]"
            ),
            "protocol_error": protocol_err,
            "authcode_error": authcode_err,
            "mint_method": "protocol",
        }

    if tokens is None:
        if not password:
            return {
                "ok": False,
                "email": email,
                "error": protocol_err
                or authcode_err
                or "protocol failed and no password for browser fallback",
                "protocol_error": protocol_err,
                "authcode_error": authcode_err,
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
            if authcode_err:
                tokens["authcode_error"] = authcode_err
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
                err_s = str(ch.get("error") or ch.get("status") or "")
                result["error"] = f"chat probe failed: {err_s}"
                # Disable immediately so CLIProxy does not rotate onto chat-denied
                # credentials (permission-denied / 403 / chat endpoint denied).
                err_l = err_s.lower()
                if any(
                    x in err_l
                    for x in (
                        "permission-denied",
                        "access to the chat endpoint is denied",
                        "forbidden",
                        "403",
                    )
                ):
                    try:
                        from cpa_xai.usage import mark_account_permission_denied

                        mark_account_permission_denied(
                            Path(path), error=err_s, log=log
                        )
                        result["disabled"] = True
                        result["disable_reason"] = "permission-denied"
                    except Exception as exc:
                        log(f"disable after chat probe fail: {exc}")
    return result
