"""Clash Verge proxy pool — community-standard rotation + health scoring.

Pattern borrowed from lxf746/any-auto-register `core/proxy_pool.py`:
  - get_next with preference for high success rate
  - report_success / report_fail
  - auto-disable after consecutive fails
  - delay-based health preference

Plus Clash-specific:
  - rotate GLOBAL selector via REST API
  - force global mode so rules don't leak real IP
  - close connections after switch (fresh TCP)
  - optional exit-IP verify
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_API = "http://127.0.0.1:9097"
DEFAULT_SECRET = "set-your-secret"
DEFAULT_PROXY_PORT = 7897

_PREFERRED_SELECTORS = ["GLOBAL", "节点选择", "Proxy", "🚀 节点选择", "proxies"]
_BAD_NAME_HINTS = ("剩余流量", "距离下次", "套餐到期", "建议", "放丢失", "官网", "DIRECT", "REJECT")

# Persist node success/fail scores (same idea as community ProxyModel table)
_STATS_PATH = Path(__file__).resolve().parent / ".clash_node_stats.json"
_FAIL_DISABLE_THRESHOLD = 5  # consecutive fails with 0 success → soft-disable
_LAST_NODE: Optional[str] = None
_LAST_EXIT_IP: Optional[str] = None


def _api_get(api: str, secret: str, path: str, timeout: float = 5.0) -> dict:
    h = {"Authorization": "Bearer " + secret} if secret else {}
    req = urllib.request.Request(api + path, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _api_put(api: str, secret: str, path: str, body: dict, timeout: float = 5.0) -> bool:
    h = {
        "Authorization": "Bearer " + secret,
        "Content-Type": "application/json",
    } if secret else {"Content-Type": "application/json"}
    data = json.dumps(body).encode()
    req = urllib.request.Request(api + path, data=data, headers=h, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status in (200, 204)
    except Exception:
        return False


def _api_patch_config(api: str, secret: str, body: dict, timeout: float = 5.0) -> bool:
    h = {
        "Authorization": "Bearer " + secret,
        "Content-Type": "application/json",
    } if secret else {"Content-Type": "application/json"}
    data = json.dumps(body).encode()
    req = urllib.request.Request(api + "/configs", data=data, headers=h, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status in (200, 204)
    except Exception:
        return False


def _api_delete(api: str, secret: str, path: str, timeout: float = 5.0) -> bool:
    h = {"Authorization": "Bearer " + secret} if secret else {}
    req = urllib.request.Request(api + path, headers=h, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status in (200, 204)
    except Exception:
        return False


def _load_stats() -> dict[str, Any]:
    if not _STATS_PATH.is_file():
        return {"nodes": {}}
    try:
        return json.loads(_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"nodes": {}}


def _save_stats(data: dict[str, Any]) -> None:
    try:
        tmp = _STATS_PATH.with_suffix(".json.tmp")
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _STATS_PATH)
    except Exception:
        pass


def _node_score(name: str, stats: dict) -> float:
    """Higher = better. Mirrors community success_count / (success+fail)."""
    n = stats.get("nodes", {}).get(name) or {}
    if n.get("disabled"):
        return -1.0
    ok = int(n.get("success") or 0)
    fail = int(n.get("fail") or 0)
    total = ok + fail
    if total == 0:
        return 0.5  # untested — neutral
    return ok / total


def report_success(node: Optional[str] = None) -> None:
    """Community pattern: mark last (or given) node as successful."""
    global _LAST_NODE
    name = node or _LAST_NODE
    if not name:
        return
    data = _load_stats()
    nodes = data.setdefault("nodes", {})
    ent = nodes.setdefault(name, {"success": 0, "fail": 0, "disabled": False})
    ent["success"] = int(ent.get("success") or 0) + 1
    ent["last_ok"] = time.time()
    ent["disabled"] = False
    _save_stats(data)


def report_fail(node: Optional[str] = None) -> None:
    """Community pattern: mark failure; soft-disable after threshold."""
    global _LAST_NODE
    name = node or _LAST_NODE
    if not name:
        return
    data = _load_stats()
    nodes = data.setdefault("nodes", {})
    ent = nodes.setdefault(name, {"success": 0, "fail": 0, "disabled": False})
    ent["fail"] = int(ent.get("fail") or 0) + 1
    ent["last_fail"] = time.time()
    ok = int(ent.get("success") or 0)
    fail = int(ent.get("fail") or 0)
    # Soft-disable: many fails with almost no success (community: fail>=5 & success==0)
    if fail >= _FAIL_DISABLE_THRESHOLD and ok == 0:
        ent["disabled"] = True
    elif fail >= 10 and ok > 0 and fail / (ok + fail) > 0.85:
        ent["disabled"] = True
    _save_stats(data)


def _list_real_nodes(api: str, secret: str, selector: str) -> tuple[list[str], dict]:
    b = _api_get(api, secret, "/proxies")
    proxies = b.get("proxies", {})
    sel = proxies.get(selector, {})
    all_n = sel.get("all", [])
    group_types = {"Selector", "URLTest", "Fallback", "LoadBalance"}
    real = []
    infos = {}
    for n in all_n:
        info = proxies.get(n)
        if not info:
            continue
        ntype = info.get("type", "")
        if ntype in group_types or ntype in ("Direct", "Reject", "Pass"):
            continue
        if any(bad in n for bad in _BAD_NAME_HINTS):
            continue
        real.append(n)
        infos[n] = info
    return real, infos


def _find_main_selector(api: str, secret: str) -> Optional[str]:
    b = _api_get(api, secret, "/proxies")
    proxies = b.get("proxies", {})
    for name in _PREFERRED_SELECTORS:
        info = proxies.get(name)
        if info and info.get("type") == "Selector":
            real, _ = _list_real_nodes(api, secret, name)
            if len(real) >= 3:
                return name
    for name, info in proxies.items():
        if info.get("type") == "Selector":
            real, _ = _list_real_nodes(api, secret, name)
            if len(real) >= 3:
                return name
    return None


def get_current_node(api: str = DEFAULT_API, secret: str = DEFAULT_SECRET) -> Optional[str]:
    try:
        sel = _find_main_selector(api, secret)
        if not sel:
            return None
        b = _api_get(api, secret, "/proxies/" + urllib.request.quote(sel))
        return b.get("now")
    except Exception:
        return None


def close_connections(api: str = DEFAULT_API, secret: str = DEFAULT_SECRET) -> None:
    """Drop all open connections so the next request uses the new node."""
    try:
        _api_delete(api, secret, "/connections")
    except Exception:
        pass


def probe_exit_ip(proxy_port: int = DEFAULT_PROXY_PORT, timeout: float = 12.0) -> Optional[str]:
    """Return exit IP via Clash mixed-port, or None on failure."""
    try:
        proxy = f"http://127.0.0.1:{proxy_port}"
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
        with opener.open("https://api.ipify.org?format=json", timeout=timeout) as r:
            data = json.loads(r.read())
            return str(data.get("ip") or "").strip() or None
    except Exception:
        return None


def rotate_node(
    api: str = DEFAULT_API,
    secret: str = DEFAULT_SECRET,
    *,
    prefer_low_delay: bool = True,
    max_delay_ms: int = 800,
    avoid: Optional[str] = None,
    force_global: bool = True,
    close_conns: bool = True,
    verify_ip: bool = False,
    proxy_port: int = DEFAULT_PROXY_PORT,
    log=None,
) -> Optional[str]:
    """Switch to a different healthy node; return its name or None."""
    global _LAST_NODE, _LAST_EXIT_IP
    try:
        if force_global:
            try:
                _api_patch_config(api, secret, {"mode": "global"})
            except Exception:
                pass

        sel = _find_main_selector(api, secret)
        if not sel:
            if log:
                log("[clash] no main selector found")
            return None
        real, infos = _list_real_nodes(api, secret, sel)
        if not real:
            if log:
                log("[clash] no real proxy nodes available")
            return None

        if avoid is None:
            try:
                b = _api_get(api, secret, "/proxies/" + urllib.request.quote(sel))
                avoid = b.get("now")
            except Exception:
                pass

        stats = _load_stats()
        candidates = [n for n in real if n != avoid and _node_score(n, stats) >= 0]
        if not candidates:
            # All soft-disabled — re-enable all and try again
            for n in list(stats.get("nodes", {}).keys()):
                stats["nodes"][n]["disabled"] = False
            _save_stats(stats)
            candidates = [n for n in real if n != avoid] or list(real)

        # Score: high success rate first, then low delay
        scored: list[tuple[float, int, str]] = []
        for n in candidates:
            rate = _node_score(n, stats)
            hist = infos.get(n, {}).get("history", [])
            delay = hist[-1].get("delay", 0) if hist else 0
            if prefer_low_delay and delay > max_delay_ms:
                continue
            # Prefer high rate, then low delay (delay 0 = untested, treat as mid)
            delay_key = delay if delay > 0 else 400
            scored.append((rate, -delay_key, n))

        if not scored:
            scored = [(0.5, 0, n) for n in candidates]

        scored.sort(reverse=True)
        # Weighted pick among top half
        top = scored[: max(3, len(scored) // 2)]
        chosen = random.choice(top)[2]

        ok = _api_put(api, secret, "/proxies/" + urllib.request.quote(sel), {"name": chosen})
        if not ok:
            if log:
                log(f"[clash] failed to rotate to {chosen}")
            return None

        if close_conns:
            close_connections(api, secret)

        time.sleep(1.2)
        _LAST_NODE = chosen

        exit_ip = None
        if verify_ip:
            exit_ip = probe_exit_ip(proxy_port)
            if exit_ip:
                _LAST_EXIT_IP = exit_ip
                if log:
                    log(f"[clash] rotated -> {chosen} (exit {exit_ip})")
            elif log:
                log(f"[clash] rotated -> {chosen} (exit IP verify failed)")
        else:
            if log:
                log(f"[clash] rotated -> {chosen}")

        return chosen
    except Exception as exc:
        if log:
            log(f"[clash] rotate error: {exc}")
        return None


def get_proxy_url(port: int = DEFAULT_PROXY_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def is_available(api: str = DEFAULT_API, secret: str = DEFAULT_SECRET) -> bool:
    try:
        _api_get(api, secret, "/version", timeout=3.0)
        return True
    except Exception:
        return False


def last_node() -> Optional[str]:
    return _LAST_NODE


def last_exit_ip() -> Optional[str]:
    return _LAST_EXIT_IP
