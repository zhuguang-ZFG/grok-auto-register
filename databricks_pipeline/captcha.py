#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reCAPTCHA v2 detection, CapSolver solving, and token injection for DrissionPage."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

LogFn = Callable[[str], None]


CAPSOLVER_BASE = "https://api.capsolver.com"

# CapSolver task type names (as of 2026-07)
TASK_RECAPTCHA_V2 = "ReCaptchaV2TaskProxyLess"
TASK_RECAPTCHA_V2_ENTERPRISE = "ReCaptchaV2EnterpriseTaskProxyLess"


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)


# -----------------------------------------------------------------
# Detection
# -----------------------------------------------------------------


def _looks_like_recaptcha_sitekey(sk: str) -> bool:
    """Google reCAPTCHA sitekeys are typically 40 chars and start with 6L."""
    sk = (sk or "").strip()
    if len(sk) < 30 or len(sk) > 80:
        return False
    if sk.startswith("sha256-") or sk.startswith("sha384-") or sk.startswith("sha512-"):
        return False
    if " " in sk or "\n" in sk:
        return False
    # classic public sitekey prefix
    if sk.startswith("6L"):
        return True
    # rare variants — still allow if base64-ish and long enough
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{35,60}", sk)) and not sk.startswith("sha")


def detect_recaptcha(page: Any) -> Optional[Dict[str, Any]]:
    """
    Scan the page for Google reCAPTCHA v2 (invisible or visible checkbox).
    Returns dict with keys: sitekey, pageurl, is_enterprise, visible
    or None if no reCAPTCHA detected.
    """
    try:
        pageurl = str(page.url or "")
    except Exception:
        pageurl = ""

    try:
        html = page.html or ""
    except Exception:
        html = ""

    sitekey = ""
    is_enterprise = False
    visible = False

    # Prefer iframe src k= parameter (most reliable on SISU)
    try:
        for el in page.eles("css:iframe[src*='recaptcha']") or []:
            src = str(el.attr("src") or "")
            m = re.search(r"[?&]k=([^&]+)", src)
            if m:
                cand = urllib.parse.unquote(m.group(1))
                if _looks_like_recaptcha_sitekey(cand):
                    sitekey = cand
                    if "enterprise" in src:
                        is_enterprise = True
                    if "bframe" in src:
                        visible = True
                    break
    except Exception:
        pass

    # 1. Check for g-recaptcha element with data-sitekey
    if not sitekey:
        try:
            grecap = page.ele("css:.g-recaptcha", timeout=1)
            if grecap:
                sk = str(grecap.attr("data-sitekey") or "")
                if _looks_like_recaptcha_sitekey(sk):
                    sitekey = sk
                    visible = True
        except Exception:
            pass

    # 2. Check for invisible or any data-sitekey
    if not sitekey:
        try:
            for el in page.eles("css:[data-sitekey]") or []:
                sk = str(el.attr("data-sitekey") or "")
                if _looks_like_recaptcha_sitekey(sk):
                    sitekey = sk
                    sz = str(el.attr("data-size") or "")
                    visible = sz != "invisible"
                    break
        except Exception:
            pass

    # 3. JS walk ___grecaptcha_cfg for 6L* keys
    if not sitekey:
        try:
            sk = page.run_js(
                """
(() => {
  const out = [];
  const walk = (o, depth) => {
    if (!o || depth > 8) return;
    if (typeof o === 'string' && o.startsWith('6L') && o.length >= 35 && o.length <= 60) out.push(o);
    if (typeof o === 'object') {
      try { for (const k of Object.keys(o)) walk(o[k], depth + 1); } catch (e) {}
    }
  };
  try { if (window.___grecaptcha_cfg) walk(window.___grecaptcha_cfg, 0); } catch (e) {}
  return out[0] || '';
})()
"""
            )
            if _looks_like_recaptcha_sitekey(str(sk or "")):
                sitekey = str(sk)
        except Exception:
            pass

    # 4. Fallback: scan HTML for 6L sitekeys near recaptcha
    if not sitekey:
        for m in re.finditer(r"(6L[A-Za-z0-9_-]{30,50})", html):
            cand = m.group(1)
            if _looks_like_recaptcha_sitekey(cand):
                sitekey = cand
                break

    if not sitekey:
        # captcha chrome without key — still report for human gate
        if re.search(r"recaptcha|grecaptcha", html, re.I):
            return {
                "sitekey": "",
                "pageurl": pageurl,
                "is_enterprise": "enterprise" in html.lower(),
                "visible": False,
                "has_response_el": False,
                "has_challenge_iframe": bool(
                    re.search(r"recaptcha.*bframe|bframe.*recaptcha", html, re.I)
                ),
            }
        return None

    # Check enterprise indicator
    if "enterprise" in html.lower() or "recaptcha/enterprise" in html:
        is_enterprise = True

    has_response_el = False
    try:
        if page.ele("css:#g-recaptcha-response", timeout=0.5):
            has_response_el = True
    except Exception:
        pass

    return {
        "sitekey": sitekey,
        "pageurl": pageurl,
        "is_enterprise": is_enterprise,
        "visible": visible,
        "has_response_el": has_response_el,
        "has_challenge_iframe": visible,
    }


# -----------------------------------------------------------------
# CapSolver solving
# -----------------------------------------------------------------


def solve_recaptcha_capsolver(
    website_url: str,
    website_key: str,
    *,
    api_key: str,
    is_enterprise: bool = False,
    proxy: Optional[str] = None,
    log: Optional[LogFn] = None,
) -> str:
    """
    Solve reCAPTCHA v2 via CapSolver API.
    Returns the gRecaptchaResponse token.
    Raises on failure / timeout.
    """
    if not api_key:
        raise ValueError("CapSolver API key is required")
    if not website_key:
        raise ValueError("website_key (sitekey) is required")

    if not _looks_like_recaptcha_sitekey(website_key):
        raise ValueError(f"invalid recaptcha sitekey: {website_key[:40]!r}")

    # CapSolver is picky: long SISU querystrings often fail; prefer origin+path
    try:
        parsed = urllib.parse.urlparse(website_url)
        short_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
    except Exception:
        short_url = website_url
    urls_to_try = []
    for u in (short_url, website_url, "https://login.databricks.com/"):
        if u and u not in urls_to_try:
            urls_to_try.append(u)

    def _create_and_poll(task: Dict[str, Any]) -> str:
        _log(
            log,
            f"[captcha] CapSolver createTask type={task.get('type')} "
            f"sitekey={website_key[:16]}... url={str(task.get('websiteURL'))[:60]}",
        )
        create_payload = json.dumps({"clientKey": api_key, "task": task}).encode()
        req = urllib.request.Request(
            f"{CAPSOLVER_BASE}/createTask",
            data=create_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        if result.get("errorId", 0) != 0:
            raise RuntimeError(result.get("errorDescription", str(result)))
        task_id = result.get("taskId")
        if not task_id:
            raise RuntimeError(f"CapSolver no taskId: {result}")
        _log(log, f"[captcha] CapSolver task created id={str(task_id)[:12]}... polling")
        poll_payload = json.dumps({"clientKey": api_key, "taskId": task_id}).encode()
        for _i in range(60):
            time.sleep(2)
            req2 = urllib.request.Request(
                f"{CAPSOLVER_BASE}/getTaskResult",
                data=poll_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req2, timeout=15) as resp2:
                    status = json.loads(resp2.read())
            except Exception:
                continue
            if status.get("errorId", 0) != 0:
                raise RuntimeError(status.get("errorDescription", str(status)))
            if status.get("status") == "ready":
                sol = status.get("solution") or {}
                token = (
                    sol.get("gRecaptchaResponse")
                    or sol.get("token")
                    or sol.get("text")
                    or ""
                )
                token = str(token).strip()
                if len(token) < 40:
                    raise RuntimeError(f"CapSolver empty token len={len(token)}")
                _log(log, f"[captcha] CapSolver ready token_len={len(token)}")
                return token
            if status.get("status") == "failed":
                raise RuntimeError(status.get("errorDescription", str(status)))
        raise RuntimeError("CapSolver recaptcha timeout")

    # Task variants: enterprise + invisible flags matter for SISU
    variants: list[Dict[str, Any]] = []
    type_order = (
        [TASK_RECAPTCHA_V2_ENTERPRISE, TASK_RECAPTCHA_V2]
        if is_enterprise
        else [TASK_RECAPTCHA_V2, TASK_RECAPTCHA_V2_ENTERPRISE]
    )
    for url in urls_to_try:
        for tt in type_order:
            for inv in (True, False):
                variants.append(
                    {
                        "type": tt,
                        "websiteURL": url,
                        "websiteKey": website_key,
                        "isInvisible": inv,
                    }
                )

    last_err: Exception | None = None
    seen: set[str] = set()
    for task in variants:
        sig = f"{task['type']}|{task['websiteURL']}|{task.get('isInvisible')}"
        if sig in seen:
            continue
        seen.add(sig)
        try:
            return _create_and_poll(task)
        except Exception as e:
            last_err = e
            _log(log, f"[captcha] variant failed: {e}")
            # if invalid input, try next variant; if timeout/other, still try few more
            continue
    raise RuntimeError(str(last_err or "CapSolver failed"))


# -----------------------------------------------------------------
# Token injection
# -----------------------------------------------------------------

def inject_recaptcha_token(page: Any, token: str) -> bool:
    """
    Inject a solved reCAPTCHA token into the page.
    Returns True if injection likely succeeded.
    """
    if not token or not page:
        return False
    # Embed token in JS — DrissionPage arg passing is unreliable for run_js
    tok_js = json.dumps(token)
    script = f"""
(() => {{
  const token = {tok_js};
  if (!token) return 'no-token';
  let hits = 0;
  const setVal = (el) => {{
    if (!el) return;
    try {{
      const proto = el.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
      const ns = Object.getOwnPropertyDescriptor(proto, 'value').set;
      ns.call(el, token);
    }} catch (e) {{
      try {{ el.value = token; }} catch (e2) {{}}
    }}
    try {{ el.innerHTML = token; }} catch (e) {{}}
    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    hits += 1;
  }};
  // ensure textarea exists for invisible recaptcha
  let ta = document.getElementById('g-recaptcha-response');
  if (!ta) {{
    ta = document.createElement('textarea');
    ta.id = 'g-recaptcha-response';
    ta.name = 'g-recaptcha-response';
    ta.style.display = 'none';
    document.body.appendChild(ta);
  }}
  setVal(ta);
  document.querySelectorAll(
    'textarea[id*="recaptcha"], textarea[name*="recaptcha"], input[name="g-recaptcha-response"]'
  ).forEach(setVal);
  // enterprise / multi-widget textareas
  document.querySelectorAll('textarea[name^="g-recaptcha-response"]').forEach(setVal);
  try {{
    const walkCb = (obj, depth) => {{
      if (!obj || depth > 6) return;
      if (typeof obj === 'function') {{
        try {{ obj(token); hits += 1; }} catch (e) {{}}
        return;
      }}
      if (typeof obj === 'object') {{
        for (const k of Object.keys(obj)) {{
          if (k === 'callback' || k === 'promise-callback') walkCb(obj[k], depth + 1);
          else if (depth < 4) walkCb(obj[k], depth + 1);
        }}
      }}
    }};
    if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {{
      Object.keys(window.___grecaptcha_cfg.clients).forEach((id) => {{
        walkCb(window.___grecaptcha_cfg.clients[id], 0);
      }});
    }}
  }} catch (e) {{}}
  try {{
    if (typeof window.onRecaptchaSuccess === 'function') {{
      window.onRecaptchaSuccess(token); hits += 1;
    }}
  }} catch (e) {{}}
  window.__dbx_recaptcha_token = token;
  return hits > 0 ? 'ok' : 'no-hit';
}})()
"""
    try:
        result = page.run_js(script)
        if result == "ok":
            return True
        # still treat no-hit as soft success if textarea has value
        try:
            val = page.run_js(
                "return (document.getElementById('g-recaptcha-response')||{}).value||''"
            )
            if val and len(str(val)) > 40:
                return True
        except Exception:
            pass
        return result == "ok"
    except Exception:
        pass

    try:
        ta = page.ele("css:#g-recaptcha-response", timeout=1)
        if ta:
            try:
                ta.clear()
            except Exception:
                pass
            ta.input(token)
            return True
    except Exception:
        pass

    return False

# -----------------------------------------------------------------
# High-level: detect + solve + inject in one shot
# -----------------------------------------------------------------


def maybe_solve_page_captcha(
    page: Any,
    *,
    api_key: str = "",
    log: Optional[LogFn] = None,
    max_attempts: int = 2,
) -> Dict[str, Any]:
    """
    High-level convenience: detect reCAPTCHA on page, solve via CapSolver,
    and inject the token. Returns dict with keys:
      ok (bool)  — True if token injected or no captcha needed
      reason (str)  — description of what happened
    Does NOT raise on solve failures — returns ok=False with reason.
    """
    info = detect_recaptcha(page)
    if not info:
        return {"ok": True, "reason": "no_captcha_detected", "info": None}

    if not info.get("sitekey"):
        if info.get("has_challenge_iframe") or info.get("visible"):
            return {"ok": False, "reason": "captcha_no_sitekey", "info": info}
        return {"ok": True, "reason": "captcha_latent", "info": info}

    if not api_key:
        return {"ok": False, "reason": "no_api_key", "info": info}

    last_err = ""
    for attempt in range(max_attempts):
        _log(log, f"[captcha] solve attempt {attempt + 1}/{max_attempts}")
        try:
            token = solve_recaptcha_capsolver(
                website_url=info["pageurl"],
                website_key=info["sitekey"],
                api_key=api_key,
                is_enterprise=info.get("is_enterprise", False),
                log=log,
            )
        except Exception as e:
            last_err = str(e)
            _log(log, f"[captcha] solve failed: {e}")
            # re-detect sitekey in case page changed
            info = detect_recaptcha(page) or info
            continue

        injected = inject_recaptcha_token(page, token)
        if injected:
            _log(log, f"[captcha] token injected (attempt {attempt + 1})")
            return {"ok": True, "reason": "solved_and_injected", "info": info}
        # CapSolver token is valid; continue UI even if DOM inject is flaky
        _log(log, "[captcha] inject uncertain — continuing with solved token")
        return {
            "ok": True,
            "reason": "solved_inject_uncertain",
            "info": info,
            "token_len": len(token),
        }

    return {"ok": False, "reason": f"failed:{last_err}", "info": info}
