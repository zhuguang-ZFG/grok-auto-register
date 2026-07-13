#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DrissionPage automation for Databricks Express signup."""

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
    # tiny YAML-ish parser for nested keys we need
    data: Dict[str, Any] = {}
    stack: List[Tuple[int, Dict[str, Any]]] = [(-1, data)]
    current_list_key: Optional[str] = None
    current_list: Optional[List[str]] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        s = line.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
            current_list_key = None
            current_list = None
        parent = stack[-1][1]
        if s.startswith("- "):
            val = s[2:].strip().strip('"').strip("'")
            if current_list is not None:
                current_list.append(val)
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            k, v = k.strip(), v.strip()
            if not v:
                node: Dict[str, Any] = {}
                parent[k] = node
                stack.append((indent, node))
                current_list = None
                current_list_key = None
            else:
                parent[k] = v.strip('"').strip("'")
                current_list = None
            continue
    # second pass: recover list fields with simpler state machine
    data = _parse_selectors_simple(path.read_text(encoding="utf-8"))
    return data


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
        path = d / f"{iso_now().replace(':', '').replace('+', '_')}_{tag}.png"
        # DrissionPage ChromiumPage
        if hasattr(page, "get_screenshot"):
            page.get_screenshot(path=str(path), full_page=True)
        elif hasattr(page, "save_screenshot"):
            page.save_screenshot(str(path))
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
    hg = selectors.get("human_gates") or {}
    for p in hg.get("phone_patterns") or []:
        if str(p).lower() in text:
            return f"phone:{p}"
    for p in hg.get("captcha_patterns") or []:
        if str(p).lower() in text:
            return f"captcha:{p}"
    return None


def _find_fill(page: Any, css_list: List[str], value: str) -> bool:
    for css in css_list or []:
        try:
            el = page.ele(f"css:{css}", timeout=2)
            if el:
                el.clear()
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


def create_browser(cfg: Optional[Dict[str, Any]] = None) -> Any:
    """Create DrissionPage Chromium page with optional proxy."""
    from DrissionPage import ChromiumOptions, ChromiumPage

    cfg = cfg or get_databricks_section()
    raw = cfg.get("_raw") or {}
    co = ChromiumOptions()
    if cfg.get("browser_headless") or raw.get("hide_window"):
        try:
            co.headless(True)
        except Exception:
            pass
    proxy = (
        str(cfg.get("browser_proxy") or cfg.get("proxy") or raw.get("browser_proxy") or raw.get("proxy") or "")
        .strip()
    )
    if proxy:
        try:
            co.set_proxy(proxy)
        except Exception:
            pass
    # isolate profile
    prof = ROOT / ".browser_profiles" / "databricks" / f"run-{int(time.time())}"
    prof.mkdir(parents=True, exist_ok=True)
    try:
        co.set_user_data_path(str(prof))
    except Exception:
        pass
    page = ChromiumPage(co)
    return page


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
    Drive signup until workspace host or human gate / failure.

    verify_callback: called when waiting for email; returns (kind, value).

    Returns dict: status, host, detail, screenshot
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
        url = str(cfg.get("signup_url") or "https://www.databricks.com/try-databricks")
        _log(log, f"[browser] open {url}")
        page.get(url)
        time.sleep(2)

        if cfg.get("prefer_express", True):
            _click_text(page, signup.get("express_texts") or [])
            time.sleep(1)

        gate = detect_human_gate(page, selectors)
        if gate and cfg.get("human_gate_on_captcha", True) and gate.startswith("captcha"):
            result["status"] = "needs_human"
            result["detail"] = gate
            result["screenshot"] = str(_screenshot(page, cfg, "captcha") or "")
            return result
        if gate and cfg.get("human_gate_on_phone", True) and gate.startswith("phone"):
            result["status"] = "needs_human"
            result["detail"] = gate
            result["screenshot"] = str(_screenshot(page, cfg, "phone") or "")
            return result

        _find_fill(page, signup.get("email_inputs") or [], email)
        _find_fill(page, signup.get("password_inputs") or [], password)
        _find_fill(page, signup.get("first_name") or [], identity.get("first_name", "Alex"))
        _find_fill(page, signup.get("last_name") or [], identity.get("last_name", "Smith"))
        if not _click_text(page, signup.get("submit_texts") or []):
            # try enter on email
            try:
                page.actions.key_down("ENTER")
            except Exception:
                pass
        time.sleep(3)

        gate = detect_human_gate(page, selectors)
        if gate:
            kind = "phone" if gate.startswith("phone") else "captcha"
            if (kind == "phone" and cfg.get("human_gate_on_phone", True)) or (
                kind == "captcha" and cfg.get("human_gate_on_captcha", True)
            ):
                result["status"] = "needs_human"
                result["detail"] = gate
                result["screenshot"] = str(_screenshot(page, cfg, kind) or "")
                return result

        # wait verification
        if verify_callback:
            _log(log, "[browser] waiting email verification")
            kind, value = verify_callback()
            if kind == "link":
                _log(log, "[browser] open verify link")
                page.get(value)
                time.sleep(3)
            elif kind == "code":
                # try fill code inputs
                for css in ("input[name=code]", "input[type=tel]", "input[autocomplete=one-time-code]"):
                    try:
                        el = page.ele(f"css:{css}", timeout=2)
                        if el:
                            el.input(value)
                            break
                    except Exception:
                        continue
                _click_text(page, ["Verify", "Continue", "Submit", "Confirm"])
                time.sleep(3)

        # poll for workspace host
        timeout = float(cfg.get("workspace_ready_timeout_sec") or 600)
        deadline = time.time() + timeout
        host = ""
        while time.time() < deadline:
            gate = detect_human_gate(page, selectors)
            if gate:
                result["status"] = "needs_human"
                result["detail"] = gate
                result["screenshot"] = str(_screenshot(page, cfg, "gate") or "")
                return result
            try:
                cur = page.url or ""
            except Exception:
                cur = ""
            m = re.search(r"(https://[a-z0-9.-]+\.cloud\.databricks\.com)", cur, re.I)
            if m:
                host = m.group(1).rstrip("/")
                break
            m = re.search(r"(https://[a-z0-9.-]+\.azuredatabricks\.net)", cur, re.I)
            if m:
                host = m.group(1).rstrip("/")
                break
            m = re.search(r"(https://[a-z0-9.-]+\.gcp\.databricks\.com)", cur, re.I)
            if m:
                host = m.group(1).rstrip("/")
                break
            # dismiss onboarding lightly
            from .onboarding import try_skip_onboarding

            try_skip_onboarding(page, selectors, log=log)
            time.sleep(2)

        if not host:
            result["status"] = "incomplete"
            result["detail"] = "workspace_timeout"
            result["screenshot"] = str(_screenshot(page, cfg, "workspace_timeout") or "")
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
