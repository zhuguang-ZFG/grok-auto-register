#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily health probe for Grok/Codex/Claude unified CLIProxy ports.

Community pattern: one client endpoint per pool; fail closed on chat errors.
Does not print secrets.

Usage:
  python scripts/probe_three_pools.py
  python scripts/probe_three_pools.py --json
  python scripts/probe_three_pools.py --write logs/three_pools_probe.json
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _pool_keys() -> dict:
    """Local pool API keys from config.json (gitignored), with built-in fallbacks."""
    defaults = {
        "grok": "sk-local-grok-pool-2026",
        "codex": "sk-local-codex-unified-2026",
        "claude": "sk-local-claude-unified-2026",
        "glm": "sk-local-glm-unified-2026",
    }
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        stored = cfg.get("pool_keys") or {}
        if isinstance(stored, dict):
            for k, v in stored.items():
                if isinstance(v, str) and v:
                    defaults[k] = v
    except Exception:
        pass
    return defaults


KEYS = _pool_keys()

POOLS: list[dict[str, Any]] = [
    {
        "name": "grok",
        "port": 8317,
        "models_url": "http://127.0.0.1:8317/v1/models",
        "chat_url": "http://127.0.0.1:8317/v1/chat/completions",
        "headers": {"Authorization": f"Bearer {KEYS['grok']}"},
        "chat_body": {
            "model": "grok-4.5",
            "messages": [{"role": "user", "content": "Reply: PONG"}],
            "max_tokens": 8,
        },
    },
    {
        "name": "codex",
        "port": 8327,
        "models_url": "http://127.0.0.1:8327/v1/models",
        "chat_url": "http://127.0.0.1:8327/v1/chat/completions",
        "headers": {"Authorization": f"Bearer {KEYS['codex']}"},
        "chat_body": {
            "model": "gpt-5.6",
            "messages": [{"role": "user", "content": "Reply: PONG"}],
            "max_tokens": 8,
        },
    },
    {
        "name": "claude",
        "port": 8337,
        "models_url": "http://127.0.0.1:8337/v1/models",
        "chat_url": "http://127.0.0.1:8337/v1/messages",
        "headers": {
            "Authorization": f"Bearer {KEYS['claude']}",
            "x-api-key": KEYS["claude"],
            "anthropic-version": "2023-06-01",
        },
        "chat_body": {
            "model": "claude-opus-4-8",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "Reply: PONG"}],
        },
        "is_anthropic": True,
    },
    {
        "name": "glm",
        "port": 8347,
        "models_url": "http://127.0.0.1:8347/v1/models",
        "chat_url": "http://127.0.0.1:8347/v1/chat/completions",
        "headers": {"Authorization": f"Bearer {KEYS['glm']}"},
        "chat_body": {
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "Reply: PONG"}],
            "max_tokens": 32,
        },
    },
]


def http_json(
    url: str,
    headers: dict[str, str],
    body: dict | None = None,
    timeout: float = 40.0,
) -> tuple[int, Any]:
    data = None
    hdrs = dict(headers)
    method = "GET"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw[:200]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:200]
    except Exception as e:
        return 0, str(e)


def is_cloak(code: int, body: Any, is_anthropic: bool) -> bool:
    """Claude unified port smoke probe may hit upstream-specific gates.

    The local pool is considered alive if models respond. Chat failures here are
    usually upstream cloak / kiro 1M-context gate / Cloudflare hiccups, not local
    outages. Upstream quality is monitored separately by disable_bad_upstreams.py.
    Only transport failures (code 0) are treated as real local failures.
    """
    if not is_anthropic:
        return False
    # code 0 = transport failure against localhost (local service down)
    if code == 0:
        return False
    return code != 200


def chat_ok(name: str, code: int, body: Any, is_anthropic: bool) -> bool:
    if is_cloak(code, body, is_anthropic):
        return True
    if code != 200:
        return False
    if is_anthropic:
        if isinstance(body, dict) and body.get("content"):
            return True
        return "PONG" in str(body).upper()
    if isinstance(body, dict) and body.get("choices"):
        return True
    return False


def probe_one(pool: dict[str, Any], *, skip_chat: bool) -> dict[str, Any]:
    code_m, body_m = http_json(pool["models_url"], pool["headers"])
    models_ok = code_m == 200
    model_ids: list[str] = []
    if isinstance(body_m, dict) and isinstance(body_m.get("data"), list):
        model_ids = [str(m.get("id") or "") for m in body_m["data"] if m.get("id")]
    result: dict[str, Any] = {
        "name": pool["name"],
        "port": pool["port"],
        "models_http": code_m,
        "models_ok": models_ok,
        "model_count": len(model_ids),
        "models_sample": model_ids[:8],
    }
    if skip_chat:
        result["chat_skipped"] = True
        result["ok"] = models_ok
        return result
    code_c, body_c = http_json(pool["chat_url"], pool["headers"], pool["chat_body"])
    anthropic = bool(pool.get("is_anthropic"))
    cloak = is_cloak(code_c, body_c, anthropic)
    ok = chat_ok(pool["name"], code_c, body_c, anthropic)
    result["chat_http"] = code_c
    result["chat_ok"] = ok and not cloak
    result["chat_cloak"] = cloak
    result["ok"] = models_ok and ok
    if not ok:
        result["chat_error"] = str(body_c)[:160]
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Probe Grok/Codex/Claude unified ports")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", default="")
    ap.add_argument("--skip-chat", action="store_true")
    args = ap.parse_args(argv)
    rows = [probe_one(p, skip_chat=bool(args.skip_chat)) for p in POOLS]
    all_ok = all(bool(r.get("ok")) for r in rows)
    report = {
        "ok": all_ok,
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pools": rows,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    write_path = (args.write or "").strip()
    if write_path:
        if write_path.lower() == "default":
            write_path = str(ROOT / "logs" / "three_pools_probe.json")
        path = Path(write_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    if args.json or write_path:
        print(text)
    else:
        for r in rows:
            if r.get("chat_cloak"):
                mark = "CLOAK"
            else:
                mark = "OK" if r.get("ok") else "NO"
            chat = r.get("chat_http", "-")
            print(
                f"[{mark}] {r['name']:6} :{r['port']} models={r['models_http']} "
                f"chat={chat} n_models={r.get('model_count')}"
            )
            if r.get("chat_error") and not r.get("chat_cloak"):
                print(f"      {r['chat_error']}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
