#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Authorization-code (PKCE) SSO → OIDC tokens for CPA mint fallback.

Community path (sso_to_cliproxy / grok-build-auth lineage):
  SSO cookie → CreateCookieSetterLink → OAuth consent Allow → code → token.

Used when Device Flow (protocol_mint) fails. SSO-only; no YesCaptcha password
login here (password CreateSession needs a captcha key — leave that to browser).

Returns the same token dict shape as mint_with_sso_protocol.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse

from .oauth_device import CLIENT_ID, ISSUER, SCOPE
from .protocol_mint import ProtocolMintError, _is_transient_tls_error
from .proxyutil import proxy_log_label, resolve_proxy, set_runtime_proxy

LogFn = Callable[[str], None]

ACCOUNTS_ORIGIN = "https://accounts.x.ai"
CREATE_COOKIE_SETTER_RPC = (
    f"{ACCOUNTS_ORIGIN}/auth_mgmt.AuthManagement/CreateCookieSetterLink"
)
AUTHORIZATION_ENDPOINT = f"{ISSUER}/oauth2/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/oauth2/token"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:56121/callback"
# Observed Next.js server action for consent Allow (may change on deploy).
SUBMIT_OAUTH2_CONSENT_ACTION = "4005315a1d7e426de592990bb54bb37471f39dd6d2"
CONNECT_ES = "connect-es/2.1.1"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _noop_log(_: str) -> None:
    return None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _code_verifier() -> str:
    return _b64url(secrets.token_bytes(48))


def _code_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


# --- minimal gRPC-web helpers (CreateCookieSetterLink only) ---


def _pb_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            break
    return bytes(out)


def _pb_string(field: int, value: str) -> bytes:
    data = value.encode("utf-8")
    tag = (field << 3) | 2  # length-delimited
    return _pb_varint(tag) + _pb_varint(len(data)) + data


def _grpc_frame(msg: bytes) -> bytes:
    # 1 byte flags (0) + 4 byte big-endian length + message
    return b"\x00" + len(msg).to_bytes(4, "big") + msg


def _grpc_headers(referer: str) -> dict[str, str]:
    return {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": CONNECT_ES,
        "origin": ACCOUNTS_ORIGIN,
        "referer": referer,
        "user-agent": USER_AGENT,
        "accept": "*/*",
    }


def _parse_grpc_web_urls(body: bytes) -> list[str]:
    """Best-effort extract https URLs from grpc-web framed protobuf response."""
    urls: list[str] = []
    i = 0
    while i + 5 <= len(body):
        # flags = body[i]
        length = int.from_bytes(body[i + 1 : i + 5], "big")
        i += 5
        if length < 0 or i + length > len(body):
            break
        chunk = body[i : i + length]
        i += length
        # skip trailers frame (flag 0x80)
        text = chunk.decode("utf-8", errors="ignore")
        for m in re.finditer(r"https://[^\x00-\x1f\"'<>\s]+", text):
            urls.append(m.group(0).rstrip(").,;"))
        # also scan raw for set-cookie paths
        for m in re.finditer(rb"https://[^\x00-\x1f\"'<>\s]+", chunk):
            try:
                urls.append(m.group(0).decode("ascii", errors="ignore").rstrip(").,;"))
            except Exception:
                pass
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _session(proxy: str | None, log: LogFn):
    try:
        from curl_cffi import requests as creq
    except ImportError as exc:
        raise ProtocolMintError("curl_cffi required for authcode mint") from exc
    s = creq.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _set_sso(session: Any, sso: str) -> None:
    for dom in ("accounts.x.ai", ".accounts.x.ai", "auth.x.ai", ".x.ai"):
        try:
            session.cookies.set("sso", sso, domain=dom)
        except Exception:
            try:
                session.cookies.set("sso", sso)
            except Exception:
                pass
        try:
            session.cookies.set("sso-rw", sso, domain=dom)
        except Exception:
            pass


def _req(
    do_req: Callable[[], Any],
    *,
    what: str,
    log: LogFn,
    retries: int = 2,
) -> Any:
    last: BaseException | None = None
    attempts = max(int(retries), 0) + 1
    for i in range(attempts):
        try:
            return do_req()
        except Exception as e:  # noqa: BLE001
            last = e
            if i + 1 >= attempts or not _is_transient_tls_error(e):
                raise ProtocolMintError(f"authcode {what}: {e}") from e
            log(f"authcode {what} transient try={i+1}/{attempts}: {e}")
            time.sleep(1.2 * (i + 1))
    raise ProtocolMintError(f"authcode {what}: {last}")


def _create_cookie_setter(
    session: Any,
    *,
    success_url: str,
    error_url: str,
    referer: str,
    log: LogFn,
) -> str:
    msg = _pb_string(1, success_url) + _pb_string(2, error_url)
    framed = _grpc_frame(msg)

    def do():
        return session.post(
            CREATE_COOKIE_SETTER_RPC,
            headers=_grpc_headers(referer),
            data=framed,
            impersonate="chrome",
            timeout=45,
        )

    resp = _req(do, what="CreateCookieSetterLink", log=log)
    hdrs = {k.lower(): v for k, v in (resp.headers or {}).items()}
    body = resp.content or b""
    grpc_status = hdrs.get("grpc-status")
    grpc_msg = unquote(hdrs.get("grpc-message") or "")
    urls = _parse_grpc_web_urls(body)
    cookie_setter = next((u for u in urls if "set-cookie" in u), None) or (
        urls[0] if urls else None
    )
    if grpc_status not in (None, "0", 0) and not cookie_setter:
        raise ProtocolMintError(
            f"CreateCookieSetterLink failed status={grpc_status} msg={grpc_msg[:160]}"
        )
    if not cookie_setter:
        # sometimes message is only in body text
        text = body.decode("utf-8", errors="replace")
        if "blocked" in text.lower() or "blocked" in grpc_msg.lower():
            raise ProtocolMintError(f"user blocked: {grpc_msg or text[:120]}")
        raise ProtocolMintError(
            f"CreateCookieSetterLink no cookie_setter_url grpc={grpc_status} {grpc_msg[:120]}"
        )
    log(f"authcode cookie_setter ok ({cookie_setter[:80]}...)")
    return cookie_setter


def _follow_set_cookie(session: Any, url: str, log: LogFn, hops: int = 8) -> str:
    current = url
    for _ in range(hops):
        if "code=" in current and (
            "127.0.0.1" in current or "localhost" in current or "callback" in current
        ):
            return current
        resp = _req(
            lambda u=current: session.get(
                u, impersonate="chrome", timeout=45, allow_redirects=False
            ),
            what="set-cookie/redirect",
            log=log,
        )
        loc = resp.headers.get("location") or resp.headers.get("Location") or ""
        status = getattr(resp, "status_code", 0)
        log(f"authcode hop HTTP {status} loc={(loc or '')[:100]}")
        if loc:
            current = urljoin(current, loc)
            continue
        # 200 HTML consent page
        if status == 200 and "consent" in current:
            return current
        break
    return current


def _submit_consent(
    session: Any,
    *,
    page_url: str,
    page_html: str,
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    challenge: str,
    nonce: str,
    log: LogFn,
) -> str:
    action_id = SUBMIT_OAUTH2_CONSENT_ACTION
    m = re.search(
        r'createServerReference\)\("([a-f0-9]{40,44})"[^)]*submitOAuth2Consent',
        page_html or "",
    )
    if not m:
        m = re.search(r'createServerReference\)\("([a-f0-9]{40,44})"', page_html or "")
    if m:
        action_id = m.group(1)

    router_tree = (
        '["",{"children":["(app)",{"children":["(auth)",{"children":["oauth2",'
        '{"children":["consent",{"children":["__PAGE__",{}]}]}]}]}]},'
        '"$undefined","$undefined",16]'
    )
    payload = [
        {
            "action": "allow",
            "clientId": client_id,
            "redirectUri": redirect_uri,
            "scope": scopes,
            "state": state,
            "codeChallenge": challenge,
            "codeChallengeMethod": "S256",
            "nonce": nonce,
            "principalType": "User",
            "principalId": "",
            "referrer": "",
        }
    ]
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "accept": "text/x-component",
        "content-type": "text/plain;charset=UTF-8",
        "next-action": action_id,
        "next-router-state-tree": quote(router_tree, safe=""),
        "origin": ACCOUNTS_ORIGIN,
        "referer": page_url,
        "user-agent": USER_AGENT,
    }
    post_url = page_url.split("?")[0] if "consent" in page_url else page_url
    resp = _req(
        lambda: session.post(
            post_url, headers=headers, data=body, impersonate="chrome", timeout=45
        ),
        what="submitOAuth2Consent",
        log=log,
    )
    text = resp.text or ""
    log(f"authcode consent HTTP {getattr(resp, 'status_code', '?')} body={text[:120]!r}")
    m = re.search(r'"code"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r"code=([A-Za-z0-9._~\-]+)", text)
    if m and "error" not in m.group(0):
        return m.group(1)
    loc = resp.headers.get("location") or resp.headers.get("Location") or ""
    if "code=" in loc:
        return _code_from_url(urljoin(page_url, loc), state)
    # retry with full page_url
    if post_url != page_url:
        resp = _req(
            lambda: session.post(
                page_url, headers=headers, data=body, impersonate="chrome", timeout=45
            ),
            what="submitOAuth2Consent2",
            log=log,
        )
        text = resp.text or ""
        m = re.search(r'"code"\s*:\s*"([^"]+)"', text)
        if m:
            return m.group(1)
        loc = resp.headers.get("location") or resp.headers.get("Location") or ""
        if "code=" in loc:
            return _code_from_url(urljoin(page_url, loc), state)
    raise ProtocolMintError(f"submitOAuth2Consent failed: {text[:240]}")


def _code_from_url(url: str, expected_state: str) -> str:
    q = parse_qs(urlparse(url).query)
    if q.get("error"):
        raise ProtocolMintError(f"oauth error: {q.get('error')} {q.get('error_description')}")
    code = (q.get("code") or [""])[0]
    state = (q.get("state") or [""])[0]
    if not code:
        raise ProtocolMintError(f"no code in redirect: {url[:180]}")
    if expected_state and state and state != expected_state:
        raise ProtocolMintError("oauth state mismatch")
    return code


def _exchange_code(
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    proxy: str | None,
    log: LogFn,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    # prefer curl_cffi for TLS fingerprint consistency
    try:
        from curl_cffi import requests as creq

        proxies = {"http": proxy, "https": proxy} if proxy else None

        def do():
            return creq.post(
                TOKEN_ENDPOINT,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                proxies=proxies,
                impersonate="chrome",
                timeout=30,
            )

        resp = _req(do, what="token_exchange", log=log)
        if getattr(resp, "status_code", 0) != 200:
            raise ProtocolMintError(
                f"token exchange HTTP {resp.status_code}: {(resp.text or '')[:300]}"
            )
        return resp.json()
    except ProtocolMintError:
        raise
    except Exception as e:  # noqa: BLE001
        # stdlib fallback
        import urllib.request

        body = urlencode(data).encode()
        req = urllib.request.Request(
            TOKEN_ENDPOINT,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        handlers = []
        if proxy:
            handlers.append(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
        opener = (
            urllib.request.build_opener(*handlers)
            if handlers
            else urllib.request.build_opener()
        )
        try:
            with opener.open(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e2:  # noqa: BLE001
            raise ProtocolMintError(f"token exchange: {e2}") from e2


def mint_with_sso_authcode(
    *,
    sso_cookie: str,
    email: str = "",
    proxy: str | None = None,
    timeout: float = 45.0,
    log: LogFn | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """SSO cookie → authorization-code OIDC tokens (no browser, no captcha).

    Raises ProtocolMintError on failure (caller may fall back to browser mint).
    """
    log = log or _noop_log
    sso = (sso_cookie or "").strip()
    if not sso:
        raise ProtocolMintError("authcode: missing sso cookie")
    if cancel and cancel():
        raise ProtocolMintError("cancelled")

    resolved = resolve_proxy(proxy)
    set_runtime_proxy(resolved or None)
    log(
        f"authcode mint start email={email or '?'} proxy={proxy_log_label(resolved) or '(none)'}"
    )

    session = _session(resolved or None, log)
    _set_sso(session, sso)

    state = secrets.token_hex(16)
    nonce = secrets.token_hex(16)
    verifier = _code_verifier()
    challenge = _code_challenge(verifier)
    redirect_uri = DEFAULT_REDIRECT_URI
    scopes = SCOPE  # space-separated, same as device flow

    auth_params = {
        "client_id": CLIENT_ID,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "referrer": "cli-proxy-api",
        "plan": "generic",
    }
    auth_url = AUTHORIZATION_ENDPOINT + "?" + urlencode(auth_params)
    consent_url = (
        f"{ACCOUNTS_ORIGIN}/oauth2/consent?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": redirect_uri,
                "scope": scopes,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "nonce": nonce,
            }
        )
    )

    # Prime authorize (pending OAuth request on AS)
    try:
        _req(
            lambda: session.get(
                auth_url, impersonate="chrome", timeout=timeout, allow_redirects=False
            ),
            what="authorize_prime",
            log=log,
        )
    except ProtocolMintError:
        raise
    except Exception as e:  # noqa: BLE001
        log(f"authcode authorize prime soft-fail: {e}")

    if cancel and cancel():
        raise ProtocolMintError("cancelled")

    setter = _create_cookie_setter(
        session,
        success_url=consent_url,
        error_url=f"{ACCOUNTS_ORIGIN}/sign-in",
        referer=f"{ACCOUNTS_ORIGIN}/sign-in?redirect=oauth2-provider",
        log=log,
    )

    landed = _follow_set_cookie(session, setter, log)
    code: str | None = None

    if "code=" in landed and (
        "127.0.0.1" in landed or "localhost" in landed or "callback" in landed
    ):
        code = _code_from_url(landed, state)
    elif "consent" in landed:
        page = _req(
            lambda: session.get(
                landed, impersonate="chrome", timeout=timeout, allow_redirects=False
            ),
            what="consent_page",
            log=log,
        )
        loc = page.headers.get("location") or page.headers.get("Location") or ""
        if loc and "code=" in loc:
            code = _code_from_url(urljoin(landed, loc), state)
        else:
            html = page.text or ""
            code = _submit_consent(
                session,
                page_url=landed,
                page_html=html,
                client_id=CLIENT_ID,
                redirect_uri=redirect_uri,
                scopes=scopes,
                state=state,
                challenge=challenge,
                nonce=nonce,
                log=log,
            )
    else:
        # last resort: open consent directly with SSO
        page = _req(
            lambda: session.get(
                consent_url,
                impersonate="chrome",
                timeout=timeout,
                allow_redirects=True,
            ),
            what="consent_direct",
            log=log,
        )
        final = str(getattr(page, "url", "") or consent_url)
        if "code=" in final:
            code = _code_from_url(final, state)
        else:
            code = _submit_consent(
                session,
                page_url=final if "consent" in final else consent_url,
                page_html=page.text or "",
                client_id=CLIENT_ID,
                redirect_uri=redirect_uri,
                scopes=scopes,
                state=state,
                challenge=challenge,
                nonce=nonce,
                log=log,
            )

    if not code:
        raise ProtocolMintError("authcode: failed to obtain authorization code")

    log("authcode exchanging code for tokens...")
    token = _exchange_code(
        code=code, verifier=verifier, redirect_uri=redirect_uri, proxy=resolved, log=log
    )
    access = str(token.get("access_token") or "")
    refresh = str(token.get("refresh_token") or "")
    if not access or not refresh:
        raise ProtocolMintError(f"authcode token missing fields: {list(token.keys())}")

    out = {
        "access_token": access,
        "refresh_token": refresh,
        "id_token": token.get("id_token"),
        "expires_in": int(token.get("expires_in") or 21600),
        "token_type": token.get("token_type") or "Bearer",
        "mint_method": "authcode",
    }
    log(f"authcode mint ok email={email or '?'} expires_in={out['expires_in']}")
    return out
