#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP proxy list pool (host:port:user:pass / URL lines).

Community-style giveaway lists (7-day HTTP nodes) for registration egress when
Clash is unavailable or you want pure HTTP proxy rotation.

Config:
  http_proxy_list_path: path to txt (default D:/Downloads/all_proxies.txt or ./all_proxies.txt)
  http_proxy_enabled: bool
  http_proxy_prefer_over_clash: if true, use this pool before Clash rotate
"""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parent
_STATS_PATH = ROOT / ".http_proxy_stats.json"
_FAIL_DISABLE = 4

LogFn = Callable[[str], None]

_PROXIES: list[str] = []
_LOADED_FROM: str = ""
_LAST: Optional[str] = None


def _load_stats() -> dict[str, Any]:
    if not _STATS_PATH.is_file():
        return {}
    try:
        return json.loads(_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_stats(st: dict[str, Any]) -> None:
    try:
        _STATS_PATH.write_text(
            json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        pass


def redact(url: str) -> str:
    """Mask password in proxy URL for logs."""
    return re.sub(r":([^:@/]+)@", r":***@", url or "")


def parse_line(line: str) -> Optional[str]:
    """Normalize one line to http://user:pass@host:port or http://host:port."""
    s = (line or "").strip()
    if not s or s.startswith("#"):
        return None
    if s.startswith("https://"):
        s = "http://" + s[len("https://") :]
    if s.startswith("http://"):
        return s
    # user:pass@host:port
    if "@" in s and s.count(":") >= 2:
        return "http://" + s
    parts = s.split(":")
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    if len(parts) == 4:
        # host:port:user:pass  (common reseller format)
        host, port, user, password = parts
        user_q = quote(user, safe="")
        pass_q = quote(password, safe="")
        return f"http://{user_q}:{pass_q}@{host}:{port}"
    if len(parts) > 4:
        # host:port:user:pass:with:colons
        host, port, user = parts[0], parts[1], parts[2]
        password = ":".join(parts[3:])
        user_q = quote(user, safe="")
        pass_q = quote(password, safe="")
        return f"http://{user_q}:{pass_q}@{host}:{port}"
    return None


def load_list(path: str | Path) -> list[str]:
    path = Path(path).expanduser()
    if not path.is_file():
        return []
    out: list[str] = []
    seen: set[str] = set()
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        u = parse_line(line)
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def resolve_list_path(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or {}
    raw = str(cfg.get("http_proxy_list_path") or "").strip()
    candidates = []
    if raw:
        candidates.append(Path(raw))
    candidates.extend(
        [
            Path(r"D:/Downloads/all_proxies.txt"),
            ROOT / "all_proxies.txt",
            ROOT / "proxies.txt",
        ]
    )
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def ensure_loaded(cfg: dict[str, Any] | None = None, *, force: bool = False) -> int:
    global _PROXIES, _LOADED_FROM
    path = resolve_list_path(cfg)
    key = str(path.resolve()) if path.is_file() else ""
    if not force and _PROXIES and _LOADED_FROM == key:
        return len(_PROXIES)
    _PROXIES = load_list(path) if path.is_file() else []
    _LOADED_FROM = key
    return len(_PROXIES)


def is_available(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or {}
    if cfg.get("http_proxy_enabled") is False:
        return False
    if str(cfg.get("http_proxy_enabled", "")).lower() in ("0", "false", "no"):
        # explicit false
        if cfg.get("http_proxy_enabled") is not None and cfg.get("http_proxy_enabled") is not True:
            if not cfg.get("http_proxy_enabled"):
                return False
    n = ensure_loaded(cfg)
    # auto-enable if list exists and config not explicitly false
    if cfg.get("http_proxy_enabled") is None:
        return n > 0
    return bool(cfg.get("http_proxy_enabled")) and n > 0


def _score(url: str, st: dict[str, Any]) -> float:
    e = st.get(url) or {}
    ok = int(e.get("ok") or 0)
    fail = int(e.get("fail") or 0)
    streak = int(e.get("fail_streak") or 0)
    if streak >= _FAIL_DISABLE and ok == 0:
        return -1e9
    # prefer higher success rate + some randomness
    total = ok + fail
    rate = (ok / total) if total else 0.5
    return rate * 100 + random.random() * 5 - streak * 3


def pick(cfg: dict[str, Any] | None = None, log: LogFn | None = None) -> Optional[str]:
    """Pick next proxy URL; updates process env HTTP(S)_PROXY for child browsers."""
    global _LAST
    cfg = cfg or {}
    ensure_loaded(cfg)
    if not _PROXIES:
        return None
    st = _load_stats()
    ranked = sorted(_PROXIES, key=lambda u: _score(u, st), reverse=True)
    # drop soft-disabled
    ranked = [u for u in ranked if _score(u, st) > -1e8]
    if not ranked:
        # reset streaks if all disabled
        for u in _PROXIES:
            e = st.setdefault(u, {})
            e["fail_streak"] = 0
        _save_stats(st)
        ranked = list(_PROXIES)
    # avoid immediate repeat when possible
    if len(ranked) > 1 and _LAST in ranked:
        ranked = [u for u in ranked if u != _LAST] + [_LAST]
    url = ranked[0]
    _LAST = url
    _apply_env(url)
    if log:
        try:
            log(f"[http-proxy] using {redact(url)}")
        except Exception:
            pass
    return url


def _apply_env(url: str) -> None:
    os.environ["HTTP_PROXY"] = url
    os.environ["HTTPS_PROXY"] = url
    os.environ["http_proxy"] = url
    os.environ["https_proxy"] = url
    # for code that reads config proxy
    os.environ["GROK_HTTP_PROXY"] = url


def current() -> Optional[str]:
    return _LAST or os.environ.get("GROK_HTTP_PROXY") or os.environ.get("HTTP_PROXY")


def report_success(url: str | None = None) -> None:
    url = url or _LAST
    if not url:
        return
    st = _load_stats()
    e = st.setdefault(url, {})
    e["ok"] = int(e.get("ok") or 0) + 1
    e["fail_streak"] = 0
    e["last_ok"] = time.time()
    _save_stats(st)


def report_fail(url: str | None = None) -> None:
    url = url or _LAST
    if not url:
        return
    st = _load_stats()
    e = st.setdefault(url, {})
    e["fail"] = int(e.get("fail") or 0) + 1
    e["fail_streak"] = int(e.get("fail_streak") or 0) + 1
    e["last_fail"] = time.time()
    _save_stats(st)


def probe(url: str, test_url: str = "https://api.ipify.org", timeout: float = 8.0) -> tuple[bool, str]:
    """Quick connectivity check through the HTTP proxy."""
    handler = urllib.request.ProxyHandler({"http": url, "https": url})
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open(test_url, timeout=timeout) as r:
            body = r.read(80).decode("utf-8", errors="replace")
            return True, body.strip()[:40]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:120]


def summary(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or {}
    path = resolve_list_path(cfg)
    n = ensure_loaded(cfg, force=True)
    st = _load_stats()
    disabled = 0
    for u in _PROXIES:
        e = st.get(u) or {}
        if int(e.get("fail_streak") or 0) >= _FAIL_DISABLE and int(e.get("ok") or 0) == 0:
            disabled += 1
    # unique hosts
    hosts = set()
    for u in _PROXIES:
        try:
            hosts.add(urlparse(u).hostname or "")
        except Exception:
            pass
    hosts.discard("")
    return {
        "path": str(path) if path.is_file() else str(path),
        "exists": path.is_file(),
        "count": n,
        "unique_hosts": len(hosts),
        "soft_disabled": disabled,
        "enabled_flag": cfg.get("http_proxy_enabled"),
        "prefer_over_clash": bool(cfg.get("http_proxy_prefer_over_clash")),
        "current": redact(current() or ""),
    }


def main() -> int:
    try:
        import stdio_utf8  # noqa: F401
    except Exception:
        pass
    import argparse

    ap = argparse.ArgumentParser(description="HTTP proxy list pool")
    ap.add_argument("action", nargs="?", default="status", choices=["status", "probe", "pick"])
    ap.add_argument("--path", default="")
    ap.add_argument("--sample", type=int, default=5)
    args = ap.parse_args()
    cfg = {}
    cfg_path = ROOT / "config.json"
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if args.path:
        cfg["http_proxy_list_path"] = args.path
    if args.action == "status":
        print(json.dumps(summary(cfg), ensure_ascii=False, indent=2))
        return 0
    if args.action == "pick":
        u = pick(cfg)
        print(redact(u or ""))
        return 0 if u else 1
    if args.action == "probe":
        ensure_loaded(cfg, force=True)
        sample = _PROXIES[: max(1, args.sample)] if len(_PROXIES) <= args.sample else random.sample(_PROXIES, args.sample)
        ok = 0
        for u in sample:
            good, info = probe(u)
            print(("OK" if good else "FAIL"), redact(u), info)
            if good:
                ok += 1
                report_success(u)
            else:
                report_fail(u)
        print(f"probe_ok={ok}/{len(sample)}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
