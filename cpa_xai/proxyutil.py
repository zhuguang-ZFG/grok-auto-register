"""Resolve outbound proxy for CPA mint HTTP + browser.

Priority (highest first):
  1. explicit argument
  2. thread-local runtime pin (set_runtime_proxy)
  3. environment https_proxy / HTTPS_PROXY / http_proxy / HTTP_PROXY

Thread-local pin avoids cross-talk when multiple mint workers run with
different proxies in the same process.

Community absorb (archive.zip cpa_export.py):
  - proxy pool parsing (config proxy_list)
  - round-robin across pool for concurrent workers
  - per-account sticky session rewrite for residential proxies
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any
from urllib.parse import urlparse

_thread = threading.local()


def set_runtime_proxy(proxy: str | None) -> None:
    """Pin proxy for the *current thread*. Empty clears pin."""
    p = (proxy or "").strip()
    _thread.proxy = p or None


def get_runtime_proxy() -> str | None:
    return getattr(_thread, "proxy", None)


def normalize_proxy_url(proxy: str) -> str:
    p = (proxy or "").strip()
    if not p:
        return ""
    return p if "://" in p else f"http://{p}"


def parse_proxy_pool(cfg: dict[str, Any]) -> list[str]:
    """Collect de-duplicated proxy candidates from cpa_proxy / proxy / proxy_list.

    Mirrors archive.zip ``cpa_export._parse_proxy_pool``.
    """
    items: list[str] = []
    for key in ("cpa_proxy", "proxy"):
        raw = str(cfg.get(key) or "").strip()
        if not raw:
            continue
        if raw.lower() in {"direct", "none", "off", "disabled"}:
            continue
        items.append(normalize_proxy_url(raw))
    raw_list = cfg.get("proxy_list", "")
    if isinstance(raw_list, list):
        chunks = [str(x).strip() for x in raw_list if str(x).strip()]
    elif isinstance(raw_list, str) and raw_list.strip():
        chunks = [part.strip() for part in re.split(r"[\n,;]+", raw_list) if part.strip()]
    else:
        chunks = []
    for part in chunks:
        if part.lower() in {"direct", "none", "off", "disabled"}:
            continue
        items.append(normalize_proxy_url(part))
    seen: set[str] = set()
    out: list[str] = []
    for p in items:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def resolve_proxy(explicit: str | None = None) -> str:
    for cand in (
        (explicit or "").strip(),
        (get_runtime_proxy() or "").strip(),
        (os.environ.get("https_proxy") or "").strip(),
        (os.environ.get("HTTPS_PROXY") or "").strip(),
        (os.environ.get("http_proxy") or "").strip(),
        (os.environ.get("HTTP_PROXY") or "").strip(),
    ):
        if cand:
            return cand
    return ""


def resolve_proxy_pool(cfg: dict[str, Any] | None = None) -> list[str]:
    """Return proxy pool: explicit > config pool > env > empty."""
    explicit = (get_runtime_proxy() or "").strip()
    if explicit:
        return [normalize_proxy_url(explicit)]
    if cfg:
        pool = parse_proxy_pool(cfg)
        if pool:
            return pool
    env = resolve_proxy()
    if env:
        return [normalize_proxy_url(env)]
    return []


def next_proxy_from_pool(cfg: dict[str, Any] | None = None) -> str:
    """Round-robin across configured proxy pool.

    Use this for concurrent probe workers to avoid saturating one egress.
    """
    pool = resolve_proxy_pool(cfg)
    if not pool:
        return ""
    idx = getattr(next_proxy_from_pool, "_rr", 0)
    p = pool[idx % len(pool)]
    next_proxy_from_pool._rr = idx + 1  # type: ignore[attr-defined]
    return p


def sticky_account_label(prefix: str, seed: str) -> str:
    """Deterministic per-account session label for residential sticky proxies."""
    safe_seed = re.sub(r"[^A-Za-z0-9_-]+", "-", (seed or "mint").strip())[:32]
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", (prefix or "mint").strip())[:16]
    return f"{safe_prefix}-{safe_seed}" if safe_seed else safe_prefix


def build_sticky_proxy(base: str, label: str) -> str:
    """Rewrite a residential proxy URL so each account gets its own session.

    Supported patterns (common in community providers):
      - http://host:port  -> http://session-LABEL:pass@host:port
      - http://user:pass@host:port -> http://user-session-LABEL:pass@host:port
    """
    base = normalize_proxy_url(base)
    if not base:
        return ""
    u = urlparse(base)
    if not u.hostname:
        return base
    user = u.username or ""
    pw = u.password or ""
    host = u.hostname
    port = u.port or (443 if (u.scheme or "http") == "https" else 80)
    scheme = u.scheme or "http"
    if user:
        new_user = f"{user}-session-{label}"
    else:
        new_user = f"session-{label}"
    auth = f"{new_user}:{pw}@" if pw else f"{new_user}@"
    return f"{scheme}://{auth}{host}:{port}"


def proxy_for_chromium(proxy: str) -> str:
    """Chromium --proxy-server cannot embed user:pass; host:port only."""
    p = (proxy or "").strip()
    if not p:
        return ""
    u = urlparse(p if "://" in p else f"http://{p}")
    host = u.hostname or ""
    if not host:
        return ""
    port = u.port or (443 if (u.scheme or "http") == "https" else 80)
    scheme = u.scheme or "http"
    return f"{scheme}://{host}:{port}"


def proxy_log_label(proxy: str) -> str:
    """Redact userinfo for logs."""
    p = (proxy or "").strip()
    if not p:
        return ""
    try:
        u = urlparse(p if "://" in p else f"http://{p}")
        host = u.hostname or "?"
        port = u.port or ""
        auth = "user:***@" if u.username else ""
        return f"{u.scheme or 'http'}://{auth}{host}{(':' + str(port)) if port else ''}"
    except Exception:
        return "(proxy)"
