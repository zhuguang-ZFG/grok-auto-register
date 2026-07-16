#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe local chatgpt2api + cc-switch codex remotes for unified pool eligibility."""
from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
LOCAL = {
    "name": "local-k12",
    "base_url": "http://127.0.0.1:8124/v1",
    "api_key": "k12-pool-local",
}
OUT = Path(r"D:/Users/grok-auto-register/logs/codex_upstream_probe.json")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def load_from_cc_switch() -> list[dict]:
    if not DB.is_file():
        return []
    c = sqlite3.connect(str(DB))
    rows = c.execute(
        "SELECT id, name, settings_config FROM providers WHERE app_type='codex'"
    ).fetchall()
    c.close()
    out: list[dict] = []
    for pid, name, sc in rows:
        try:
            j = json.loads(sc or "{}")
        except Exception:
            continue
        key = str((j.get("auth") or {}).get("OPENAI_API_KEY") or "").strip()
        cfg = str(j.get("config") or "")
        base = ""
        for line in cfg.splitlines():
            s = line.strip()
            if s.startswith("base_url") and "=" in s:
                base = s.split("=", 1)[1].strip().strip('"').strip("'")
        name_l = (name or "").lower()
        if not base or not key:
            continue
        if "8317" in base or "grok" in name_l:
            continue
        if "127.0.0.1:8124" in base or "localhost:8124" in base:
            continue
        out.append(
            {
                "name": str(name),
                "id": str(pid),
                "base_url": base,
                "api_key": key,
            }
        )
    return out


def http_json(url: str, key: str, body: dict | None = None, timeout: float = 25.0):
    data = None
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": UA,
        "Accept": "application/json",
    }
    method = "GET"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw[:200]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return e.code, raw[:300]
    except Exception as e:
        return 0, str(e)


def models_url(base: str) -> str:
    b = base.rstrip("/")
    if b.endswith("/models"):
        return b
    return f"{b}/models"


def chat_url(base: str) -> str:
    b = base.rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    return f"{b}/chat/completions"


def probe_one(entry: dict) -> dict:
    base = entry["base_url"].rstrip("/")
    key = entry["api_key"]
    code_m, body_m = http_json(models_url(base), key)
    models_ok = code_m == 200
    code_c, body_c = http_json(
        chat_url(base),
        key,
        {
            "model": "gpt-5.6",
            "messages": [{"role": "user", "content": "Reply: PONG"}],
            "max_tokens": 8,
        },
    )
    chat_ok = code_c == 200
    model_used = "gpt-5.6"
    if not chat_ok:
        code_c2, body_c2 = http_json(
            chat_url(base),
            key,
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Reply: PONG"}],
                "max_tokens": 8,
            },
        )
        if code_c2 == 200:
            chat_ok = True
            code_c, body_c = code_c2, body_c2
            model_used = "gpt-5.6-sol"
    recommend = bool(models_ok or chat_ok)
    return {
        "name": entry["name"],
        "id": entry.get("id"),
        "base_url": base,
        "models_http": code_m,
        "chat_http": code_c,
        "models_ok": models_ok,
        "chat_ok": chat_ok,
        "model_used": model_used if chat_ok else None,
        "recommend": recommend,
        "error_snippet": None if chat_ok else str(body_c)[:180],
        "api_key": key if recommend else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-print", action="store_true")
    ap.add_argument(
        "--include-keys",
        action="store_true",
        help="keep api_key in JSON for local config wiring (default strips keys)",
    )
    args = ap.parse_args()
    entries = [LOCAL] + load_from_cc_switch()
    seen: set[str] = set()
    uniq: list[dict] = []
    for e in entries:
        b = e["base_url"].rstrip("/")
        k = b + "|" + (e.get("api_key") or "")[:16]
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
    out_rows = []
    for r in results:
        row = dict(r)
        if not args.include_keys:
            row.pop("api_key", None)
        out_rows.append(row)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        mark = "OK" if r["recommend"] else "NO"
        print(
            f"[{mark}] {r['name']} models={r['models_http']} "
            f"chat={r['chat_http']} {r['base_url']}"
        )
        if r.get("error_snippet"):
            print(f"      {r['error_snippet']}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
