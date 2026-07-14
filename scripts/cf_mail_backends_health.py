#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe every mail_backends entry: create address + list mails.

Usage:
  python scripts/cf_mail_backends_health.py
  python scripts/cf_mail_backends_health.py --config config.json
"""
from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _name(n: int = 8) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "u" + "".join(secrets.choice(alphabet) for _ in range(n))


def probe_backend(be: dict) -> list[dict]:
    try:
        from curl_cffi import requests as r
    except ImportError:
        import requests as r  # type: ignore

    base = str(be.get("api_base") or "").rstrip("/")
    path = str(be.get("path_accounts") or "/api/new_address")
    if not path.startswith("/"):
        path = "/" + path
    msg_path = str(be.get("path_messages") or "/api/mails")
    if not msg_path.startswith("/"):
        msg_path = "/" + msg_path
    auth_mode = str(be.get("auth_mode") or "none").lower()
    api_key = str(be.get("api_key") or "").strip()
    domains = be.get("domains") or [""]
    if isinstance(domains, str):
        domains = [d.strip() for d in domains.split(",") if d.strip()]

    out: list[dict] = []
    for domain in domains:
        domain = str(domain or "").strip()
        headers = {"Content-Type": "application/json"}
        if api_key and auth_mode == "x-admin-auth":
            headers["x-admin-auth"] = api_key
        elif api_key and auth_mode == "x-api-key":
            headers["X-API-Key"] = api_key
        elif api_key and auth_mode not in ("", "none"):
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {"name": _name(), "enablePrefix": True}
        if domain:
            payload["domain"] = domain
        row: dict = {"api_base": base, "domain": domain or "(default)", "ok": False}
        try:
            cr = r.post(
                f"{base}{path}",
                json=payload,
                headers=headers,
                impersonate="chrome",
                timeout=20,
            )
            row["create_http"] = cr.status_code
            if cr.status_code >= 400:
                row["error"] = (cr.text or "")[:160]
                out.append(row)
                continue
            data = cr.json() if cr.text else {}
            addr = str((data or {}).get("address") or "")
            jwt = str((data or {}).get("jwt") or "")
            row["address"] = addr
            if not addr or not jwt:
                row["error"] = f"missing address/jwt: {str(data)[:120]}"
                out.append(row)
                continue
            mr = r.get(
                f"{base}{msg_path}",
                headers={"Authorization": f"Bearer {jwt}"},
                params={"limit": 20, "offset": 0},
                impersonate="chrome",
                timeout=20,
            )
            row["mails_http"] = mr.status_code
            if mr.status_code < 400:
                row["ok"] = True
            else:
                row["error"] = (mr.text or "")[:160]
        except Exception as exc:
            row["error"] = str(exc)[:200]
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    args = ap.parse_args()
    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        print(f"missing {cfg_path}")
        return 1
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    backends = cfg.get("mail_backends") or []
    if not backends:
        print("no mail_backends; fallback single cloudflare_api_base")
        backends = [
            {
                "api_base": cfg.get("cloudflare_api_base"),
                "domains": str(cfg.get("defaultDomains") or "").split(","),
                "path_accounts": cfg.get("cloudflare_path_accounts") or "/api/new_address",
                "path_messages": cfg.get("cloudflare_path_messages") or "/api/mails",
                "auth_mode": cfg.get("cloudflare_auth_mode") or "none",
                "api_key": cfg.get("cloudflare_api_key") or "",
            }
        ]
    rows: list[dict] = []
    for be in backends:
        if not isinstance(be, dict) or not be.get("api_base"):
            continue
        rows.extend(probe_backend(be))
    ok_n = sum(1 for x in rows if x.get("ok"))
    for x in rows:
        flag = "OK " if x.get("ok") else "FAIL"
        print(
            f"[{flag}] {x.get('domain')} @ {x.get('api_base')} "
            f"create={x.get('create_http')} mails={x.get('mails_http')} "
            f"{x.get('address') or ''} {x.get('error') or ''}"
        )
    print(f"summary ok={ok_n}/{len(rows)}")
    return 0 if ok_n == len(rows) and rows else 1


if __name__ == "__main__":
    sys.exit(main())
