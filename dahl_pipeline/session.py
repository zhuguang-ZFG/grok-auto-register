#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long-lived DrissionPage session for Dahl (Cloudflare-gated HTTP)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LogFn = Callable[[str], None]

BASE = "https://inference.dahl.global"
DEFAULT_MODEL = "MiniMaxAI/MiniMax-M2.7"


class DahlBrowserSession:
    """One Chromium page; all API calls go through page JS fetch."""

    def __init__(
        self,
        *,
        proxy: str = "http://127.0.0.1:7897",
        headless: bool = False,
        log: Optional[LogFn] = None,
        profile_dir: Optional[Path] = None,
        hide_window: bool = True,
    ) -> None:
        self.proxy = proxy
        self.headless = headless
        self.hide_window = hide_window
        self.log = log or (lambda _m: None)
        # Stable profile: reuse CF clearance across restarts when possible
        self.profile_dir = Path(
            profile_dir or (ROOT / ".browser_profiles" / "dahl" / "proxy_main")
        )
        self._page: Any = None
        self._lock = threading.RLock()
        self.token: str = ""
        self.available_tokens: Optional[int] = None
        self.models: List[str] = []
        # Models that actually accept chat (catalog may list more; filter after probe)
        self.chat_ok_models: List[str] = []
        # Limited auto remint when quota/key dies (not infinite)
        self.remint_max_per_day: int = 5
        self.remint_low_threshold: int = 50_000  # if mint reports below this, allow proactive remint
        self.last_remint_reason: str = ""

    def _log(self, msg: str) -> None:
        self.log(msg)

    def remint_status(self) -> Dict[str, Any]:
        from . import quota as q

        snap = q.status_snapshot(self.remint_max_per_day)
        snap["last_remint_reason"] = self.last_remint_reason
        snap["available_tokens"] = self.available_tokens
        return snap

    def try_remint(self, reason: str) -> bool:
        """
        Mint a new key if daily budget allows.
        Returns True if a new key was minted.
        """
        from . import quota as q

        if not q.can_remint(self.remint_max_per_day):
            self._log(
                f"[dahl] remint blocked: day cap "
                f"{q.get_daily_count()}/{self.remint_max_per_day} reason={reason}"
            )
            return False
        data = self.mint_token()
        n = q.record_remint(reason)
        self.last_remint_reason = reason
        self.save_local()
        self._log(
            f"[dahl] remint ok #{n}/{self.remint_max_per_day} "
            f"reason={reason} avail={data.get('available_tokens')}"
        )
        return True

    def alive(self) -> bool:
        """Best-effort: browser page still usable."""
        if self._page is None:
            return False
        try:
            _ = self._page.url
            return True
        except Exception:
            return False

    def start(self) -> None:
        from DrissionPage import ChromiumOptions, ChromiumPage

        with self._lock:
            if self._page is not None and self.alive():
                return
            if self._page is not None:
                try:
                    self._page.quit()
                except Exception:
                    pass
                self._page = None
            co = ChromiumOptions()
            if self.proxy:
                try:
                    co.set_proxy(self.proxy)
                except Exception:
                    pass
            # Prefer real Chrome with hidden window over headless (CF often stricter on headless)
            if self.headless:
                try:
                    co.headless(True)
                except Exception:
                    pass
            elif self.hide_window:
                try:
                    # DrissionPage: set_argument / set_pref for minimized start
                    co.set_argument("--window-position=-32000,-32000")
                    co.set_argument("--window-size=800,600")
                except Exception:
                    pass
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                co.set_user_data_path(str(self.profile_dir))
            except Exception:
                pass
            self._page = ChromiumPage(co)
            self._log(f"[dahl] open {BASE}/ profile={self.profile_dir.name}")
            self._page.get(BASE + "/")
            time.sleep(4)
            # warm CF
            self._page_fetch("GET", BASE + "/v1/models")

    def ensure(self) -> None:
        """Start or recover browser after crash."""
        if not self.alive():
            self._log("[dahl] browser dead → restart")
            with self._lock:
                self._page = None
            self.start()
            # remint after recovery
            try:
                self.mint_token()
                self.list_models()
            except Exception as exc:
                self._log(f"[dahl] remint after restart failed: {exc}")

    def close(self) -> None:
        with self._lock:
            if self._page is not None:
                try:
                    self._page.quit()
                except Exception:
                    pass
                self._page = None

    def _page_fetch(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
        timeout_ms: int = 120000,
    ) -> Dict[str, Any]:
        if self._page is None or not self.alive():
            self.ensure() if self._page is not None else self.start()
        assert self._page is not None
        headers = headers or {}
        # Embed JSON literals — DrissionPage async/arg passing is unreliable
        method_j = json.dumps(method or "GET")
        url_j = json.dumps(url)
        headers_j = json.dumps(headers)
        body_j = json.dumps(body if body else None)
        timeout_j = int(timeout_ms or 120000)
        script = f"""
return (async () => {{
  const method = {method_j};
  const url = {url_j};
  const headers = {headers_j};
  const body = {body_j};
  const timeoutMs = {timeout_j};
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {{
    const opts = {{ method, headers, signal: ctrl.signal }};
    if (body !== null && body !== undefined && method !== 'GET' && method !== 'HEAD') {{
      opts.body = body;
    }}
    const r = await fetch(url, opts);
    clearTimeout(t);
    const text = await r.text();
    return {{ status: r.status, body: text }};
  }} catch (e) {{
    clearTimeout(t);
    return {{ status: 0, body: '', error: String(e) }};
  }}
}})()
"""
        with self._lock:
            raw = self._page.run_js(script)
        if not isinstance(raw, dict):
            return {"status": 0, "body": "", "error": f"bad_js_result:{type(raw)}:{raw!r}"}
        return raw

    def mint_token(self) -> Dict[str, Any]:
        r = self._page_fetch(
            "POST",
            BASE + "/tokens",
            headers={"Content-Type": "application/json"},
            body="{}",
        )
        if r.get("status") not in (200, 201):
            raise RuntimeError(f"mint token failed: {r}")
        data = json.loads(r.get("body") or "{}")
        self.token = str(data.get("token") or "").strip()
        self.available_tokens = data.get("available_tokens")
        if not self.token:
            raise RuntimeError(f"no token in response: {data}")
        self._log(f"[dahl] minted token avail={self.available_tokens}")
        return data

    def list_models(self, *, probe_chat: bool = False) -> List[str]:
        r = self._page_fetch("GET", BASE + "/v1/models")
        if r.get("status") != 200:
            raise RuntimeError(f"models failed: {r}")
        data = json.loads(r.get("body") or "{}")
        ids = [str(x.get("id")) for x in (data.get("data") or []) if x.get("id")]
        self.models = ids
        self._log(f"[dahl] models n={len(ids)} catalog={ids}")
        if probe_chat and ids:
            self.probe_chat_models(ids)
        elif not self.chat_ok_models and ids:
            # safe default: only expose known-good until probed
            self.chat_ok_models = [
                m
                for m in ids
                if m
                in (
                    "MiniMaxAI/MiniMax-M2.7",
                    "moonshotai/Kimi-K2.6",
                )
            ] or list(ids[:1])
        return ids

    def probe_chat_models(self, ids: Optional[List[str]] = None) -> List[str]:
        """Smoke each catalog id; keep only those that accept a tiny chat."""
        ids = list(ids or self.models or [])
        ok: List[str] = []
        if not self.token:
            self.mint_token()
        for mid in ids:
            payload = {
                "model": mid,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 4,
            }
            body = json.dumps(payload)
            r = self._page_fetch(
                "POST",
                BASE + "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                body=body,
                timeout_ms=90000,
            )
            status = int(r.get("status") or 0)
            text = r.get("body") or ""
            if status == 200:
                ok.append(mid)
                self._log(f"[dahl] chat_ok {mid}")
            else:
                self._log(f"[dahl] chat_skip {mid} status={status} {text[:120]!r}")
        self.chat_ok_models = ok
        if ok:
            # Prefer serving only verified models to clients
            self.models = list(ok)
        return ok

    def chat_completions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from . import quota as q

        if not self.token:
            # bootstrap key does not consume daily remint budget
            self.mint_token()
        body = json.dumps(payload)

        def once() -> Dict[str, Any]:
            return self._page_fetch(
                "POST",
                BASE + "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                body=body,
                timeout_ms=180000,
            )

        r = once()
        status = int(r.get("status") or 0)
        text = r.get("body") or ""

        # 401 invalid key, or quota-like errors → budgeted remint + one retry
        need = status == 401 or q.is_quota_error(status, text)
        if need:
            reason = f"http_{status}"
            if self.try_remint(reason):
                r = once()
                status = int(r.get("status") or 0)
                text = r.get("body") or ""
            elif status == 401:
                # legacy single remint without budget was unsafe; still try once if no budget file
                # already blocked — fall through to error
                pass

        if status != 200:
            raise RuntimeError(f"chat HTTP {status}: {text[:400]}")
        data = json.loads(text)
        # optional: subtract rough usage if present
        try:
            used = int((data.get("usage") or {}).get("total_tokens") or 0)
            if used and isinstance(self.available_tokens, int):
                self.available_tokens = max(0, self.available_tokens - used)
        except Exception:
            pass
        # proactive remint when reported balance very low (still day-capped)
        if (
            isinstance(self.available_tokens, int)
            and self.available_tokens < int(self.remint_low_threshold)
            and self.available_tokens >= 0
        ):
            self._log(
                f"[dahl] low balance {self.available_tokens} < {self.remint_low_threshold}"
            )
            self.try_remint("low_balance")
        return data

    def save_local(self, path: Optional[Path] = None) -> Path:
        path = path or (ROOT / "dahl_keys" / "active.local.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "token": self.token,
            "available_tokens": self.available_tokens,
            "models": self.models,
            "base_url": BASE + "/v1",
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
