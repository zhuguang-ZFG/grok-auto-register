#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full automated Dahl E2E: mint → models → chat → save."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .session import DEFAULT_MODEL, DahlBrowserSession, ROOT

LogFn = Callable[[str], None]


def run_e2e(
    *,
    proxy: str = "http://127.0.0.1:7897",
    model: Optional[str] = None,
    prompt: str = "Reply with exactly: dahl-ok",
    headless: bool = False,
    log: Optional[LogFn] = None,
    keep_browser: bool = False,
) -> Dict[str, Any]:
    """
    Fully automated end-to-end.

    Returns report dict with ok, model, content, token_prefix, available_tokens.
    """
    log = log or (lambda m: print(m, flush=True))
    sess = DahlBrowserSession(proxy=proxy, headless=headless, log=log)
    report: Dict[str, Any] = {"ok": False}
    try:
        sess.start()
        tok = sess.mint_token()
        models = sess.list_models()
        use_model = model or (
            DEFAULT_MODEL if DEFAULT_MODEL in models else (models[0] if models else DEFAULT_MODEL)
        )
        chat = sess.chat_completions(
            {
                "model": use_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 64,
            }
        )
        content = ""
        try:
            content = (
                ((chat.get("choices") or [{}])[0].get("message") or {}).get("content")
                or ""
            )
        except Exception:
            content = ""
        path = sess.save_local()
        # non-secret summary
        summary_path = ROOT / "dahl_keys" / "latest.json"
        summary_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "available_tokens": sess.available_tokens,
                    "token_prefix": (sess.token[:8] + "...") if sess.token else "",
                    "models": models,
                    "chat_model": chat.get("model") or use_model,
                    "content_preview": content[:200],
                    "active_path": str(path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        report = {
            "ok": True,
            "available_tokens": sess.available_tokens,
            "token_prefix": sess.token[:8] + "...",
            "models": models,
            "model": use_model,
            "content": content,
            "usage": chat.get("usage"),
            "saved": str(path),
        }
        log(f"[dahl] e2e OK model={use_model} content={content[:80]!r}")
        return report
    finally:
        if not keep_browser:
            sess.close()
        elif report.get("ok"):
            # stash session for proxy — caller owns close
            report["_session"] = sess
