#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DrissionPage automation for Databricks SISU signup (login.databricks.com)."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import ROOT, get_databricks_section, resolve_path
from .schema import iso_now

LogFn = Callable[[str], None]


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)


def load_selectors(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or get_databricks_section()
    path = resolve_path(
        str(cfg.get("selectors_file") or "databricks_pipeline/selectors.yaml"),
        ROOT,
    )
    if not path.is_file():
        return {}
    return _parse_selectors_simple(path.read_text(encoding="utf-8"))


def _parse_selectors_simple(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    section: Optional[str] = None
    key: Optional[str] = None
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        s = line.strip()
        if indent == 0 and s.endswith(":") and not s.startswith("-"):
            section = s[:-1]
            root[section] = {}
            key = None
            continue
        if section is None:
            if ":" in s and not s.startswith("-"):
                k, v = s.split(":", 1)
                root[k.strip()] = v.strip()
            continue
        if indent >= 2 and s.endswith(":") and not s.startswith("-"):
            key = s[:-1]
            root[section][key] = []
            continue
        if s.startswith("- ") and key is not None:
            root[section][key].append(s[2:].strip().strip('"').strip("'"))
            continue
        if ":" in s and not s.startswith("-") and indent >= 2:
            k, v = s.split(":", 1)
            root[section][k.strip()] = v.strip().strip('"').strip("'")
            key = None
    return root


def _screenshot(page: Any, cfg: Dict[str, Any], tag: str) -> Optional[Path]:
    try:
        d = resolve_path(str(cfg.get("screenshots_dir") or "screenshots/databricks"), ROOT)
        d.mkdir(parents=True, exist_ok=True)
        stamp = iso_now().replace(":", "").replace("+", "_")
        path = d / f"{stamp}_{tag}.png"
        if hasattr(page, "get_screenshot"):
            page.get_screenshot(path=str(path), full_page=True)
        return path
    except Exception:
        return None


def _page_text(page: Any) -> str:
    try:
        return (page.html or "")[:200000]
    except Exception:
        return ""


def detect_human_gate(page: Any, selectors: Dict[str, Any]) -> Optional[str]:
    text = _page_text(page).lower()
    url = ""
    try:
        url = (page.url or "").lower()
    except Exception:
        pass
    hg = selectors.get("human_gates") or {}
    for p in hg.get("phone_patterns") or []:
        if str(p).lower() in text or str(p).lower() in url:
            return f"phone:{p}"
    # only hard-block captcha if challenge UI is interactive and we cannot proceed
    # recaptcha scripts are always present on SISU; do not trip solely on script tags
    for p in hg.get("captcha_block_patterns") or []:
        if str(p).lower() in text:
            return f"captcha:{p}"
    if "challenge" in url and "recaptcha" in text and "verify-code" not in url:
        # standalone challenge page
        if "g-recaptcha-response" in text or "please verify" in text:
            return "captcha:challenge_page"
    return None


def dismiss_cookies(page: Any, log: Optional[LogFn] = None) -> None:
    """Force-close OneTrust cookie banner via JS + text buttons."""
    # Force hide OneTrust overlay (blocks real form buttons)
    try:
        page.run_js("""
(() => {
  const ids = ['onetrust-banner-sdk','onetrust-pc-sdk','onetrust-consent-sdk','ot-sdk-btn-floating','ot-center-float'];
  ids.forEach(id => { const el=document.getElementById(id); if(el) el.style.display='none'; });
  // also try OneTrust API
  try { if (window.OneTrust && OneTrust.Close) OneTrust.Close(); } catch(e){}
  try { if (window.OneTrust && OneTrust.RejectAll) OneTrust.RejectAll(); } catch(e){}
  // remove overlay
  document.querySelectorAll('[id*=onetrust]').forEach(e => { e.style.display='none'; });
  document.querySelectorAll('.onetrust-pc-dark-filter,.ot-floating-button').forEach(e => { e.style.display='none'; });
})()
""")
    except Exception:
        pass
    # also click text buttons as fallback
    for t in (
        "Accept All",
        "Accept all",
        "Allow all",
        "全部接受",
        "确认我的选择",
        "全部拒绝",
        "Reject All",
        "Agree",
    ):
        try:
            el = page.ele(f"text:{t}", timeout=1)
            if el:
                el.click()
                _log(log, f"[browser] cookie: {t}")
                time.sleep(0.5)
                break
        except Exception:
            continue


def _find_fill(page: Any, css_list: List[str], value: str) -> bool:
    for css in css_list or []:
        try:
            el = page.ele(f"css:{css}", timeout=2)
            if el:
                try:
                    el.clear()
                except Exception:
                    pass
                el.input(value)
                return True
        except Exception:
            continue
    return False


def _click_text(page: Any, texts: List[str]) -> bool:
    for t in texts or []:
        try:
            el = page.ele(f"text:{t}", timeout=2)
            if el:
                el.click()
                return True
        except Exception:
            continue
        try:
            el = page.ele(f"tag:button@@text():{t}", timeout=1)
            if el:
                el.click()
                return True
        except Exception:
            continue
    return False


def _fill_otp_boxes(page: Any, code: str) -> bool:
    # SISU codes are 6 alphanumeric chars (hyphen stripped): Q6YHMB
    code = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
    if len(code) < 4:
        return False
    ordered = []
    for i in range(1, 7):
        el = None
        for sel in (
            f'css:input[aria-label="Enter verification code character {i}"]',
            f'css:input[aria-label="Character {i}"]',
            f'@aria-label=Character {i}',
            f'@aria-label=Enter verification code character {i}',
        ):
            try:
                el = page.ele(sel, timeout=0.6)
                if el:
                    break
            except Exception:
                continue
        if el:
            ordered.append(el)
    if len(ordered) < 4:
        # fallback scan
        for el in page.eles("tag:input") or []:
            try:
                aria = (el.attr("aria-label") or "").lower()
                t = (el.attr("type") or "").lower()
                if t in ("checkbox", "hidden", "radio"):
                    continue
                if "character" in aria or "verification" in aria:
                    ordered.append(el)
            except Exception:
                continue
    if len(ordered) < 4:
        return False

    # Prefer real key events — React controlled inputs ignore .value= often
    try:
        ordered[0].click()
        time.sleep(0.1)
        # clear then type full code into first box (many UIs auto-advance)
        try:
            page.actions.type(code)
            time.sleep(0.5)
            return True
        except Exception:
            pass
    except Exception:
        pass

    for i, ch in enumerate(code[: len(ordered)]):
        el = ordered[i]
        try:
            el.click()
            time.sleep(0.05)
            # key-by-key
            try:
                el.input(ch, clear=True)
            except TypeError:
                try:
                    el.clear()
                except Exception:
                    pass
                el.input(ch)
            time.sleep(0.05)
        except Exception:
            try:
                page.run_js(
                    """
                    const el = arguments[0], ch = arguments[1];
                    const setter = Object.getOwnPropertyDescriptor(
                      window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, ch);
                    el.dispatchEvent(new InputEvent('input', {bubbles:true, data:ch, inputType:'insertText'}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                    """,
                    el,
                    ch,
                )
            except Exception:
                pass
    return True


def extract_workspace_host(url: str) -> str:
    for pat in (
        r"(https://[a-z0-9.-]+\.cloud\.databricks\.com)",
        r"(https://[a-z0-9.-]+\.azuredatabricks\.net)",
        r"(https://[a-z0-9.-]+\.gcp\.databricks\.com)",
    ):
        m = re.search(pat, url or "", re.I)
        if m:
            return m.group(1).rstrip("/")
    return ""


def create_browser(cfg: Optional[Dict[str, Any]] = None) -> Any:
    from DrissionPage import ChromiumOptions, ChromiumPage
    import socket

    cfg = cfg or get_databricks_section()
    raw = cfg.get("_raw") or {}
    co = ChromiumOptions()
    # Databricks SISU is flaky in true headless; only headless when explicitly set.
    if cfg.get("browser_headless"):
        try:
            co.headless(True)
        except Exception:
            pass
    elif cfg.get("hide_window") or raw.get("hide_window"):
        try:
            co.set_argument("--window-position=-32000,-32000")
            co.set_argument("--window-size=1000,800")
        except Exception:
            pass
    proxy = str(
        cfg.get("browser_proxy")
        or cfg.get("proxy")
        or raw.get("browser_proxy")
        or raw.get("proxy")
        or ""
    ).strip()
    if proxy:
        try:
            co.set_proxy(proxy)
        except Exception:
            pass
    # CRITICAL: use a free local debug port so we never attach to Dahl/Grok Chrome
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    try:
        co.set_local_port(port)
    except Exception:
        try:
            co.set_address(f"127.0.0.1:{port}")
        except Exception:
            pass
    # Isolated profile per run
    prof = ROOT / ".browser_profiles" / "databricks" / f"run-{int(time.time())}-{port}"
    prof.mkdir(parents=True, exist_ok=True)
    try:
        co.set_user_data_path(str(prof))
    except Exception:
        pass
    page = ChromiumPage(co)
    try:
        page.set.timeouts(base=20, page_load=60, script=30)
    except Exception:
        pass
    return page


def _safe_url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""


def _wait_url_contains(page: Any, needles: List[str], timeout: float = 30) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = _safe_url(page)
        low = last.lower()
        if any(n.lower() in low for n in needles):
            return last
        time.sleep(0.5)
    return last


def run_signup(
    email: str,
    password: str,
    identity: Dict[str, str],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    log: Optional[LogFn] = None,
    verify_callback: Optional[Callable[[], Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    """
    SISU flow observed 2026-07:
      login.databricks.com/signup → email → Continue with email
      → /verify-code → OTP → (password / profile) → workspace

    verify_callback returns ('code'|'link', value). Prefer code for this flow.
    """
    cfg = cfg or get_databricks_section()
    selectors = load_selectors(cfg)
    signup = selectors.get("signup") or {}
    page = create_browser(cfg)
    result: Dict[str, Any] = {
        "status": "incomplete",
        "host": "",
        "detail": "",
        "screenshot": None,
        "page": page,
    }
    try:
        url = str(
            cfg.get("signup_url")
            or "https://login.databricks.com/signup"
        )
        # marketing page has no form — force SISU
        if "try-databricks" in url or "www.databricks.com" in url:
            url = "https://login.databricks.com/signup"
        _log(log, f"[browser] open {url}")
        page.get(url)
        time.sleep(3)
        dismiss_cookies(page, log)

        gate = detect_human_gate(page, selectors)
        if gate and gate.startswith("phone") and cfg.get("human_gate_on_phone", True):
            result["status"] = "needs_human"
            result["detail"] = gate
            result["screenshot"] = str(_screenshot(page, cfg, "phone") or "")
            return result

        # Step 1: email only
        if not _find_fill(
            page,
            signup.get("email_inputs") or ["input[name=email]", "input[type=email]"],
            email,
        ):
            result["detail"] = "email_input_not_found"
            result["screenshot"] = str(_screenshot(page, cfg, "no_email") or "")
            return result

        if not _click_text(
            page,
            signup.get("continue_email_texts")
            or ["Continue with email", "Continue", "Sign up"],
        ):
            try:
                page.actions.key_down("\n")
            except Exception:
                pass
        # Wait for navigation to verify-code (critical — do not race OTP fill)
        cur = _wait_url_contains(page, ["verify-code", "verify", "select-product"], timeout=45)
        _log(log, f"[browser] after email: {cur[:160]}")
        dismiss_cookies(page, log)

        gate = detect_human_gate(page, selectors)
        if gate and gate.startswith("phone") and cfg.get("human_gate_on_phone", True):
            result["status"] = "needs_human"
            result["detail"] = gate
            result["screenshot"] = str(_screenshot(page, cfg, "phone") or "")
            return result

        # Step 2: OTP / verify-code
        if not verify_callback:
            result["detail"] = "verify_callback_missing"
            return result

        # Poll mail while keeping page warm (touch url every few seconds)
        _log(log, "[browser] waiting email verification")
        kind, value = None, None
        otp_deadline = time.time() + float(cfg.get("otp_timeout_sec") or 180)
        last_err = ""
        while time.time() < otp_deadline:
            try:
                # keep connection alive
                _ = _safe_url(page)
            except Exception as exc:
                last_err = str(exc)
                _log(log, f"[browser] page touch err: {last_err[:80]}")
            try:
                # short timeout slices so we can keep browser alive
                from . import email_bridge as _eb  # type: ignore

                # verify_callback already does full wait — call once outside loop below
                break
            except Exception:
                break
        try:
            kind, value = verify_callback()
        except Exception as exc:
            result["detail"] = f"verify_wait:{exc}"
            result["screenshot"] = str(_screenshot(page, cfg, "verify_wait") or "")
            return result

        # Ensure still on verify page
        cur = _safe_url(page)
        if "verify" not in cur.lower():
            cur = _wait_url_contains(page, ["verify-code", "verify"], timeout=15)
            _log(log, f"[browser] recheck url: {cur[:120]}")

        if kind == "link":
            _log(log, "[browser] open verify link")
            try:
                page.get(value)
            except Exception as exc:
                result["detail"] = f"open_link:{exc}"
                result["screenshot"] = str(_screenshot(page, cfg, "open_link") or "")
                return result
            time.sleep(3)
        else:
            _log(log, f"[browser] fill OTP code={value}")
            filled = False
            for attempt in range(3):
                try:
                    if _fill_otp_boxes(page, value):
                        filled = True
                        break
                except Exception as exc:
                    _log(log, f"[browser] otp fill attempt {attempt+1}: {exc}")
                    time.sleep(1)
            if not filled:
                result["detail"] = "otp_input_not_found"
                result["screenshot"] = str(_screenshot(page, cfg, "no_otp") or "")
                return result
            time.sleep(1)
            _click_text(page, ["Continue", "Verify", "Submit", "Next"])
            time.sleep(4)
            # CapSolver path if challenge appears right after OTP
            try:
                from .captcha import maybe_solve_page_captcha

                raw = cfg.get("_raw") or {}
                api_key = str(
                    cfg.get("capsolver_api_key") or raw.get("capsolver_api_key") or ""
                ).strip()
                if api_key and str(cfg.get("captcha_provider") or "").lower() in (
                    "capsolver",
                    "auto",
                ):
                    cres = maybe_solve_page_captcha(
                        page,
                        api_key=api_key,
                        log=log,
                        max_attempts=int(cfg.get("captcha_max_attempts") or 2),
                    )
                    _log(log, f"[browser] captcha post-otp: {cres.get('reason')}")
            except Exception as exc:
                _log(log, f"[browser] captcha post-otp err: {exc}")

        # Step 3: product pick + captcha + password / profile
        time.sleep(2)
        try:
            cur = page.url or ""
        except Exception:
            cur = ""
        _log(log, f"[browser] post-verify: {cur[:120]}")

        from .captcha import maybe_solve_page_captcha
        from .onboarding import try_skip_onboarding

        raw = cfg.get("_raw") or {}
        api_key = str(
            cfg.get("capsolver_api_key") or raw.get("capsolver_api_key") or ""
        ).strip()
        captcha_provider = str(
            cfg.get("captcha_provider")
            or ("capsolver" if api_key else "manual")
        ).lower()
        captcha_attempts = int(cfg.get("captcha_max_attempts") or 2)

        def _try_captcha(tag: str) -> Optional[Dict[str, Any]]:
            """Return needs_human result dict if captcha hard-fails."""
            if captcha_provider in ("off", "none", "manual"):
                # manual mode: only hard-stop if interactive challenge visible
                from .captcha import detect_recaptcha

                info = detect_recaptcha(page)
                if info and info.get("has_challenge_iframe") and cfg.get(
                    "human_gate_on_captcha", True
                ):
                    return {
                        "status": "needs_human",
                        "detail": f"captcha_manual:{tag}",
                        "screenshot": str(_screenshot(page, cfg, f"captcha_{tag}") or ""),
                        "info": info,
                    }
                return None
            if not api_key:
                return {
                    "status": "needs_human",
                    "detail": "captcha_no_api_key",
                    "screenshot": str(_screenshot(page, cfg, "captcha_nokey") or ""),
                }
            res = maybe_solve_page_captcha(
                page,
                api_key=api_key,
                log=log,
                max_attempts=captcha_attempts,
            )
            if res.get("ok"):
                _log(log, f"[browser] captcha {tag}: {res.get('reason')}")
                return None
            if cfg.get("human_gate_on_captcha", True):
                return {
                    "status": "needs_human",
                    "detail": f"captcha_fail:{res.get('reason')}",
                    "screenshot": str(_screenshot(page, cfg, f"captcha_{tag}") or ""),
                }
            return None

        # product selection (Trial vs Free Edition)
        prefer_trial = bool(cfg.get("prefer_express", True))
        product_texts = list(signup.get("product_trial_texts") or [])
        if prefer_trial:
            product_texts = [
                "Start trial with express setup",
                "Start trial",
                "Start your free trial",
                "Databricks Trial",
                "For work",
                "Trial",
            ] + product_texts
        else:
            product_texts = [
                "Get Free Edition",
                "Free Edition",
                "For personal use",
            ] + product_texts
        if _click_text(page, product_texts):
            _log(log, "[browser] product selected")
            time.sleep(3)

        nh = _try_captcha("post_product")
        if nh:
            result.update(nh)
            result["page"] = page
            return result

        # Step 3b: handle setup-account page (account name + region + Continue)
        time.sleep(2)
        cur2 = _safe_url(page)
        if "setup-account" in cur2:
            _log(log, "[browser] on setup-account")
            # fill account display name if empty
            try:
                el = page.ele("css:input[name=accountDisplayName]", timeout=2)
                if el:
                    val = el.attr("value") or ""
                    if not val.strip():
                        el.clear()
                        el.input(identity.get("company", "Dev Labs"))
                        _log(log, "[browser] filled accountDisplayName")
            except Exception:
                pass
            # solve captcha once for this page
            nh = _try_captcha("setup_account")
            if nh:
                result.update(nh)
                result["page"] = page
                return result
            # click the REAL Continue button (type=button, not cookie type=submit)
            clicked = False
            try:
                for btn in page.eles("tag:button") or []:
                    btype = str(btn.attr("type") or "")
                    btext = (btn.text or "").strip()
                    if btype == "button" and btext.lower() in ("continue", "next", "submit", "start"):
                        btn.click()
                        _log(log, f"[browser] setup-account: clicked '{btext}' (type=button)")
                        clicked = True
                        time.sleep(4)
                        break
            except Exception as exc:
                _log(log, f"[browser] setup-account click err: {exc}")
            if not clicked:
                _click_text(page, ["Continue", "Next"])
                time.sleep(3)

        # cloud pick optional
        cloud = str(cfg.get("cloud_preference") or "aws").lower()
        cloud_map = {
            "aws": ["AWS", "Amazon Web Services", "Continue with AWS"],
            "azure": ["Azure", "Microsoft Azure"],
            "gcp": ["GCP", "Google Cloud"],
        }
        _click_text(page, cloud_map.get(cloud, cloud_map["aws"]))
        time.sleep(1)
        nh = _try_captcha("post_cloud")
        if nh:
            result.update(nh)
            result["page"] = page
            return result

        pw_filled = _find_fill(
            page,
            signup.get("password_inputs")
            or ["input[type=password]", "input[name=password]"],
            password,
        )
        if pw_filled:
            # confirm password second field
            try:
                pws = page.eles("css:input[type=password]") or []
                if len(pws) > 1:
                    pws[1].input(password)
            except Exception:
                pass
            _find_fill(
                page,
                signup.get("first_name")
                or ["input[name=firstName]", "input[name=first_name]"],
                identity.get("first_name", "Alex"),
            )
            _find_fill(
                page,
                signup.get("last_name")
                or ["input[name=lastName]", "input[name=last_name]"],
                identity.get("last_name", "Smith"),
            )
            _find_fill(
                page,
                ["input[name=company]", "input[name=organization]"],
                identity.get("company", "Acme Labs"),
            )
            nh = _try_captcha("pre_submit")
            if nh:
                result.update(nh)
                result["page"] = page
                return result
            _click_text(
                page,
                signup.get("submit_texts")
                or ["Continue", "Create account", "Sign up", "Next", "Get started"],
            )
            time.sleep(4)
            nh = _try_captcha("post_submit")
            if nh:
                result.update(nh)
                result["page"] = page
                return result

        # Step 4: wait workspace host
        timeout = float(cfg.get("workspace_ready_timeout_sec") or 600)
        deadline = time.time() + timeout
        host = ""
        captcha_rounds = 0
        while time.time() < deadline:
            gate = detect_human_gate(page, selectors)
            if gate and gate.startswith("phone") and cfg.get("human_gate_on_phone", True):
                result["status"] = "needs_human"
                result["detail"] = gate
                result["screenshot"] = str(_screenshot(page, cfg, "phone") or "")
                return result
            if captcha_rounds < captcha_attempts:
                nh = _try_captcha(f"loop{captcha_rounds}")
                captcha_rounds += 1
                if nh:
                    result.update(nh)
                    result["page"] = page
                    return result
            try:
                cur = page.url or ""
            except Exception:
                cur = ""
            host = extract_workspace_host(cur)
            if host:
                break
            # keep advancing UI
            if "select-product" in cur or "select-account" in cur:
                _click_text(page, product_texts)
            if "accounts.cloud.databricks.com" in cur:
                try_skip_onboarding(page, selectors, log=log)
            try_skip_onboarding(page, selectors, log=log)
            if _find_fill(page, ["input[type=password]"], password):
                try:
                    pws = page.eles("css:input[type=password]") or []
                    if len(pws) > 1:
                        pws[1].input(password)
                except Exception:
                    pass
                _click_text(page, ["Continue", "Create account", "Next", "Sign up"])
            _click_text(
                page,
                ["Continue", "Next", "Get started", "Skip", "Not now", "Finish"],
            )
            time.sleep(2)

        if not host:
            host = extract_workspace_host(_page_text(page))
        if not host:
            result["status"] = "incomplete"
            result["detail"] = "workspace_timeout"
            result["screenshot"] = str(_screenshot(page, cfg, "workspace_timeout") or "")
            try:
                result["last_url"] = page.url
            except Exception:
                pass
            return result

        result["status"] = "workspace_ready"
        result["host"] = host
        result["detail"] = "ok"
        return result
    except Exception as exc:
        result["status"] = "incomplete"
        result["detail"] = f"exception:{exc}"
        result["screenshot"] = str(_screenshot(page, cfg, "exception") or "")
        return result
