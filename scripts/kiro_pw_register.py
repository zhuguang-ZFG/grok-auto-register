#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kiro registration via real Playwright browser (anti-suspend).

Uses real Chromium with stealth + fingerprint overrides + warm-up behavior.
Community research: pure HTTP (KiroX_Cli) gets 100% suspended because TES
sees synthetic fingerprints. Real browser execution gives TES real signals.

Usage:
  python scripts/kiro_pw_register.py --n 1 --proxy http://127.0.0.1:7897
  python scripts/kiro_pw_register.py --n 1 --headless  # not recommended
"""
from __future__ import annotations

import asyncio
import hashlib
import imaplib
import json
import os
import re
import secrets
import socket
import string
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "side_pools" / "kirox-cli" / "work"
HOTMAIL = ROOT / "data" / "hotmail_pool.txt"
HOTMAIL_USED = ROOT / "data" / "hotmail_pool.used.txt"
KIRO_USED = WORK / "kiro_mail_used.txt"
OUT_DIR = ROOT / "side_pools" / "kirox-cli" / "output"
REG_OIDC = "https://oidc.us-east-1.amazonaws.com"
REG_SCOPES = [
    "codewhisperer:completions", "codewhisperer:analysis",
    "codewhisperer:conversations", "codewhisperer:transformations",
    "codewhisperer:taskassist",
]
REG_REDIRECT_URI = "http://127.0.0.1:3128"
KIRO_SIGNIN_URL = "https://app.kiro.dev/signin"
ISSUER_URL = "https://view.awsapps.com/start/"
FIRST_NAMES = ["James","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles","Christopher","Daniel","Matthew","Anthony","Mark","Steven","Andrew","Joshua","Kenneth","Kevin","Brian","Edward","Ronald","Timothy","Jason","Jeffrey","Ryan"]
LAST_NAMES = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson","White","Harris"]

import random as _random


def log(msg: str, level: str = "info"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def gen_password() -> str:
    """AWS Builder ID password: length + upper + lower + digit + special."""
    upper = _random.choice(string.ascii_uppercase)
    lower = "".join(_random.choice(string.ascii_lowercase) for _ in range(6))
    digits = "".join(_random.choice(string.digits) for _ in range(4))
    special = _random.choice("!@#$%")
    rest = "".join(_random.choice(string.ascii_letters + string.digits) for _ in range(4))
    chars = list(upper + lower + digits + special + rest)
    _random.shuffle(chars)
    # ensure leading letter (some forms dislike leading special)
    if not chars[0].isalpha():
        for i, c in enumerate(chars):
            if c.isalpha():
                chars[0], chars[i] = chars[i], chars[0]
                break
    return "".join(chars)


def gen_name() -> tuple[str, str]:
    return _random.choice(FIRST_NAMES), _random.choice(LAST_NAMES)


def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# --- IMAP OTP retrieval ------------------------------------------------------

def imap_get_otp(email_addr: str, password: str, client_id: str,
                 refresh_token: str, pre_count: int = 0,
                 timeout_s: int = 120) -> str | None:
    """Connect to Outlook IMAP via XOAUTH2, wait for AWS OTP email, extract 6-digit code."""
    import base64
    import urllib.request
    import urllib.parse

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            # Step 1: Refresh access_token from Microsoft
            token_data = urllib.parse.urlencode({
                "client_id": client_id,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
            }).encode()
            req = urllib.request.Request(
                "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                tok_json = json.loads(resp.read().decode())
            access_token = tok_json.get("access_token", "")
            if not access_token:
                log("IMAP: no access_token from refresh", "err")
                time.sleep(5)
                continue

            # Step 2: Build XOAUTH2 auth string
            auth_str = f"user={email_addr}\x01auth=Bearer {access_token}\x01\x01"
            auth_b64 = base64.b64encode(auth_str.encode()).decode()

            # Step 3: Connect IMAP
            imap = imaplib.IMAP4_SSL("outlook.office365.com", 993)
            imap.authenticate("XOAUTH2", lambda _: auth_str.encode())
            imap.select("INBOX", readonly=True)
            _, data = imap.search(None, "ALL")
            ids = data[0].split()
            for mid in reversed(ids[-5:]):
                _, msg_data = imap.fetch(mid, "(RFC822)")
                raw = msg_data[0][1]
                body = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                if "no-reply" in body.lower() or "signin.aws" in body.lower() or "verification" in body.lower():
                    codes = re.findall(r"\b(\d{6})\b", body)
                    if codes:
                        imap.logout()
                        return codes[-1]
            imap.logout()
        except Exception as e:
            log(f"IMAP retry: {e}", "dbg")
        time.sleep(5)
    return None


# --- Hotmail pool ------------------------------------------------------------

def load_skip() -> set[str]:
    skip: set[str] = set()
    for p in [HOTMAIL_USED, KIRO_USED]:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            email = line.split("----", 1)[0].strip().lower()
            if "@" in email:
                skip.add(email)
    att = WORK / "kiro_mail_attempts.txt"
    if att.is_file():
        for line in att.read_text(encoding="utf-8", errors="ignore").splitlines():
            em = line.split("\t", 1)[0].strip().lower()
            if "@" in em:
                skip.add(em)
    return skip


def pick_mails(n: int) -> list[dict[str, str]]:
    skip = load_skip()
    picked = []
    for line in HOTMAIL.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        if len(parts) != 4:
            continue
        email = parts[0].strip().lower()
        if email in skip:
            continue
        picked.append({"email": parts[0].strip(), "password": parts[1],
                       "client_id": parts[2], "refresh_token": parts[3]})
        if len(picked) >= n:
            break
    if len(picked) < n:
        raise SystemExit(f"not enough free hotmail: need {n}, got {len(picked)}")
    return picked


def mark_used(email: str):
    WORK.mkdir(parents=True, exist_ok=True)
    with KIRO_USED.open("a", encoding="utf-8") as f:
        f.write(email + "\n")
    with HOTMAIL_USED.open("a", encoding="utf-8") as f:
        f.write(email + "\n")


# --- Callback server --------------------------------------------------------

def start_callback_server() -> tuple[HTTPServer, dict]:
    """Local server on :3128 to receive OAuth callback."""
    state = {"code": ""}
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            code = qs.get("code", [""])[0]
            if code:
                state["code"] = code
                log("Callback code received", "ok")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>OK</h2></body></html>")
        def log_message(self, *a): pass
    # free port if busy
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", 3128))
        sock.close()
    except OSError:
        sock.close()
        import subprocess
        try:
            r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if ":3128" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    if pid.isdigit():
                        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        except Exception:
            pass
        time.sleep(1)
    srv = HTTPServer(("127.0.0.1", 3128), H)
    srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    log("Callback server on :3128", "ok")
    return srv, state


# --- Fingerprint config + script --------------------------------------------

def gen_fp_config() -> dict:
    w = _random.choice([1366, 1440, 1536, 1600, 1920])
    h = {1366: 768, 1440: 900, 1536: 864, 1600: 900, 1920: 1080}[w]
    return {
        "user_agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_random.randint(128,142)}.0.0.0 Safari/537.36",
        "locale": "en-US",
        "timezone": "America/New_York",
        "viewport": {"width": w, "height": h},
        "screen": {"width": w, "height": h},
        "hardware_concurrency": _random.choice([4, 6, 8, 12, 16]),
        "device_memory": _random.choice([4, 8, 16, 32]),
        "color_depth": 24,
        "pixel_ratio": _random.choice([1.0, 1.25, 1.5, 2.0]),
        "max_touch_points": 0,
        "platform": "Win32",
        "canvas_noise": secrets.token_hex(4),
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": _random.choice([
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0)",
        ]),
    }


def build_fp_script(fp: dict) -> str:
    return """() => {
        const config = """ + json.dumps(fp) + """;
        Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>config.hardware_concurrency});
        Object.defineProperty(navigator,'deviceMemory',{get:()=>config.device_memory});
        Object.defineProperty(navigator,'platform',{get:()=>config.platform});
        Object.defineProperty(navigator,'languages',{get:()=>[config.locale,config.locale.split('-')[0]]});
        Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
        Object.defineProperty(navigator,'maxTouchPoints',{get:()=>config.max_touch_points});
        Object.defineProperty(screen,'width',{get:()=>config.screen.width});
        Object.defineProperty(screen,'height',{get:()=>config.screen.height});
        Object.defineProperty(screen,'colorDepth',{get:()=>config.color_depth});
        Object.defineProperty(window,'devicePixelRatio',{get:()=>config.pixel_ratio});
        const gp1=WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter=function(p){if(p===37445)return config.webgl_vendor;if(p===37446)return config.webgl_renderer;return gp1.call(this,p);};
        const gp2=WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter=function(p){if(p===37445)return config.webgl_vendor;if(p===37446)return config.webgl_renderer;return gp2.call(this,p);};
        const tdO=HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL=function(t){const c=this.getContext('2d');if(c){const n=parseInt(config.canvas_noise,16);const d=c.getImageData(0,0,Math.min(this.width,2),Math.min(this.height,2));for(let i=0;i<d.data.length;i+=4){d.data[i]=(d.data[i]+(n>>(i%8))%3)&0xFF;}c.putImageData(d,0,0);}return tdO.call(this,t);};
        const agO=AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData=function(ch){const d=agO.call(this,ch);if(d.length>0){const n=parseInt(config.canvas_noise.slice(0,4),16)/65536;for(let i=0;i<Math.min(d.length,10);i++){d[i]+=(n*0.0000001);}}return d;};
        const pN=Performance.prototype.now;Performance.prototype.now=function(){return pN.call(this)+Math.random()*0.1;};
    }"""


# --- Registration -----------------------------------------------------------

async def register_one(account: dict[str, str], proxy: str, headless: bool = False) -> dict[str, Any]:
    """Full registration flow with real browser. Returns result dict."""
    from playwright.async_api import async_playwright

    email = account["email"]
    fname, lname = gen_name()
    password = gen_password()
    fp = gen_fp_config()

    log(f"Registering {email} (headless={headless})")

    # Phase 1: OIDC client registration via httpx
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
    state_val = secrets.token_urlsafe(32)

    client = httpx.Client(verify=False, timeout=30,
                          proxy=proxy if proxy else None)
    reg_resp = client.post(f"{REG_OIDC}/client/register", json={
        "clientName": "Kiro IDE", "clientType": "public",
        "grantTypes": ["authorization_code", "refresh_token"],
        "issuerUrl": ISSUER_URL,
        "redirectUris": [REG_REDIRECT_URI], "scopes": REG_SCOPES,
    })
    reg = reg_resp.json()
    if "clientId" not in reg:
        return {"email": email, "status": "failed", "error": f"OIDC failed: {reg}"}
    client_id = reg["clientId"]
    client_secret = reg["clientSecret"]
    log("OIDC client registered")

    signin_url = f"{KIRO_SIGNIN_URL}?" + urlencode({
        "state": state_val,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "redirect_uri": REG_REDIRECT_URI,
        "redirect_from": "KiroIDE",
    })

    # Phase 2: callback server
    srv, cb_state = start_callback_server()

    # Phase 3: Playwright browser — NO explicit proxy (TUN handles routing)
    # The kiro.dev SPA fails to load CDN assets through explicit Clash proxy.
    # TUN mode already routes all browser traffic correctly.
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-first-run",
        f"--window-size={fp['screen']['width']},{fp['screen']['height']}",
        "--disable-background-timer-throttling",
    ]
    if headless:
        launch_args += ["--disable-gpu", "--no-sandbox"]
    launch_kwargs = {"headless": headless, "args": launch_args}
    # NO explicit proxy for browser — Clash TUN already routes all traffic.
    # Explicit proxy breaks kiro.dev SPA CDN asset loading.

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            viewport=fp["viewport"], screen=fp["screen"],
            locale=fp["locale"], timezone_id=fp["timezone"],
            user_agent=fp["user_agent"], color_scheme="light",
            device_scale_factor=fp["pixel_ratio"],
        )
        page = await context.new_page()
        # NO playwright-stealth — it breaks kiro.dev SPA ("Cannot assign to read only property 'createElement'").
        # Just hide webdriver flag via init script.
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        await context.add_init_script(build_fp_script(fp))

        log("Navigating to signin...")
        await page.goto(signin_url, timeout=60000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        await asyncio.sleep(5)

        # Accept cookies if present (English + Chinese)
        try:
            for sel in ['button:has-text("Accept")', 'button:has-text("接受")',
                        'button:has-text("Decline")', 'button:has-text("拒绝")']:
                try:
                    accept = page.locator(sel).first
                    if await accept.is_visible(timeout=2000):
                        await accept.click()
                        log(f"Cookie: {sel}")
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue
        except Exception:
            pass

        # Click "Builder ID Sign in" button (NOT "Create/signup")
        log("Clicking Builder ID sign in...")
        clicked = False
        for sel in ['button:has-text("Builder ID")', 'text=Builder ID']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.click()
                    clicked = True
                    log("Clicked Builder ID")
                    break
            except Exception:
                continue

        # Wait for signin/callback on our local server (kiro.dev redirects here)
        log("Waiting for signin callback...")
        signin_params = {}
        for _ in range(20):
            url = page.url
            if "127.0.0.1:3128" in url or "localhost:3128" in url:
                parsed = urlparse(url)
                qs = parse_qs(parsed.query)
                signin_params = {k: v[0] for k, v in qs.items()}
                log(f"Signin callback: {list(signin_params.keys())}")
                break
            await asyncio.sleep(2)

        # Now navigate browser to OIDC authorize URL (like kiro-register-en does)
        if signin_params and not cb_state["code"]:
            authorize_url = f"{REG_OIDC}/authorize?" + urlencode({
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REG_REDIRECT_URI,
                "scopes": ",".join(REG_SCOPES),
                "state": state_val,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            })
            log("Navigating to OIDC authorize...")
            await page.goto(authorize_url, timeout=60000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
            except Exception:
                pass
            await asyncio.sleep(3)

        # Wait until we land on signin.aws or profile.aws
        log("Waiting for signin.aws or profile.aws...")
        for _ in range(15):
            url = page.url
            if "signin.aws" in url or "profile.aws" in url:
                break
            await asyncio.sleep(2)

        url = page.url
        log(f"Current url: {url[:80]}")
        if "profile.aws" not in url and "signin.aws" not in url and not cb_state["code"]:
            await browser.close()
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
            return {"email": email, "status": "failed", "error": f"no profile.aws, at {url[:60]}"}

        # CRITICAL: Dismiss cookie banner BEFORE interacting with page
        # signin.aws shows Chinese cookie consent that blocks all buttons
        log("Dismissing cookies before interaction...")
        for attempt in range(3):
            # Try clicking cookie buttons
            for sel in [
                'button:has-text("Accept")', 'button:has-text("Decline")',
                'button:has-text("接受")', 'button:has-text("拒绝")',
                'button:has-text("关闭")',
            ]:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        log(f"Cookie dismissed: {sel}")
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue
            # Also force-remove the cookie overlay via JS (belt + suspenders)
            try:
                await page.evaluate("""() => {
                    const els = document.querySelectorAll(
                        '[id*="awsccc"], [class*="awsccc"], [data-testid*="cookie"], '
                        + '#awsccc-sb-ux-c, #cookie-banner, .aws-cookie'
                    );
                    els.forEach(e => e.remove());
                }""")
            except Exception:
                pass
            await asyncio.sleep(1)

        # WARM-UP: mouse movements + scrolling (anti-suspend key!)
        log("Warm-up: simulating human browsing...")
        try:
            vp = page.viewport_size
            for _ in range(3):
                await page.mouse.move(
                    _random.randint(100, vp["width"] - 100),
                    _random.randint(100, vp["height"] - 100),
                    steps=_random.randint(10, 25),
                )
                await asyncio.sleep(_random.uniform(0.3, 0.8))
            await page.mouse.wheel(0, _random.randint(50, 150))
            await asyncio.sleep(_random.uniform(0.5, 1.0))
            await page.mouse.wheel(0, -_random.randint(30, 80))
            await asyncio.sleep(0.5)
        except Exception:
            pass

        # Enter email (works on both signin.aws and profile.aws)
        log("Entering email...")
        try:
            # signin.aws uses different selectors than profile.aws
            email_input = None
            for sel in ['input[type="email"]', 'input[name="email"]',
                        'input[placeholder*="mail"]', 'input[type="text"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=5000):
                        email_input = el
                        break
                except Exception:
                    continue
            if email_input:
                await email_input.click()
                await asyncio.sleep(0.5)
                await email_input.type(email, delay=_random.randint(50, 120))
                await asyncio.sleep(1)
                # Submit: try Enter, then find button
                submitted = False
                for sel in ['button[type="submit"]', 'button:has-text("Next")',
                            'button:has-text("Continue")', 'button:has-text("Submit")',
                            'input[type="submit"]']:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=2000):
                            await btn.click()
                            submitted = True
                            break
                    except Exception:
                        continue
                if not submitted:
                    await page.keyboard.press("Enter")
                await asyncio.sleep(5)
                log("Email submitted")
            else:
                log("No email input found!", "err")
        except Exception as e:
            log(f"Email step error: {e}", "err")

        # After email submit: dismiss cookies + wait for profile.aws SPA render
        log("Post-email: dismissing cookies + waiting for SPA...")
        await asyncio.sleep(3)
        for attempt in range(3):
            # Force-remove cookie overlay
            try:
                await page.evaluate("""() => {
                    document.querySelectorAll(
                        '[id*="awsccc"], [class*="awsccc"], [data-testid*="cookie"], '
                        + '#awsccc-sb-ux-c, #cookie-banner'
                    ).forEach(e => e.remove());
                }""")
            except Exception:
                pass
            for sel in ['button:has-text("Accept")', 'button:has-text("Decline")', 'button:has-text("Dismiss")',
                    'button:has-text("接受")', 'button:has-text("拒绝")', 'button:has-text("关闭")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue
            await asyncio.sleep(2)
        log(f"After cookies: {page.url[:80]}")
        await asyncio.sleep(8)  # longer wait for SPA render

        # Dismiss cookies AGAIN if profile.aws SPA re-triggered them
        for sel in ['button:has-text("Accept")', 'button:has-text("Decline")',
                    'button:has-text("接受")', 'button:has-text("拒绝")']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue

        # After email submit + cookies dismissed, profile.aws SPA renders a NAME input
        # (placeholder="Maria José Silva"). Must fill name + submit to trigger OTP send.
        # Align with kiro-register-en: verify input_value, JS-click Continue, retry until leave NAME.
        log("Post-cookie: looking for name input on profile.aws...")
        await asyncio.sleep(3)
        full_name = f"{fname} {lname}"
        name_submitted = False

        async def _click_continue_js() -> bool:
            try:
                return bool(await page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                    // Prefer confirm-name / continue over edit / cookie chrome
                    const prefer = ['confirm your name', 'confirm name', 'confirm', 'continue',
                                    'next', 'submit', 'verify', '继续', '下一步', '确认'];
                    const skip = ['cookie', 'preference', 'edit my name', 'edit', 'change', '修改'];
                    for (const key of prefer) {
                        for (const b of visible) {
                            const t = (b.innerText || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                            if (skip.some(s => t.includes(s) && !t.includes('confirm'))) continue;
                            if (t.includes(key)) { b.click(); return true; }
                        }
                    }
                    for (let i = visible.length - 1; i >= 0; i--) {
                        const t = (visible[i].innerText || '').toLowerCase();
                        if (skip.some(s => t.includes(s))) continue;
                        if (!t) continue;
                        visible[i].click(); return true;
                    }
                    return false;
                }"""))
            except Exception:
                return False

        for attempt in range(10):
            try:
                # Confirm-name interstitial (no text input; buttons Edit/Confirm)
                page_btns = []
                try:
                    page_btns = await page.evaluate("""() => Array.from(document.querySelectorAll('button'))
                        .filter(b => b.offsetWidth > 0).map(b => (b.innerText||'').trim())""")
                except Exception:
                    page_btns = []
                joined = " | ".join(page_btns).lower()
                if "confirm your name" in joined or "confirm name" in joined:
                    log(f"Confirm-name screen buttons={page_btns}")
                    # Prefer exact Confirm, never Edit
                    confirmed = False
                    for sel in [
                        'button:has-text("Confirm your name")',
                        'button:has-text("Confirm name")',
                        'button:has-text("Confirm")',
                        'button:has-text("确认")',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            if await btn.is_visible(timeout=1500):
                                txt = (await btn.text_content() or "").strip()
                                if "edit" in txt.lower():
                                    continue
                                await btn.click()
                                confirmed = True
                                log(f"Clicked confirm-name: {txt[:40]}")
                                break
                        except Exception:
                            continue
                    if not confirmed:
                        await _click_continue_js()
                    await asyncio.sleep(4)
                    url_now = page.url
                    if any(k in url_now for k in ("verify", "otp", "enter-code", "password")):
                        name_submitted = True
                        log(f"Confirm-name advanced → {url_now[:90]}", "ok")
                        break
                    # check OTP input appeared even if hash lags
                    try:
                        if await page.locator('input[inputmode="numeric"], input[autocomplete="one-time-code"]').first.is_visible(timeout=1000):
                            name_submitted = True
                            log("Confirm-name advanced (OTP visible)", "ok")
                            break
                    except Exception:
                        pass
                    continue

                # Still on email step? re-fill email and continue
                email_cnt = await page.locator('input[type="email"]').count()
                if "#/signup/enter-email" in page.url or email_cnt > 0:
                    email_loc = page.locator('input[type="email"], input[type="text"]').first
                    if await email_loc.is_visible(timeout=1500):
                        val = ""
                        try:
                            val = await email_loc.input_value()
                        except Exception:
                            pass
                        ph = (await email_loc.get_attribute("placeholder") or "")
                        if "Silva" not in ph:
                            if email.lower() not in (val or "").lower():
                                await email_loc.click()
                                await email_loc.fill("")
                                await email_loc.type(email, delay=_random.randint(40, 90))
                            await _click_continue_js()
                            await asyncio.sleep(3)
                            log(f"Email re-submit attempt {attempt+1}, url={page.url[:90]}")
            except Exception as e:
                log(f"pre-name attempt {attempt+1}: {e}", "dbg")

            name_field = page.locator(
                'input[placeholder*="Silva"], input[placeholder*="name" i], '
                'input[aria-label*="name" i], input[type="text"]'
            )
            try:
                if await name_field.count() == 0 or not await name_field.first.is_visible(timeout=2000):
                    # maybe confirm screen without input
                    if "confirm" in joined:
                        continue
                    await asyncio.sleep(2)
                    continue
                nf = name_field.first
                ph = (await nf.get_attribute("placeholder") or "") + (await nf.get_attribute("type") or "")
                if "mail" in ph.lower() and "Silva" not in ph:
                    await asyncio.sleep(1)
                    continue
                await nf.click()
                await asyncio.sleep(0.2)
                try:
                    await nf.fill("")
                except Exception:
                    pass
                await nf.type(full_name, delay=_random.randint(40, 100))
                await asyncio.sleep(0.5)
                filled_val = ""
                try:
                    filled_val = await nf.input_value()
                except Exception:
                    pass
                if filled_val.strip() != full_name:
                    log(f"Name value mismatch got={filled_val!r}, retry type", "dbg")
                    await nf.fill(full_name)
                log(f"Name filled: {full_name}")
                for _sub in range(4):
                    # try Confirm first, then continue
                    for sel in [
                        'button:has-text("Confirm your name")',
                        'button:has-text("Confirm")',
                        'button:has-text("Continue")',
                        'button:has-text("继续")',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            if await btn.is_visible(timeout=800):
                                txt = (await btn.text_content() or "").strip()
                                if "edit" in txt.lower() or "cookie" in txt.lower():
                                    continue
                                await btn.click()
                                log(f"Name click: {txt[:40]}")
                                break
                        except Exception:
                            continue
                    else:
                        await _click_continue_js()
                    await asyncio.sleep(4)
                    url_now = page.url
                    still_name = False
                    try:
                        still_name = await page.locator('input[placeholder*="Silva"]').first.is_visible(timeout=800)
                    except Exception:
                        still_name = False
                    if any(k in url_now for k in ("enter-name", "verify", "otp", "enter-code")):
                        name_submitted = True
                        log(f"Name submitted → {url_now[:90]}", "ok")
                        break
                    if not still_name:
                        # confirm interstitial or OTP
                        name_submitted = True
                        log(f"Name field gone → {url_now[:90]}", "ok")
                        break
                    log(f"Name still on page after click {_sub+1}, url={url_now[:90]}", "dbg")
                if name_submitted:
                    # one more confirm pass if needed
                    try:
                        page_btns2 = await page.evaluate("""() => Array.from(document.querySelectorAll('button'))
                            .filter(b => b.offsetWidth > 0).map(b => (b.innerText||'').trim())""")
                        if any("confirm" in (b or "").lower() for b in page_btns2):
                            for sel in ['button:has-text("Confirm your name")', 'button:has-text("Confirm")']:
                                try:
                                    btn = page.locator(sel).first
                                    if await btn.is_visible(timeout=1000):
                                        await btn.click()
                                        log("Post-name Confirm clicked")
                                        await asyncio.sleep(3)
                                        break
                                except Exception:
                                    continue
                    except Exception:
                        pass
                    break
            except Exception as e:
                log(f"Name step attempt {attempt+1}: {e}", "dbg")
            await asyncio.sleep(2)

        if not name_submitted:
            log(f"Name submit uncertain, continuing; url={page.url[:100]}", "dbg")

        # OTP input search — profile.aws uses various OTP input patterns
        log("Waiting for OTP page...")
        otp_input = None
        otp_sels = [
            'input[inputmode="numeric"]', 'input[autocomplete="one-time-code"]',
            'input[name*="otp"]', 'input[name*="code"]', 'input[name*="OTP"]',
            'input[placeholder*="digit"]', 'input[placeholder*="verification"]',
            'input[placeholder*="code"]', 'input[placeholder*="Code"]',
            'input[placeholder*="数字"]', 'input[placeholder*="验证"]',
            'input[aria-label*="code"]', 'input[aria-label*="OTP"]',
            'input[maxlength="6"]', 'input[pattern*="[0-9]"]',
            'input[data-testid*="code"]', 'input[data-testid*="otp"]',
        ]
        for tick in range(35):
            # re-dismiss cookie that may reappear
            try:
                await page.evaluate("""() => {
                    document.querySelectorAll('[id*="awsccc"], [class*="awsccc"]').forEach(e => e.remove());
                }""")
            except Exception:
                pass
            for sel in otp_sels:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=800):
                        otp_input = el
                        log(f"OTP input found: {sel}")
                        break
                except Exception:
                    continue
            if otp_input:
                break
            # fallback: single visible text/tel/number that is not name/email
            try:
                all_inp = page.locator('input:not([type="hidden"]):not([type="password"]):not([type="email"])')
                cnt = await all_inp.count()
                candidates = []
                for i in range(cnt):
                    inp = all_inp.nth(i)
                    if not await inp.is_visible():
                        continue
                    ph = (await inp.get_attribute("placeholder") or "")
                    if "Silva" in ph or "mail" in ph.lower() or "name" in ph.lower():
                        continue
                    itype = (await inp.get_attribute("type") or "text").lower()
                    if itype in ("text", "tel", "number", ""):
                        candidates.append(inp)
                if len(candidates) == 1:
                    otp_input = candidates[0]
                    log("OTP input found via single-text fallback")
                    break
            except Exception:
                pass
            # confirm-name interstitial during OTP wait
            try:
                for sel in [
                    'button:has-text("Confirm your name")',
                    'button:has-text("Confirm name")',
                    'button:has-text("Confirm")',
                ]:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=400):
                        txt = (await btn.text_content() or "").strip()
                        if "edit" in txt.lower() or "cookie" in txt.lower():
                            continue
                        await btn.click()
                        log(f"OTP wait: clicked {txt[:40]}")
                        await asyncio.sleep(3)
                        break
            except Exception:
                pass
            # still on name? re-submit
            try:
                ni = page.locator('input[placeholder*="Silva"]').first
                if await ni.is_visible(timeout=500):
                    cur = ""
                    try:
                        cur = await ni.input_value()
                    except Exception:
                        pass
                    if full_name not in (cur or ""):
                        await ni.fill(full_name)
                    await _click_continue_js()
                    log(f"OTP wait: re-clicked continue on name, url={page.url[:80]}", "dbg")
            except Exception:
                pass
            if tick in (5, 15, 25):
                log(f"OTP wait tick={tick} url={page.url[:100]}", "dbg")
            await asyncio.sleep(2)

        if not otp_input:
            try:
                u = page.url
                dump = await page.evaluate("""() => ({
                    title: document.title,
                    btns: Array.from(document.querySelectorAll('button')).filter(b=>b.offsetWidth>0)
                        .map(b=>(b.innerText||'').trim().slice(0,40)),
                    inputs: Array.from(document.querySelectorAll('input:not([type="hidden"])'))
                        .filter(i=>i.offsetWidth>0)
                        .map(i=>`${i.type}:${i.name||i.placeholder||i.id||''}`.slice(0,50)),
                })""")
                log(f"OTP miss url={u[:120]} dump={dump}", "err")
                shot = WORK / f"otp_miss_{int(time.time())}.png"
                await page.screenshot(path=str(shot), full_page=True)
                log(f"OTP miss screenshot={shot}", "err")
            except Exception as e:
                log(f"OTP miss dump failed: {e}", "dbg")
            await browser.close()
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
            return {"email": email, "status": "failed", "error": "no OTP input found"}

        # Fetch OTP from IMAP
        log("Fetching OTP from IMAP...")
        otp = imap_get_otp(email, account["password"], account["client_id"],
                           account["refresh_token"], timeout_s=90)
        if not otp:
            await browser.close()
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
            return {"email": email, "status": "failed", "error": "OTP not received"}
        log(f"OTP: {otp}")

        # Type OTP with human delays
        await otp_input.click()
        await asyncio.sleep(0.5)
        for digit in otp:
            await page.keyboard.type(digit, delay=_random.randint(150, 350))
            await asyncio.sleep(_random.uniform(0.1, 0.3))
        await asyncio.sleep(1)
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass
        # click Continue if OTP page has it
        try:
            for sel in ['button:has-text("Continue")', 'button:has-text("Verify")', 'button[type="submit"]']:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    txt = (await btn.text_content() or "").lower()
                    if "cookie" in txt or "resend" in txt:
                        continue
                    await btn.click()
                    break
        except Exception:
            pass
        await asyncio.sleep(3)

        # Password step — React-controlled inputs need native value setter (community pattern)
        log("Setting password...")
        try:
            pwd_ready = False
            for _wait in range(25):
                cnt = await page.evaluate("""() => Array.from(document.querySelectorAll('input[type="password"]'))
                    .filter(el => el.offsetWidth > 0).length""")
                if cnt >= 1:
                    pwd_ready = True
                    log(f"Password fields visible count={cnt}")
                    break
                # still on OTP? resubmit enter
                try:
                    if await page.locator('input[placeholder*="digit"], input[inputmode="numeric"]').first.is_visible(timeout=400):
                        await page.keyboard.press("Enter")
                except Exception:
                    pass
                await asyncio.sleep(1)
            if not pwd_ready:
                log(f"Password fields never appeared after OTP; url={page.url[:100]}", "err")
                await browser.close()
                try:
                    srv.shutdown()
                except Exception:
                    pass
                try:
                    srv.server_close()
                except Exception:
                    pass
                return {"email": email, "status": "failed", "error": "no password fields after OTP"}
            for attempt in range(6):
                filled = await page.evaluate("""(pwd) => {
                    const inputs = Array.from(document.querySelectorAll('input[type="password"]'))
                        .filter(el => el.offsetWidth > 0);
                    if (inputs.length === 0) return 0;
                    const ns = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    function setVal(el, val) {
                        el.focus();
                        ns.call(el, val);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
                        el.blur();
                    }
                    inputs.forEach(el => setVal(el, pwd));
                    return inputs.length;
                }""", password)
                log(f"Password filled fields={filled} attempt={attempt+1}")
                if filled < 1:
                    pwds = page.locator('input[type="password"]')
                    count = await pwds.count()
                    for i in range(min(count, 2)):
                        await pwds.nth(i).click()
                        await pwds.nth(i).fill("")
                        await pwds.nth(i).type(password, delay=_random.randint(30, 70))
                # also human-type into each field if lens still 0
                vals0 = await page.evaluate("""() => Array.from(document.querySelectorAll('input[type="password"]'))
                    .filter(el => el.offsetWidth > 0).map(el => (el.value||'').length)""")
                if any(v == 0 for v in (vals0 or [0])):
                    pwds = page.locator('input[type="password"]')
                    count = await pwds.count()
                    for i in range(min(count, 2)):
                        el = pwds.nth(i)
                        if not await el.is_visible():
                            continue
                        await el.click()
                        await el.fill("")
                        await el.type(password, delay=_random.randint(40, 90))
                await asyncio.sleep(0.8)
                # uncheck any leftover marketing checkbox if it blocks submit
                try:
                    await page.evaluate("""() => {
                        document.querySelectorAll('input[type="checkbox"]').forEach(c => {
                            // leave as-is; do not force marketing
                        });
                    }""")
                except Exception:
                    pass
                # dump validation hints
                try:
                    hints = await page.evaluate("""() => Array.from(document.querySelectorAll(
                        '[role="alert"], [class*="error"], [class*="Error"], [class*="invalid"], p, span, li'
                    )).map(e => (e.innerText||'').trim()).filter(t => t && t.length < 120)
                      .filter(t => /password|require|invalid|must|至少|密码|weak|match|special|digit|upper|lower/i.test(t))
                      .slice(0, 8)""")
                    if hints:
                        log(f"Password hints: {hints}", "dbg")
                except Exception:
                    pass
                # submit Continue
                await page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                    for (const b of visible) {
                        const t = (b.innerText || '').toLowerCase();
                        if (t.includes('cookie') || t.includes('preference')) continue;
                        if (t.includes('continue') || t.includes('next') || t.includes('submit')
                            || t.includes('create') || t.includes('继续')) {
                            b.click(); return;
                        }
                    }
                }""")
                # also try enabled submit only
                try:
                    btn = page.locator('button:has-text("Continue")').first
                    if await btn.is_visible(timeout=1000):
                        disabled = await btn.get_attribute("disabled")
                        aria = await btn.get_attribute("aria-disabled")
                        if disabled is None and aria != "true":
                            await btn.click()
                except Exception:
                    pass
                await asyncio.sleep(5)
                still_pwd = False
                try:
                    still_pwd = await page.locator('input[type="password"]').first.is_visible(timeout=1000)
                except Exception:
                    still_pwd = False
                if not still_pwd:
                    log("Password set (fields gone)", "ok")
                    break
                vals = await page.evaluate("""() => Array.from(document.querySelectorAll('input[type="password"]'))
                    .filter(el => el.offsetWidth > 0).map(el => (el.value||'').length)""")
                log(f"Password still visible value_lens={vals} url={page.url[:90]}", "dbg")
                if attempt == 2:
                    # regenerate stronger password once
                    password = gen_password()
                    log(f"Rotating password mid-step", "dbg")
            log("Password step done")
        except Exception as e:
            log(f"Password step error: {e}", "err")

        # Wait for callback (SSO: password → consent → authorize → :3128?code=)
        # Align with kiro-register-en: parse code from page.url + JS consent clicks.
        def _code_from_url(u: str) -> str:
            """Only accept OAuth callback URLs — not random profile.aws query params."""
            if not u:
                return ""
            if "code_challenge" in u:
                return ""
            if "code=" not in u:
                return ""
            try:
                parsed = urlparse(u)
                host = (parsed.hostname or "").lower()
                # Real callback lands on local server, or rare intermediate with code on oidc
                if host not in ("127.0.0.1", "localhost") and "oidc." not in host:
                    return ""
                qs = parse_qs(parsed.query)
                code_val = qs.get("code", [""])[0]
                if code_val and len(code_val) > 20:
                    return code_val
            except Exception:
                pass
            return ""

        async def _has_password_fields() -> bool:
            try:
                return bool(await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('input[type="password"]'))
                        .some(el => el.offsetWidth > 0);
                }"""))
            except Exception:
                return False

        async def _ensure_password_submitted() -> bool:
            """If still on password form, re-fill + click Continue."""
            if not await _has_password_fields():
                return False
            log("Still on password form — re-submitting", "dbg")
            await page.evaluate("""(pwd) => {
                const inputs = Array.from(document.querySelectorAll('input[type="password"]'))
                    .filter(el => el.offsetWidth > 0);
                const ns = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                inputs.forEach(el => {
                    ns.call(el, pwd);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                });
            }""", password)
            await asyncio.sleep(0.4)
            await page.evaluate("""() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                for (const b of visible) {
                    const t = (b.innerText || '').toLowerCase();
                    if (t.includes('cookie') || t.includes('preference')) continue;
                    if (t.includes('continue') || t.includes('next') || t.includes('submit')
                        || t.includes('create') || t.includes('继续')) {
                        b.click(); return;
                    }
                }
            }""")
            await asyncio.sleep(3)
            return True

        async def _click_consent_js() -> bool:
            # Never treat password-page Continue as consent.
            if await _has_password_fields():
                return False
            try:
                return bool(await page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button, a[role="button"], input[type="submit"]'));
                    const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                    // Consent is Allow/Authorize — NOT bare Continue on signup password
                    const keywords = ['allow', 'authorize', 'accept access', 'grant',
                                      '允许', '授权', '同意访问'];
                    for (const b of visible) {
                        const t = ((b.innerText || b.value || b.getAttribute('aria-label') || '') + '').toLowerCase();
                        if (t.includes('cookie') || t.includes('preference') || t.includes('edit')) continue;
                        if (keywords.some(k => t.includes(k))) {
                            b.click();
                            return true;
                        }
                    }
                    // awsapps consent often has Allow as primary
                    if (location.hostname.includes('awsapps.com') && visible.length > 0) {
                        for (const b of visible) {
                            const t = (b.innerText || '').toLowerCase();
                            if (t.includes('allow') || t.includes('authorize') || t.includes('accept') || t.includes('confirm')) {
                                b.click(); return true;
                            }
                        }
                    }
                    return false;
                }"""))
            except Exception:
                return False

        async def _dump_sso_debug(tag: str) -> None:
            try:
                u = page.url
                info = await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button, a[role="button"], input[type="submit"]'))
                        .filter(b => b.offsetWidth > 0 && b.offsetHeight > 0)
                        .map(b => (b.innerText || b.value || '').trim().slice(0, 40));
                    const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"])'))
                        .filter(i => i.offsetWidth > 0)
                        .map(i => `${i.type||'text'}:${i.name||i.placeholder||i.id||''}`.slice(0, 40));
                    return {btns, inputs, title: document.title};
                }""")
                log(f"SSO dump[{tag}] url={u[:120]} title={info.get('title','')[:40]}", "dbg")
                log(f"SSO dump[{tag}] buttons={info.get('btns', [])}", "dbg")
                log(f"SSO dump[{tag}] inputs={info.get('inputs', [])}", "dbg")
                shot = WORK / f"sso_debug_{int(time.time())}.png"
                await page.screenshot(path=str(shot), full_page=True)
                log(f"SSO dump[{tag}] screenshot={shot}", "dbg")
            except Exception as e:
                log(f"SSO dump failed: {e}", "dbg")

        log("Waiting for SSO callback or consent page...")
        auth_code = ""
        for i in range(45):
            if cb_state["code"]:
                auth_code = cb_state["code"]
                break
            url = page.url
            from_url = _code_from_url(url)
            if from_url:
                auth_code = from_url
                cb_state["code"] = from_url
                log(f"Auth code from page.url ({len(from_url)} chars)", "ok")
                break
            if "127.0.0.1:3128" in url or "localhost:3128" in url:
                from_url = _code_from_url(url) or (parse_qs(urlparse(url).query).get("code", [""])[0])
                if from_url and len(from_url) > 10:
                    auth_code = from_url
                    cb_state["code"] = from_url
                    log("Auth code from local callback URL", "ok")
                    break

            log(f"SSO wait[{i}]: {url[:90]}", "dbg")

            # Password form not finished — do NOT consent-click Continue
            if await _ensure_password_submitted():
                continue

            # Consent / Allow on oidc / awsapps only
            clicked = await _click_consent_js()
            if clicked:
                log("Consent JS click", "ok")
                await asyncio.sleep(3)
                continue

            # Locator fallback — never bare Continue while password fields exist
            if not await _has_password_fields():
                for sel in [
                    'button:has-text("Allow")', 'button:has-text("Authorize")',
                    'button:has-text("允许")', 'button:has-text("授权")',
                    'button:has-text("Accept")',
                ]:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=800):
                            txt = (await btn.text_content() or "").strip()
                            if "cookie" in txt.lower():
                                continue
                            log(f"Consent locator: '{txt[:30]}'")
                            await btn.click()
                            await asyncio.sleep(3)
                            break
                    except Exception:
                        continue

            # Landed on profile home without code — re-hit OIDC authorize (logged-in session)
            on_profile_home = "profile.aws" in url and ("/profile" in url or "details" in url)
            if i in (3, 8, 15, 25) and not cb_state["code"] and not auth_code:
                if not await _has_password_fields() and (
                    on_profile_home
                    or any(h in url for h in ("profile.aws", "signin.aws", "oidc.", "awsapps.com", "view.awsapps"))
                ):
                    log(f"Re-trying OIDC authorize (tick {i})...")
                    auth_url2 = f"{REG_OIDC}/authorize?" + urlencode({
                        "response_type": "code",
                        "client_id": client_id,
                        "redirect_uri": REG_REDIRECT_URI,
                        "scopes": ",".join(REG_SCOPES),
                        "state": state_val,
                        "code_challenge": code_challenge,
                        "code_challenge_method": "S256",
                    })
                    try:
                        await page.goto(auth_url2, timeout=30000)
                        await asyncio.sleep(3)
                    except Exception as e:
                        log(f"authorize retry: {e}", "dbg")

            if i in (10, 20, 35):
                await _dump_sso_debug(f"tick{i}")

            await asyncio.sleep(2)

        if not auth_code and not cb_state["code"]:
            await _dump_sso_debug("final")

        await browser.close()

    try:
        srv.shutdown()
    except Exception:
        pass
    try:
        srv.server_close()
    except Exception:
        pass

    if not cb_state["code"]:
        return {"email": email, "status": "failed", "error": "no callback code"}

    # Phase 4: Token exchange
    log("Exchanging tokens...")
    tokens: dict[str, Any] = {}
    last_err = ""
    for attempt in range(4):
        try:
            token_resp = client.post(f"{REG_OIDC}/token", json={
                "clientId": client_id, "clientSecret": client_secret,
                "grantType": "authorization_code",
                "code": cb_state["code"],
                "redirectUri": REG_REDIRECT_URI,
                "codeVerifier": code_verifier,
            }, headers={"Content-Type": "application/json"})
            tokens = token_resp.json()
            if "accessToken" in tokens:
                break
            last_err = f"token exchange failed: {tokens}"
            log(f"token exchange attempt {attempt+1}: {tokens}", "dbg")
        except Exception as e:
            last_err = str(e)
            log(f"token exchange attempt {attempt+1} err: {e}", "dbg")
        time.sleep(1.5 * (attempt + 1))
    if "accessToken" not in tokens:
        return {"email": email, "status": "failed", "error": last_err or "token exchange failed"}

    log("Tokens obtained!")

    # Also get AWS SSO token (device flow)
    aws_rt = tokens.get("refreshToken", "")
    result = {
        "email": email,
        "password": password,
        "status": "success",
        "refreshToken": aws_rt,
        "accessToken": tokens.get("accessToken", ""),
        "clientId": client_id,
        "clientSecret": client_secret,
        "region": "us-east-1",
        "expiresIn": tokens.get("expiresIn"),
    }
    return result


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Kiro Playwright registration")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--proxy", default="http://127.0.0.1:7897")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--output", default="")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.output or str(OUT_DIR / f"pw_results_{time.strftime('%Y%m%d_%H%M%S')}.json")

    mails = pick_mails(args.n)
    log(f"Picked {len(mails)} mail(s)")
    results = []

    for i, account in enumerate(mails):
        log(f"[{i+1}/{len(mails)}] {account['email']}")
        try:
            result = asyncio.run(register_one(account, args.proxy, args.headless))
            results.append(result)
            if result.get("status") == "success":
                log(f"SUCCESS: {result['email']}")
                mark_used(account["email"])
            else:
                log(f"FAILED: {result.get('error', 'unknown')}")
                with (WORK / "kiro_mail_attempts.txt").open("a", encoding="utf-8") as f:
                    f.write(f"{account['email']}\t{result.get('error','')[:60]}\t{time.strftime('%Y%m%d_%H%M%S')}\n")
        except Exception as e:
            log(f"Exception: {e}", "err")
            results.append({"email": account["email"], "status": "failed", "error": str(e)})
        # delay between registrations
        if i < len(mails) - 1:
            delay = _random.randint(10, 30)
            log(f"Waiting {delay}s before next...")
            time.sleep(delay)

    # Save results
    existing = []
    if Path(out_path).is_file():
        try:
            existing = json.loads(Path(out_path).read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.extend(results)
    Path(out_path).write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    success = sum(1 for r in results if r.get("status") == "success")
    log(f"Done: {success}/{len(results)} success → {out_path}")

    # Print for pipeline
    print(f"\nRESULTS_FILE={out_path}")
    print(f"SUCCESS={success}")
    print(f"TOTAL={len(results)}")


if __name__ == "__main__":
    main()
