#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Switch CLIProxy routing profile: pool (failover) vs cache (session affinity).

Community (LINUX DO) recommends session-affinity for prompt-cache hit rate.
Our free Grok pool defaults to round-robin without affinity so exhausted
accounts can rotate quickly. Use this script to flip without hand-editing YAML.

Usage:
  python set_cliproxy_routing.py status
  python set_cliproxy_routing.py pool
  python set_cliproxy_routing.py cache
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_CONFIG = Path(r"D:/cli-proxy-api/config.yaml")

PROFILES = {
    "pool": {
        "strategy": "round-robin",
        "session_affinity": False,
        "note": "load-balance free pool; prefer quota failover over cache hits",
    },
    "cache": {
        "strategy": "round-robin",
        "session_affinity": True,
        "note": "sticky session for prompt/KV cache; still fails over when auth dies",
    },
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def parse_routing(text: str) -> dict[str, str]:
    strategy = "round-robin"
    affinity = "false"
    m = re.search(r"(?m)^\s*strategy:\s*[\"']?([^\s\"'#]+)", text)
    if m:
        strategy = m.group(1)
    m = re.search(r"(?m)^\s*session-affinity:\s*(true|false)", text, re.I)
    if m:
        affinity = m.group(1).lower()
    return {"strategy": strategy, "session_affinity": affinity}


def apply_profile(text: str, profile: str) -> str:
    prof = PROFILES[profile]
    strategy = prof["strategy"]
    affinity = "true" if prof["session_affinity"] else "false"

    if re.search(r"(?m)^\s*strategy:\s*", text):
        text = re.sub(
            r"(?m)^(\s*strategy:\s*)[\"']?[^\"'\n#]+[\"']?",
            rf"\1{strategy}",
            text,
            count=1,
        )
    else:
        # append routing block
        block = (
            "\nrouting:\n"
            f'  strategy: {strategy}\n'
            f"  session-affinity: {affinity}\n"
        )
        text = text.rstrip() + block + "\n"
        return text

    if re.search(r"(?m)^\s*session-affinity:\s*", text):
        text = re.sub(
            r"(?m)^(\s*session-affinity:\s*)(true|false)",
            rf"\1{affinity}",
            text,
            count=1,
            flags=re.I,
        )
    else:
        # insert under routing after strategy line
        text = re.sub(
            r"(?m)^(\s*strategy:\s*[^\n]+)\n",
            rf"\1\n  session-affinity: {affinity}\n",
            text,
            count=1,
        )
    return text


def detect_profile(parsed: dict[str, str]) -> str:
    aff = parsed.get("session_affinity", "false") == "true"
    if aff:
        return "cache"
    return "pool"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CLIProxy routing profile switcher")
    p.add_argument(
        "action",
        choices=["status", "pool", "cache"],
        help="status | pool (failover) | cache (session affinity)",
    )
    p.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"path to CLIProxy config.yaml (default: {DEFAULT_CONFIG})",
    )
    args = p.parse_args(argv)
    path = Path(args.config)
    if not path.is_file():
        print(f"[!] config not found: {path}", file=sys.stderr)
        return 1

    text = _read(path)
    parsed = parse_routing(text)
    current = detect_profile(parsed)

    if args.action == "status":
        print(f"[*] config: {path}")
        print(f"[*] strategy: {parsed['strategy']}")
        print(f"[*] session-affinity: {parsed['session_affinity']}")
        print(f"[*] profile: {current} — {PROFILES[current]['note']}")
        return 0

    if args.action == current:
        print(f"[*] already profile={current}, no change")
        print(f"[*] strategy={parsed['strategy']} session-affinity={parsed['session_affinity']}")
        return 0

    new_text = apply_profile(text, args.action)
    bak = path.with_suffix(path.suffix + f".bak-{args.action}")
    bak.write_text(text, encoding="utf-8")
    _write(path, new_text)
    after = parse_routing(new_text)
    print(f"[+] switched {current} -> {args.action}")
    print(f"[+] strategy={after['strategy']} session-affinity={after['session_affinity']}")
    print(f"[*] backup: {bak}")
    print(f"[*] note: {PROFILES[args.action]['note']}")
    print("[*] CLIProxy file-watches config.yaml; reload should be automatic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
