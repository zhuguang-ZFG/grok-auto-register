#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import secrets
import string

from curl_cffi import requests as r


def main() -> int:
    s = r.Session()
    s.headers.update(
        {
            "Accept": "application/json, application/ld+json",
            "Content-Type": "application/json",
        }
    )
    d = s.get("https://api.mail.tm/domains", impersonate="chrome", timeout=20)
    print("domains", d.status_code, d.headers.get("content-type"), d.text[:300])
    try:
        dom = d.json()
    except Exception as e:
        print("json fail", e)
        return 1
    if isinstance(dom, dict) and "hydra:member" in dom:
        domain = dom["hydra:member"][0]["domain"]
    elif isinstance(dom, list):
        domain = dom[0]["domain"] if isinstance(dom[0], dict) else dom[0]
    else:
        print("bad domains", type(dom), str(dom)[:200])
        return 1
    user = "u" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    addr = f"{user}@{domain}"
    pw = secrets.token_urlsafe(12)
    cr = s.post(
        "https://api.mail.tm/accounts",
        json={"address": addr, "password": pw},
        impersonate="chrome",
        timeout=20,
    )
    print("create", cr.status_code, cr.text[:200])
    tok = s.post(
        "https://api.mail.tm/token",
        json={"address": addr, "password": pw},
        impersonate="chrome",
        timeout=20,
    )
    print("token", tok.status_code, tok.text[:200])
    jwt = (tok.json() or {}).get("token")
    msg = s.get(
        "https://api.mail.tm/messages",
        headers={"Authorization": f"Bearer {jwt}"},
        impersonate="chrome",
        timeout=20,
    )
    print("messages", msg.status_code, msg.text[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
