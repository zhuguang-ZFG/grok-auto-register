#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local OpenAI-compatible proxy; upstream via Dahl browser (CF)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .session import DEFAULT_MODEL, DahlBrowserSession


def make_handler(sess: DahlBrowserSession, api_key: str = "sk-local-dahl"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _auth_ok(self) -> bool:
            if not api_key:
                return True
            h = self.headers.get("Authorization") or ""
            return h in (f"Bearer {api_key}", api_key)

        def _send(self, code: int, body: Dict[str, Any]) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._auth_ok():
                self._send(401, {"error": {"message": "unauthorized"}})
                return
            if path in ("/health", "/"):
                body = {
                    "ok": True,
                    "browser_alive": sess.alive(),
                    "models": sess.chat_ok_models or sess.models,
                    "catalog_models": sess.models,
                    "available_tokens": sess.available_tokens,
                }
                try:
                    body["remint"] = sess.remint_status()
                except Exception:
                    pass
                self._send(200, body)
                return
            if path in ("/v1/models", "/models"):
                try:
                    sess.ensure()
                    if not sess.models:
                        sess.list_models(probe_chat=False)
                    # Prefer chat-verified list for OpenAI clients
                    serve = sess.chat_ok_models or sess.models
                except Exception as exc:
                    self._send(502, {"error": {"message": str(exc)}})
                    return
                data = [
                    {"id": m, "object": "model", "owned_by": "dahl"}
                    for m in serve
                ]
                self._send(200, {"object": "list", "data": data})
                return
            self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._auth_ok():
                self._send(401, {"error": {"message": "unauthorized"}})
                return
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(400, {"error": {"message": "invalid json"}})
                return
            if path not in ("/v1/chat/completions", "/chat/completions"):
                self._send(404, {"error": {"message": "not found"}})
                return
            if not payload.get("model"):
                payload["model"] = DEFAULT_MODEL
            try:
                sess.ensure()
                out = sess.chat_completions(payload)
                self._send(200, out)
            except Exception as exc:
                # one recovery retry
                try:
                    sess.ensure()
                    out = sess.chat_completions(payload)
                    self._send(200, out)
                except Exception as exc2:
                    self._send(502, {"error": {"message": str(exc2)[:500]}})

    return Handler


def _watchdog(sess: DahlBrowserSession, interval: float = 45.0) -> None:
    """Background: keep browser warm; remint if dead."""
    while True:
        time.sleep(interval)
        try:
            if not sess.alive():
                print("[dahl] watchdog: restart browser", flush=True)
                sess.ensure()
            else:
                # light touch so CF session stays warm
                try:
                    sess.list_models()
                except Exception:
                    sess.ensure()
        except Exception as exc:
            print(f"[dahl] watchdog err: {exc}", flush=True)


def run_forever(
    *,
    host: str = "127.0.0.1",
    port: int = 8330,
    proxy: str = "http://127.0.0.1:7897",
    api_key: str = "sk-local-dahl",
    headless: bool = False,
    hide_window: bool = True,
    watchdog: bool = True,
    remint_max_per_day: int = 5,
    remint_low_threshold: int = 50_000,
) -> None:
    sess = DahlBrowserSession(
        proxy=proxy,
        headless=headless,
        hide_window=hide_window,
        log=print,
    )
    sess.remint_max_per_day = int(remint_max_per_day)
    sess.remint_low_threshold = int(remint_low_threshold)
    sess.start()
    # first mint counts toward day budget only if we use try_remint — keep first free
    sess.mint_token()
    sess.list_models(probe_chat=True)
    sess.save_local()
    if watchdog:
        t = threading.Thread(target=_watchdog, args=(sess,), daemon=True)
        t.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(sess, api_key=api_key))
    print(
        f"dahl proxy http://{host}:{port}/v1  key={api_key} "
        f"hide_window={hide_window} headless={headless} "
        f"remint_max/day={remint_max_per_day}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        sess.close()
