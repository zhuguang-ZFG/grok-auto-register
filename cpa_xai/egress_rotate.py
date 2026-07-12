#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mint-time egress rotation (Clash node / HTTP list).

Registration already rotates per account; mint workers often re-use the same
local proxy URL (e.g. 127.0.0.1:7897) while the upstream node is sticky until
rotated again. Call this before protocol mint and on transient TLS failures.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

LogFn = Callable[[str], None]

_rotate_lock = threading.Lock()


def rotate_mint_egress(
    log: LogFn | None = None,
    *,
    verify_ip: bool | None = None,
) -> dict[str, Any]:
    """Rotate Clash/HTTP egress used by mint.

    Returns dict: ok, clash_node?, http_proxy?, proxy?, error?
    ``proxy`` is the URL to pass into curl_cffi (may be unchanged for Clash).
    """
    log = log or (lambda _m: None)
    out: dict[str, Any] = {"ok": False, "proxy": None}

    with _rotate_lock:
        try:
            # Lazy import: avoid circular import at package load time.
            import grok_register_ttk as reg  # type: ignore
        except Exception as e:  # noqa: BLE001
            out["error"] = f"import register: {e}"
            log(f"mint egress rotate skip: {e}")
            return out

        try:
            cfg = getattr(reg, "config", None)
            if isinstance(cfg, dict) and verify_ip is not None:
                cfg["clash_verify_ip"] = bool(verify_ip)

            # Before switching nodes, soft-disable score the last failing Clash
            # exit (community: fail streak → drop bad nodes from 注册专用).
            try:
                import clash_proxy as _cp  # type: ignore

                report = getattr(_cp, "report_fail", None)
                if callable(report):
                    report()
            except Exception as _rf_exc:  # noqa: BLE001
                log(f"mint egress report_fail skip: {_rf_exc}")

            rotate_fn = getattr(reg, "rotate_egress_proxy", None)
            if not callable(rotate_fn):
                out["error"] = "rotate_egress_proxy missing"
                log("mint egress rotate skip: no rotate_egress_proxy")
                return out

            egress = rotate_fn(log) or {}
            out["clash_node"] = egress.get("clash_node")
            out["http_proxy"] = egress.get("http_proxy")

            proxy = ""
            if isinstance(cfg, dict):
                proxy = (
                    str(cfg.get("_runtime_http_proxy") or cfg.get("proxy") or "")
                    .strip()
                )
            if not proxy and egress.get("http_proxy"):
                proxy = str(egress.get("http_proxy") or "").strip()
            out["proxy"] = proxy or None
            out["ok"] = bool(egress.get("clash_node") or egress.get("http_proxy") or proxy)
            if out["ok"]:
                node = out.get("clash_node") or out.get("http_proxy") or proxy
                safe = str(node).encode("ascii", "backslashreplace").decode("ascii")
                log(f"mint egress rotated: {safe[:120]}")
            else:
                log("mint egress rotate: no node change (clash/http unavailable?)")
            return out
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
            safe = str(e).encode("ascii", "backslashreplace").decode("ascii")
            log(f"mint egress rotate error: {safe[:160]}")
            return out
