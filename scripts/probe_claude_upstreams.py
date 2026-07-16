#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe cc-switch Claude providers via Anthropic /v1/messages."""
from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
OUT = Path(r"D:/Users/grok-auto-register/logs/claude_upstream_probe.json")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

MODEL_CANDIDATES = [
    "claude-opus-4-8",
    "claude-opus-4-8[1M]",
    "claude-opus-4-8[1m]",
    "claude-opus-4-7",
    "claude-opus-4-7[1M]",
    "claude-opus-4-7[1m]",
    "claude-opus-4-6",
    "claude-sonnet-4-5",
    "glm-5.2",
    "glm-5.1",
    "glm-5-turbo",
]


def load_providers() -> list[dict]:
    if not DB.is_file():
        return []
    c = sqlite3.connect(str(DB))
    rows = c.execute(
        "SELECT id, name, settings_config FROM providers WHERE app_type='claude'"
    ).fetchall()
    c.close()
    out: list[dict] = []
    for pid, name, sc in rows:
        try:
            j = json.loads(sc or "{}")
        except Exception:
            continue
        env = j.get("env") or {}
        if not isinstance(env, dict):
            continue
        base = str(env.get("ANTHROPIC_BASE_URL") or "").strip().rstrip("/")
        key = str(
            env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or ""
        ).strip()
        model = str(env.get("ANTHROPIC_MODEL") or "").strip()
        if not base or not key:
            continue
        out.append(
            {
                "id": str(pid),
                "name": str(name),
                "base_url": base,
                "api_key": key,
                "preferred_model": model,
            }
        )
    return out


def messages_url(base: str) -> str:
    b = base.rstrip("/")
    if b.endswith("/messages"):
        return b
    if b.endswith("/v1"):
        return f"{b}/messages"
    # bigmodel: .../api/anthropic  → .../api/anthropic/v1/messages
    if "anthropic" in b and not b.endswith("/v1"):
        return f"{b}/v1/messages"
    return f"{b}/v1/messages"


def post_messages(base: str, key: str, model: str, timeout: float = 45.0):
    url = messages_url(base)
    body = {
        "model": model,
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
    }
    data = json.dumps(body).encode("utf-8")
    headers = {
        "x-api-key": key,
        "Authorization": f"Bearer {key}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "User-Agent": UA,
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw[:300]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]
    except Exception as e:
        return 0, str(e)


def extract_text(body) -> str:
    if not isinstance(body, dict):
        return str(body)[:120]
    content = body.get("content")
    if isinstance(content, list) and content:
        block = content[0]
        if isinstance(block, dict):
            return str(block.get("text") or "")[:80]
    return str(body)[:120]


def probe_one(entry: dict) -> dict:
    base = entry["base_url"]
    key = entry["api_key"]
    is_glm = "bigmodel.cn" in base or str(entry.get("preferred_model") or "").lower().startswith(
        "glm"
    )
    models: list[str] = []
    if entry.get("preferred_model"):
        models.append(entry["preferred_model"])
    for m in MODEL_CANDIDATES:
        if m not in models:
            models.append(m)
    last_code, last_body, used = 0, None, None
    ok = False
    for m in models:
        code, body = post_messages(base, key, m)
        last_code, last_body, used = code, body, m
        text = extract_text(body).upper()
        if code == 200 and (
            "PONG" in text or (isinstance(body, dict) and body.get("content"))
        ):
            ok = True
            break
        if code in (401, 403):
            break
    return {
        "name": entry["name"],
        "id": entry["id"],
        "base_url": base,
        "is_glm": is_glm,
        "messages_http": last_code,
        "messages_ok": ok,
        "model_used": used if ok else None,
        "recommend": ok and not is_glm,
        "recommend_glm_fallback": ok and is_glm,
        "error_snippet": None if ok else str(last_body)[:180],
        "api_key": key if ok else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-print", action="store_true")
    ap.add_argument("--include-keys", action="store_true")
    args = ap.parse_args()
    entries = load_providers()
    seen: set[str] = set()
    uniq: list[dict] = []
    for e in entries:
        k = e["base_url"] + "|" + e["api_key"][:16]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    if args.dry_print:
        print(
            json.dumps(
                [{k: v for k, v in e.items() if k != "api_key"} for e in uniq],
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    results = [probe_one(e) for e in uniq]
    out = []
    for r in results:
        row = dict(r)
        if not args.include_keys:
            row.pop("api_key", None)
        out.append(row)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        kind = "GLM" if r["is_glm"] else "CLD"
        mark = "OK" if r["messages_ok"] else "NO"
        pool = (
            "POOL"
            if r["recommend"]
            else ("GLM" if r["recommend_glm_fallback"] else "—")
        )
        print(
            f"[{mark}/{pool}][{kind}] {r['name']} http={r['messages_http']} "
            f"model={r.get('model_used')} {r['base_url']}"
        )
        if r.get("error_snippet"):
            print(f"      {r['error_snippet']}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
