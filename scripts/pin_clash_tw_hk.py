#!/usr/bin/env python3
"""Report Clash egress (follows whatever the UI / URLTest currently selected).

Smart-router / CPA import already use mixed-port ``http://127.0.0.1:7897``,
so they track the local node as soon as you switch it in Clash Verge.
This script does **not** pin by default.

  python scripts/pin_clash_tw_hk.py           # status + geo via 7897
  python scripts/pin_clash_tw_hk.py --fix-tw-hk   # emergency only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

SECRET = "set-your-secret"
BASE = "http://127.0.0.1:9097"
MIXED = "http://127.0.0.1:7897"

# URLTest / panel placeholders that are not real exit nodes.
_FAKE_NOW = re.compile(r"剩余流量|距离下次|套餐到期|官网|重置剩余")


def api(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)


def _selectors(proxies: dict) -> list[str]:
    out = [s for s in ("大哥云", "GLOBAL", "自动选择") if s in proxies]
    if not out:
        out = [n for n in proxies if "节点" in n]
    return out


def status() -> int:
    """Print current selector→node and egress geo through mixed-port (no PUT)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _, prox = api("GET", "/proxies")
    proxies = prox["proxies"]
    sels = _selectors(proxies)
    if not sels:
        print("no selector found")
        return 1
    bad = False
    for sel in sels:
        now = (proxies[sel].get("now") or "").strip()
        fake = bool(_FAKE_NOW.search(now))
        if fake:
            bad = True
        print(f"now {sel} -> {now}" + ("  [FAKE/INFO node]" if fake else ""))
    proxy = urllib.request.ProxyHandler({"http": MIXED, "https": MIXED})
    opener = urllib.request.build_opener(proxy)
    try:
        with opener.open(
            "http://ip-api.com/json/?fields=status,country,city,query", timeout=12
        ) as r:
            print("egress", r.read().decode())
    except Exception as e:
        bad = True
        print(f"egress FAIL {type(e).__name__}: {e}")
    if bad:
        print(
            "hint: mixed-port follows Clash UI; switch node there. "
            "Use --fix-tw-hk only as emergency (does not track UI after that)."
        )
        return 2
    return 0


def pin_tw_hk() -> int:
    """Emergency: force 大哥云/GLOBAL onto a TW/HK leaf. Does not follow UI."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _, prox = api("GET", "/proxies")
    proxies = prox["proxies"]
    selectors = [s for s in ("大哥云", "GLOBAL") if s in proxies]
    if not selectors:
        selectors = [n for n in proxies if "节点" in n]
    if not selectors:
        print("no selector found")
        return 1

    alls = proxies[selectors[0]].get("all") or []
    tw = [n for n in alls if re.search(r"TW|台湾", n)]
    hk = [n for n in alls if re.search(r"HK|香港", n)]
    prefer = None
    for pool in (tw, hk):
        for n in pool:
            if "01" in n or n.endswith("-1"):
                prefer = n
                break
        if prefer:
            break
    if not prefer:
        prefer = (tw or hk or [None])[0]
    if not prefer:
        print("no TW/HK node found")
        return 1

    for node_sel in selectors:
        path = "/proxies/" + urllib.parse.quote(node_sel)
        api("PUT", path, {"name": prefer})
        _, prox2 = api("GET", "/proxies")
        now = prox2["proxies"][node_sel].get("now")
        print(f"pinned {node_sel} -> {now}")
    print("note: this overrides UI until you change the selector again in Clash")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--fix-tw-hk",
        action="store_true",
        help="emergency only: force TW/HK (stops following UI until you switch)",
    )
    args = ap.parse_args()
    if args.fix_tw_hk:
        return pin_tw_hk()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
