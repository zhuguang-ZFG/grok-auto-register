#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create Databricks workspace PAT via UI automation."""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, Optional, Tuple

from .onboarding import try_skip_onboarding

LogFn = Callable[[str], None]

_DAPI = re.compile(r"\bdapi[a-zA-Z0-9]{20,}\b")


def mint_pat(
    page: Any,
    host: str,
    selectors: Dict[str, Any],
    *,
    log: Optional[LogFn] = None,
) -> Tuple[Optional[str], str]:
    """
    Navigate settings and generate a personal access token.

    Returns (token_or_None, detail).
    """
    tok_sel = selectors.get("token") or {}
    comment = str(tok_sel.get("token_comment") or "grok-auto-register-dbx")
    host = host.rstrip("/")

    try_skip_onboarding(page, selectors, log=log)

    # try direct settings URLs
    for path in tok_sel.get("settings_paths") or ["/settings/user"]:
        url = f"{host}{path}"
        if log:
            log(f"[token] open {url}")
        try:
            page.get(url)
            time.sleep(2)
        except Exception as exc:
            if log:
                log(f"[token] nav err: {exc}")

    for t in tok_sel.get("developer_texts") or ["Developer", "Access tokens"]:
        try:
            el = page.ele(f"text:{t}", timeout=2)
            if el:
                el.click()
                time.sleep(1.5)
        except Exception:
            continue

    for t in tok_sel.get("generate_texts") or ["Generate new token", "Generate token"]:
        try:
            el = page.ele(f"text:{t}", timeout=2)
            if el:
                el.click()
                time.sleep(1)
                break
        except Exception:
            continue

    # fill comment if input present
    for css in (
        "input[name=comment]",
        "input[placeholder*=Comment]",
        "input[placeholder*=comment]",
        "input[type=text]",
    ):
        try:
            el = page.ele(f"css:{css}", timeout=1)
            if el:
                el.input(comment)
                break
        except Exception:
            continue

    # confirm generate
    for t in ("Generate", "Create", "OK", "Done"):
        try:
            el = page.ele(f"text:{t}", timeout=1)
            if el:
                el.click()
                time.sleep(1.5)
                break
        except Exception:
            continue

    html = ""
    try:
        html = page.html or ""
    except Exception:
        html = ""
    m = _DAPI.search(html)
    if m:
        if log:
            log("[token] found dapi token in page")
        return m.group(0), "ok"

    # try copy from input value
    for css in ("input[type=text]", "input[readonly]", "textarea"):
        try:
            els = page.eles(f"css:{css}", timeout=1) or []
            for el in els:
                val = ""
                try:
                    val = el.attr("value") or el.text or ""
                except Exception:
                    continue
                m = _DAPI.search(str(val))
                if m:
                    return m.group(0), "ok"
        except Exception:
            continue

    return None, "token_not_found"
