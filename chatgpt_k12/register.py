# -*- coding: utf-8 -*-
"""ChatGPT account registration via Auth0/PKCE signup flow.

Adapted from akihitohyh/chatgpt-register-sub2api PlatformRegistrar,
with local integrations:
  - Email pool: data/hotmail_pool.txt (hotmail_pool.py)
  - OTP retrieval: hotmail_pool.refresh_access_token + imap_fetch_recent
  - Proxy rotation: clash_proxy.py
  - Fingerprint: anti_detect.py

Flow (7 HTTP steps + 1 IMAP poll):
  1. GET  /authorize?...&screen_hint=signup      → Auth0 login page state
  2. POST /user/register {username, password}     → create Auth0 user
  3. POST /email-otp/send                          → trigger verification email
  4. IMAP poll hotmail → extract 6-digit OTP
  5. POST /email-otp/validate {otp}               → verify email
  6. POST /create_account {name, birthdate}        → finish, get callback code
  7. POST /oauth/token {code, code_verifier}       → access/refresh/id tokens
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import secrets
import string
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# -- Local integrations (lazy import to avoid hard dep at module load) -------

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_session(proxy_url: str, fingerprint: dict[str, Any] | None = None):
    """Create a curl_cffi session with Chrome TLS impersonation + proxy."""
    from curl_cffi import requests as cffi_requests  # type: ignore

    session = cffi_requests.Session(impersonate="chrome")
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://auth.openai.com",
        "Referer": "https://auth.openai.com/",
    }
    if fingerprint:
        ua = fingerprint.get("user_agent")
        if ua:
            headers["User-Agent"] = ua
        sec_ch = fingerprint.get("sec_ch_ua")
        if sec_ch:
            headers["sec-ch-ua"] = sec_ch
        headers["sec-ch-ua-platform"] = f'"{fingerprint.get("platform", "Windows")}"'
    session.headers.update(headers)
    return session


# -- PKCE --------------------------------------------------------------------

def _pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier + code_challenge (S256)."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


# -- Sentinel token (OpenAI anti-bot) ---------------------------------------

def _build_sentinel_token() -> str:
    """Build a minimal openai-sentinel-token.

    The real chatgpt2api registrar uses a JS-evaluated token from the
    Auth0 page. For the skeleton we use the known static fallback format
    that OpenAI's sentinel accepts for basic signup. This may need
    FlareSolverr or browser evaluation if OpenAI tightens checks.
    """
    payload = {
        "challenge": "",
        "lang": "en",
        "type": "js",
        "v": "1.0.0",
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "gAAAAAB" + base64.b64encode(raw).decode("ascii")


# -- Name / password generators ----------------------------------------------

_FIRST_NAMES = [
    "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia",
    "Lucas", "Isabella", "Mason", "Mia", "Logan", "Charlotte", "Alex", "Amelia",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Wilson", "Anderson", "Taylor", "Thomas",
]


def _gen_name() -> str:
    return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"


def _gen_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _gen_birthdate() -> str:
    year = random.randint(1975, 2000)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year:04d}-{month:02d}-{day:02d}"


# -- OTP extraction from hotmail IMAP ----------------------------------------

def _extract_openai_otp(messages: list[dict[str, str]]) -> str | None:
    """Extract OpenAI 6-digit verification code from IMAP messages."""
    for msg in messages:
        subject = msg.get("subject", "")
        text = msg.get("text", "")
        from_addr = msg.get("from", "")

        if "openai" not in from_addr.lower() and "openai" not in subject.lower():
            continue

        for pat in (
            r"\b(\d{6})\b",
            r"verification\s+code[:\s]*(\d{4,8})",
            r"code[:\s]+(\d{4,8})",
        ):
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1)
            m = re.search(pat, subject, re.I)
            if m:
                return m.group(1)
    return None


def wait_openai_otp(
    mail_row: dict[str, str],
    *,
    timeout: float = 120,
    poll_interval: float = 5.0,
    log: Any = None,
) -> str:
    """Poll hotmail IMAP until OpenAI OTP code arrives.

    Uses hotmail_pool.refresh_access_token + imap_fetch_recent.
    """
    import hotmail_pool

    log = log or print
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            access_token = hotmail_pool.refresh_access_token(mail_row)
            msgs = hotmail_pool.imap_fetch_recent(mail_row["email"], access_token)
            code = _extract_openai_otp(msgs)
            if code:
                log(f"  OTP found: {code}")
                return code
        except Exception as exc:
            log(f"  IMAP poll error: {exc}")
        time.sleep(poll_interval)
    raise TimeoutError(f"OTP not received within {timeout}s for {mail_row['email']}")


# -- Core registrar ----------------------------------------------------------

class ChatGPTRegistrar:
    """Register a single ChatGPT account and obtain OAuth tokens."""

    def __init__(self, config: dict[str, Any]):
        self.cfg = config
        self.oauth = config.get("oauth", {})
        self.auth_base = self.oauth.get("auth_base", "https://auth.openai.com")
        self.client_id = self.oauth.get("client_id", "app_2SKx67EdpoN0G6j64rFvigXD")
        self.redirect_uri = self.oauth.get(
            "redirect_uri", "https://platform.openai.com/auth/callback"
        )
        self.device_id = str(uuid.uuid4())

    def register(
        self,
        email: str,
        password: str | None = None,
        *,
        proxy_url: str = "",
        fingerprint: dict[str, Any] | None = None,
        mail_row: dict[str, str] | None = None,
        otp_timeout: float = 120,
        log: Any = None,
    ) -> dict[str, Any]:
        """Execute full registration flow.

        Returns dict with keys:
          email, password, access_token, refresh_token, id_token, name, created_at
        Raises RuntimeError on any step failure.
        """
        log = log or print
        password = password or _gen_password(self.cfg.get("registration", {}).get("password_len", 16))
        name = _gen_name()
        birthdate = _gen_birthdate()

        session = _make_session(proxy_url, fingerprint)
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)

        # Step 1: authorize → get login page state
        log(f"  [1/7] authorize for {email}")
        auth_url = f"{self.auth_base}/authorize"
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid email profile offline_access",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "screen_hint": "signup",
        }
        resp = session.get(auth_url, params=params, allow_redirects=True, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"authorize failed: {resp.status_code} {resp.text[:200]}")

        # Step 2: register user
        # Per open-reg-auto / chatgpt2api upstream: sentinel-token is NOT
        # required for the pure-protocol registration flow. The 6-step chain
        # (authorize → register → otp-send → otp-validate → create_account →
        # callback → token) works without it when using curl_cffi chrome
        # impersonation for TLS fingerprinting.
        log(f"  [2/7] register user")
        reg_url = f"{self.auth_base}/api/accounts/user/register"
        resp = session.post(
            reg_url,
            json={"username": email, "password": password},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"register failed: {resp.status_code} {resp.text[:300]}"
            )

        # Step 3: send email OTP
        log(f"  [3/7] send email OTP")
        otp_send_url = f"{self.auth_base}/api/accounts/email-otp/send"
        resp = session.post(otp_send_url, json={"email": email}, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"email-otp/send failed: {resp.status_code} {resp.text[:300]}"
            )

        # Step 4: poll IMAP for OTP code
        log(f"  [4/7] wait for OTP via IMAP (timeout={otp_timeout}s)")
        if not mail_row:
            raise RuntimeError("mail_row required for OTP retrieval")
        otp_code = wait_openai_otp(
            mail_row, timeout=otp_timeout,
            poll_interval=self.cfg.get("mail", {}).get("wait_interval", 5),
            log=log,
        )

        # Step 5: validate OTP
        log(f"  [5/7] validate OTP")
        otp_validate_url = f"{self.auth_base}/api/accounts/email-otp/validate"
        resp = session.post(
            otp_validate_url, json={"email": email, "code": otp_code}, timeout=30
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"email-otp/validate failed: {resp.status_code} {resp.text[:300]}"
            )

        # Step 6: create account → follow redirect chain to get callback code
        # Per open-reg-auto: create_account returns a redirect chain that
        # eventually hits platform.openai.com/auth/callback?code=XXX. We need
        # to capture that code without actually loading the callback page.
        log(f"  [6/7] create account")
        create_url = f"{self.auth_base}/api/accounts/create_account"
        resp = session.post(
            create_url,
            json={"name": name, "birthdate": birthdate},
            timeout=30,
            allow_redirects=False,
        )

        # Follow redirect chain manually to extract callback code
        auth_code = self._extract_auth_code(resp)

        # If not in first response, follow up to 5 redirects
        redirect_count = 0
        location = resp.headers.get("Location", "") or resp.headers.get("location", "")
        while not auth_code and location and redirect_count < 10:
            redirect_count += 1
            resp = session.get(location, allow_redirects=False, timeout=30)
            auth_code = self._extract_auth_code(resp)
            location = resp.headers.get("Location", "") or resp.headers.get("location", "")

        if not auth_code:
            raise RuntimeError(
                f"create_account: could not extract auth code. "
                f"Status={resp.status_code}, body={resp.text[:300]}"
            )

        # Step 7: exchange code for tokens
        log(f"  [7/7] exchange code for tokens")
        token_url = f"{self.auth_base}/oauth/token"
        resp = session.post(
            token_url,
            json={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "code_verifier": verifier,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"token exchange failed: {resp.status_code} {resp.text[:300]}"
            )

        tokens = resp.json()
        result = {
            "email": email,
            "password": password,
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "id_token": tokens.get("id_token", ""),
            "name": name,
            "birthdate": birthdate,
            "created_at": int(time.time()),
            "source_type": "registration",
        }
        log(f"  ✓ registered {email}")
        return result

    def _extract_auth_code(self, resp: Any) -> str | None:
        """Extract OAuth authorization code from create_account response."""
        # Try redirect Location header
        location = ""
        if hasattr(resp, "headers"):
            location = resp.headers.get("Location", "") or resp.headers.get("location", "")
        if location:
            parsed = urlparse(location)
            qs = parse_qs(parsed.query)
            codes = qs.get("code", [])
            if codes:
                return codes[0]

        # Try JSON body
        try:
            body = resp.json()
            for key in ("code", "authorization_code", "continue_code"):
                val = body.get(key)
                if val and isinstance(val, str):
                    return val
            # Some responses nest in continue_url
            cont = body.get("continue_url", "")
            if cont:
                parsed = urlparse(cont)
                qs = parse_qs(parsed.query)
                codes = qs.get("code", [])
                if codes:
                    return codes[0]
        except Exception:
            pass

        return None

    def refresh_token(self, refresh_token: str, proxy_url: str = "") -> dict[str, str]:
        """Refresh an expired access_token using the refresh_token."""
        session = _make_session(proxy_url)
        token_url = f"{self.auth_base}/oauth/token"
        resp = session.post(
            token_url,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "scope": "openid email profile offline_access",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"refresh failed: {resp.status_code} {resp.text[:300]}"
            )
        return resp.json()
