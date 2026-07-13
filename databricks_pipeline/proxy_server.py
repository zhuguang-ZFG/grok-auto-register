#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal OpenAI-compatible proxy over Databricks pool."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .config import get_databricks_section
from .pool import list_selectable, soft_disable
from .probe import forward_chat, load_catalog, resolve_model_name

_rr_lock = threading.Lock()
_rr_index = 0


def _pick_credential(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    global _rr_index
    live = list_selectable(cfg)
    if not live:
        return None
    with _rr_lock:
        cred = live[_rr_index % len(live)]
        _rr_index += 1
    return cred


def _collect_models(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    cat = load_catalog(cfg)
    aliases = cat.get("aliases") or {}
    names = set(cfg.get("probe_models") or [])
    names.update(aliases.keys())
    names.update(aliases.values())
    for c in list_selectable(cfg):
        for k, v in (c.get("models") or {}).items():
            if isinstance(v, dict) and v.get("ok"):
                names.add(k)
        names.update((c.get("aliases") or {}).keys())
    return [
        {"id": n, "object": "model", "owned_by": "databricks-pool"}
        for n in sorted(names)
    ]


def make_handler(cfg: Dict[str, Any]):
    api_key = str(cfg.get("proxy_api_key") or "")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return  # quiet

        def _auth_ok(self) -> bool:
            if not api_key:
                return True
            h = self.headers.get("Authorization") or ""
            if h == f"Bearer {api_key}":
                return True
            if h == api_key:
                return True
            return False

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
            if path in ("/v1/models", "/models"):
                self._send(200, {"object": "list", "data": _collect_models(cfg)})
                return
            if path in ("/health", "/"):
                self._send(200, {"ok": True, "live": len(list_selectable(cfg))})
                return
            self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._auth_ok():
                self._send(401, {"error": {"message": "unauthorized"}})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(400, {"error": {"message": "invalid json"}})
                return
            if path not in ("/v1/chat/completions", "/chat/completions"):
                self._send(404, {"error": {"message": "not found"}})
                return
            model = str(payload.get("model") or "")
            messages = payload.get("messages") or []
            if not model or not isinstance(messages, list):
                self._send(400, {"error": {"message": "model and messages required"}})
                return
            max_tokens = int(payload.get("max_tokens") or 256)
            temperature = float(payload.get("temperature") or 0.7)

            tried: List[str] = []
            last_body: Dict[str, Any] = {"error": {"message": "no live credentials"}}
            last_code = 503
            for _ in range(3):
                cred = _pick_credential(cfg)
                if not cred:
                    break
                cid = str(cred.get("id"))
                if cid in tried:
                    break
                tried.append(cid)
                try:
                    code, body = forward_chat(
                        cred,
                        model,
                        messages,
                        cfg=cfg,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                except Exception as exc:
                    last_code = 502
                    last_body = {"error": {"message": str(exc)}}
                    continue
                last_code, last_body = code, body
                if code in (401, 403):
                    try:
                        soft_disable(cid, f"proxy_auth_{code}", cfg)
                    except Exception:
                        pass
                    continue
                if code == 402:
                    try:
                        soft_disable(cid, "proxy_quota", cfg)
                    except Exception:
                        pass
                    continue
                self._send(code, body if isinstance(body, dict) else {"raw": body})
                return
            self._send(last_code, last_body)

    return Handler


def serve(cfg: Optional[Dict[str, Any]] = None, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    cfg = cfg or get_databricks_section()
    port = int(cfg.get("proxy_port") or 8320)
    httpd = ThreadingHTTPServer((host, port), make_handler(cfg))
    return httpd


def run_forever(cfg: Optional[Dict[str, Any]] = None) -> None:
    cfg = cfg or get_databricks_section()
    httpd = serve(cfg)
    port = int(cfg.get("proxy_port") or 8320)
    print(f"databricks proxy on http://127.0.0.1:{port}/v1  key={cfg.get('proxy_api_key')}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
