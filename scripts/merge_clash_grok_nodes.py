#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build merged mihomo config: active profile + 600+ grok-eligible nodes
in a dedicated 宝可梦 selector group, with grok/x.ai domains routed to it.

Usage: python scripts/merge_clash_grok_nodes.py [--reload]

- Writes profiles/grok_merged.yaml (copy of active profile + injections).
- With --reload: PUT /configs to mihomo so it takes effect immediately.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import urllib.request

import yaml

PROF_DIR = r"C:/Users/zhugu/AppData/Roaming/io.github.clash-verge-rev.clash-verge-rev/profiles"
ACTIVE_UID = "RRkxrzOfRMqu"          # current active profile (24 nodes)
DONORS = [
    "RzZts8dxSJQp.yaml", "RcHV0A3cq2Jk.yaml", "R1Dw0OHGLRF6.yaml",
    "ReTfEgwrDoEQ.yaml", "R7R0pTHV10yC.yaml", "RjA6gNCGFcoQ.yaml",
    "RYRl14EP2pnI.yaml", "kiro_novproxy_chain.yaml",
]
GROUP = "宝可梦"
OUT_NAME = "grok_merged.yaml"

# grok blocked in most of Asia; keep Americas/Europe/JP/TW-adjacent exits
BAD = re.compile(
    r"HK|Hong|香港|SG|Sing|新加坡|MY|马来|TH|泰|IN|印度|ID|印尼|VN|越南|PH|菲律宾"
    r"|CN|中国|Relay|中转|Expire|到期|流量|剩余|官网|套餐|重置|欢迎|返利",
    re.I,
)
GROK_DOMAINS = ["grok.com", "x.ai"]


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--api", default="http://127.0.0.1:9097")
    ap.add_argument("--secret", default="")
    args = ap.parse_args()

    base_path = os.path.join(PROF_DIR, f"{ACTIVE_UID}.yaml")
    base = load(base_path)
    if not isinstance(base, dict):
        print("active profile unreadable")
        return 1
    merged = copy.deepcopy(base)
    proxies = merged.setdefault("proxies", []) or []
    existing = {
        (str(p.get("server")), str(p.get("port")), str(p.get("type")))
        for p in proxies
        if isinstance(p, dict)
    }
    existing_names = {str(p.get("name")) for p in proxies if isinstance(p, dict)}

    added = 0
    new_names: list[str] = []
    for donor in DONORS:
        dp = os.path.join(PROF_DIR, donor)
        if not os.path.exists(dp):
            continue
        try:
            dd = load(dp)
        except Exception:
            continue
        for px in (dd or {}).get("proxies") or []:
            if not isinstance(px, dict):
                continue
            name = str(px.get("name") or "")
            srv = str(px.get("server") or "")
            if not srv or BAD.search(name):
                continue
            key = (srv, str(px.get("port")), str(px.get("type")))
            if key in existing:
                continue
            # avoid name collisions inside merged config
            nn = name
            i = 2
            while nn in existing_names:
                nn = f"{name}#{i}"
                i += 1
            px2 = dict(px)
            px2["name"] = nn
            proxies.append(px2)
            existing.add(key)
            existing_names.add(nn)
            new_names.append(nn)
            added += 1

    groups = merged.setdefault("proxy-groups", []) or []
    # all real node names available for the group (old + new)
    all_node_names = [str(p.get("name")) for p in proxies if isinstance(p, dict)]
    gidx = next((i for i, g in enumerate(groups)
                 if isinstance(g, dict) and g.get("name") == GROUP), None)
    if gidx is None:
        groups.insert(0, {
            "name": GROUP,
            "type": "select",
            "proxies": all_node_names,
        })
    else:
        g = groups[gidx]
        have = set(g.get("proxies") or [])
        g["proxies"] = list(g.get("proxies") or []) + [
            n for n in new_names if n not in have
        ]

    rules = merged.setdefault("rules", []) or []
    inject = [f"DOMAIN-SUFFIX,{d},{GROUP}" for d in GROK_DOMAINS]
    rules[:] = [r for r in rules if not (
        isinstance(r, str) and any(r.startswith(f"DOMAIN-SUFFIX,{d},") for d in GROK_DOMAINS)
    )]
    rules[0:0] = inject

    out = os.path.join(PROF_DIR, OUT_NAME)
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    print(f"[merge] wrote {out}: +{added} nodes, group {GROUP} size="
          f"{len(groups[0]['proxies'])}, rules +{len(inject)}")

    if args.reload:
        body = json.dumps({"path": out, "force": True}).encode()
        req = urllib.request.Request(
            f"{args.api}/configs", data=body, method="PUT",
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {args.secret}"} if args.secret else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print("[merge] mihomo reload:", r.status)
        except urllib.error.HTTPError as e:
            print("[merge] reload HTTP", e.code, e.read()[:300])
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
