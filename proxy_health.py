#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Proxy health for unattended pool: Clash API + exit IP + optional xAI TLS probe.

Usage:
  python proxy_health.py
  python proxy_health.py --json
  python proxy_health.py --rotate-if-bad
  python proxy_health.py --probe-xai
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STATE_PATH = ROOT / ".proxy_health.json"


def _load_cfg() -> dict[str, Any]:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe(s: Any) -> str:
    return str(s).encode("ascii", "backslashreplace").decode("ascii")


def check_proxy(*, rotate_if_bad: bool = False, probe_xai: bool = True) -> dict[str, Any]:
    cfg = _load_cfg()
    proxy = (
        str(cfg.get("cpa_proxy") or cfg.get("proxy") or "http://127.0.0.1:7897").strip()
        or "http://127.0.0.1:7897"
    )
    out: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "proxy": proxy,
        "clash_ok": False,
        "node": None,
        "exit_ip": None,
        "xai_ok": None,
        "xai_error": None,
        "rotated": None,
        "ok": False,
    }

    try:
        import clash_proxy as cp

        out["clash_ok"] = bool(cp.is_available())
        if out["clash_ok"]:
            try:
                out["node"] = cp.get_current_node()
            except Exception as e:
                out["node_error"] = _safe(e)
            try:
                out["exit_ip"] = cp.probe_exit_ip()
            except Exception as e:
                out["exit_ip_error"] = _safe(e)
    except Exception as e:
        out["clash_error"] = _safe(e)

    if probe_xai:
        try:
            from curl_cffi import requests as cf

            s = cf.Session()
            s.proxies = {"http": proxy, "https": proxy}
            t0 = time.time()
            r = s.get(
                "https://accounts.x.ai/",
                impersonate="chrome",
                timeout=20,
                allow_redirects=True,
            )
            out["xai_ms"] = int((time.time() - t0) * 1000)
            out["xai_status"] = int(getattr(r, "status_code", 0) or 0)
            out["xai_ok"] = 200 <= out["xai_status"] < 500
        except Exception as e:
            out["xai_ok"] = False
            out["xai_error"] = _safe(e)[:200]

    bad = (not out["clash_ok"]) or (out.get("xai_ok") is False)
    if bad and rotate_if_bad:
        try:
            import clash_proxy as cp

            if cp.is_available():
                node = cp.rotate_node(
                    log=lambda m: None,
                    verify_ip=bool(cfg.get("clash_verify_ip", False)),
                )
                out["rotated"] = _safe(node) if node else None
                if node:
                    # re-probe xai once after rotate
                    try:
                        from curl_cffi import requests as cf

                        s = cf.Session()
                        s.proxies = {"http": proxy, "https": proxy}
                        r = s.get(
                            "https://accounts.x.ai/",
                            impersonate="chrome",
                            timeout=20,
                            allow_redirects=True,
                        )
                        out["xai_status_after"] = int(getattr(r, "status_code", 0) or 0)
                        out["xai_ok"] = 200 <= out["xai_status_after"] < 500
                        out["xai_error"] = None
                    except Exception as e:
                        out["xai_ok"] = False
                        out["xai_error"] = _safe(e)[:200]
        except Exception as e:
            out["rotate_error"] = _safe(e)

    out["ok"] = bool(out.get("clash_ok")) and (out.get("xai_ok") is not False)
    try:
        STATE_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Clash/proxy health for grok pool")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rotate-if-bad", action="store_true")
    ap.add_argument("--probe-xai", action="store_true", default=True)
    ap.add_argument("--no-probe-xai", action="store_true")
    args = ap.parse_args(argv)
    probe = not args.no_probe_xai
    rep = check_proxy(rotate_if_bad=bool(args.rotate_if_bad), probe_xai=probe)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(
            f"[proxy] clash_ok={rep.get('clash_ok')} node={_safe(rep.get('node'))} "
            f"exit_ip={rep.get('exit_ip')} xai_ok={rep.get('xai_ok')} "
            f"xai_ms={rep.get('xai_ms')} rotated={rep.get('rotated')} ok={rep.get('ok')}"
        )
        if rep.get("xai_error"):
            print(f"[proxy] xai_error={rep.get('xai_error')}")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
