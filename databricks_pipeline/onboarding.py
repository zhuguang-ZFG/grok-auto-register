#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dismiss Databricks first-run onboarding dialogs."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

LogFn = Callable[[str], None]


def try_skip_onboarding(
    page: Any,
    selectors: Dict[str, Any],
    *,
    log: Optional[LogFn] = None,
    rounds: int = 5,
) -> None:
    texts = (selectors.get("onboarding") or {}).get("skip_texts") or [
        "Skip",
        "Not now",
        "Continue",
        "Next",
        "Get started",
    ]
    for _ in range(rounds):
        clicked = False
        for t in texts:
            try:
                el = page.ele(f"text:{t}", timeout=0.8)
                if el:
                    el.click()
                    clicked = True
                    if log:
                        log(f"[onboarding] click {t!r}")
                    time.sleep(0.8)
            except Exception:
                continue
        if not clicked:
            break
