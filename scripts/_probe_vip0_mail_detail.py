#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe vip0 email detail endpoints after listing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from curl_cffi import requests

ROOT = Path(__file__).resolve().parents[1]
sec = json.loads((ROOT / "vip0_mail.local.json").read_text(encoding="utf-8"))
base = sec["base"].rstrip("/")
proxy = json.loads((ROOT / "config.json").read_text(encoding="utf-8")).get("proxy") or ""
proxies = {"http": proxy, "https": proxy} if proxy else None

s = requests.Session(impersonate="chrome131")
lr = s.post(
    f"{base}/api/login",
    json={"email": sec["email"], "password": sec["password"]},
    headers={"Content-Type": "application/json"},
    timeout=30,
    proxies=proxies,
)
jwt = lr.json()["data"]["token"]
h = {"Authorization": jwt, "Accept": "application/json"}

# list all accounts
al = s.get(f"{base}/api/account/list", headers=h, timeout=30, proxies=proxies)
print("accounts", al.text[:2000])
accounts = al.json().get("data") or []

for acc in accounts:
    aid = acc.get("accountId")
    email = acc.get("email")
    el = s.get(
        f"{base}/api/email/list",
        params={"accountId": aid, "allReceive": 0, "size": 20, "num": 1, "type": 1},
        headers=h,
        timeout=30,
        proxies=proxies,
    )
    print(f"\n=== list accountId={aid} email={email}", el.status_code, el.text[:600])
    data = el.json().get("data") or {}
    lst = data.get("list") or []
    # also try allReceive=1
    el2 = s.get(
        f"{base}/api/email/list",
        params={"accountId": aid, "allReceive": 1, "size": 20, "num": 1, "type": 1},
        headers=h,
        timeout=30,
        proxies=proxies,
    )
    print("allReceive=1", el2.status_code, el2.text[:400])

# brute common detail paths with emailId=0 / 1
paths = [
    "/api/email/info?emailId=1",
    "/api/email/detail?emailId=1",
    "/api/email/get?emailId=1",
    "/api/email/1",
    "/api/email/content?emailId=1",
    "/api/email/read?emailId=1",
    "/api/my/email/info?emailId=1",
]
for path in paths:
    r = s.get(f"{base}{path}", headers=h, timeout=15, proxies=proxies)
    print(path, r.status_code, r.text[:200])

# POST variants
for path, body in [
    ("/api/email/info", {"emailId": 1}),
    ("/api/email/detail", {"emailId": 1}),
    ("/api/email/get", {"emailId": 1}),
]:
    r = s.post(f"{base}{path}", json=body, headers={**h, "Content-Type": "application/json"}, timeout=15, proxies=proxies)
    print("POST", path, r.status_code, r.text[:200])

# fetch frontend for API hints
for path in ("/", "/assets/", "/js/app.js"):
    try:
        r = s.get(f"{base}{path}", timeout=15, proxies=proxies)
        print("page", path, r.status_code, "len", len(r.text or ""), "ctype", r.headers.get("content-type"))
    except Exception as e:
        print("page err", path, e)
