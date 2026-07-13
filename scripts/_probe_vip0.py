#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot probe for vip0.xyz Cloud Mail API + CapSolver. Not for production import."""

from __future__ import annotations

import json
import secrets
import string
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from curl_cffi import requests  # noqa: E402


def load_cfg() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def capsolver_solve(api_key: str, website_url: str, sitekey: str, proxy: str = "") -> str:
    task: dict
    if proxy:
        u = urlparse(proxy)
        task = {
            "type": "AntiTurnstileTask",
            "websiteURL": website_url,
            "websiteKey": sitekey,
            "proxyType": "http",
            "proxyAddress": u.hostname or "127.0.0.1",
            "proxyPort": int(u.port or 7897),
        }
        if u.username:
            task["proxyLogin"] = u.username
        if u.password:
            task["proxyPassword"] = u.password
    else:
        task = {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": sitekey,
        }
    r = requests.post(
        "https://api.capsolver.com/createTask",
        json={"clientKey": api_key, "task": task},
        timeout=60,
    )
    data = r.json()
    print("createTask", {k: data.get(k) for k in ("errorId", "errorCode", "errorDescription", "taskId", "status")})
    tid = data.get("taskId")
    if not tid:
        raise RuntimeError(f"createTask failed: {data}")
    for i in range(40):
        time.sleep(2)
        jd = requests.post(
            "https://api.capsolver.com/getTaskResult",
            json={"clientKey": api_key, "taskId": tid},
            timeout=60,
        ).json()
        st = jd.get("status")
        print(
            f"poll {i} status={st} err={jd.get('errorId')} "
            f"{jd.get('errorDescription') or ''} token_len="
            f"{len(((jd.get('solution') or {}).get('token') or ''))}"
        )
        if st == "ready":
            token = (jd.get("solution") or {}).get("token") or ""
            if not token:
                raise RuntimeError(f"ready but empty token: {jd}")
            return token
        if st == "failed" or (jd.get("errorId") and st != "processing"):
            raise RuntimeError(f"solve failed: {jd}")
    raise RuntimeError("solve timeout")


def main() -> int:
    cfg = load_cfg()
    api_key = (cfg.get("capsolver_api_key") or "").strip()
    proxy = (cfg.get("proxy") or "").strip()
    # credentials from env or local secrets file (gitignored)
    secrets_path = ROOT / "secrets" / "vip0_mail.json"
    if not secrets_path.is_file():
        secrets_path = ROOT / "vip0_mail.local.json"
    if secrets_path.is_file():
        sec = json.loads(secrets_path.read_text(encoding="utf-8"))
        login_email = sec.get("email") or ""
        login_pw = sec.get("password") or ""
        base = (sec.get("base") or "https://vip0.xyz").rstrip("/")
        sitekey = sec.get("sitekey") or "0x4AAAAAAD0dY6S9OmQL32yO"
        account_id = sec.get("accountId")
    else:
        print("MISSING secrets file:", secrets_path)
        print("Create vip0_mail.local.json with email/password")
        return 2

    print("proxy", proxy or "(none)")
    print("base", base, "login", login_email)

    proxies = {"http": proxy, "https": proxy} if proxy else None
    s = requests.Session(impersonate="chrome131")
    lr = s.post(
        f"{base}/api/login",
        json={"email": login_email, "password": login_pw},
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        timeout=30,
        proxies=proxies,
    )
    print("login", lr.status_code, lr.text[:220])
    lj = lr.json()
    jwt = None
    if isinstance(lj.get("data"), dict):
        jwt = lj["data"].get("token")
    if not jwt:
        print("login failed shape", lj)
        return 3
    headers = {
        "Authorization": jwt,  # no Bearer
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    al = s.get(f"{base}/api/account/list", headers=headers, timeout=30, proxies=proxies)
    print("account/list", al.status_code, al.text[:1500])

    if account_id is None:
        try:
            data = al.json().get("data")
            if isinstance(data, list) and data:
                account_id = data[0].get("accountId") or data[0].get("id")
            elif isinstance(data, dict):
                lst = data.get("list") or data.get("records") or []
                if lst:
                    account_id = lst[0].get("accountId") or lst[0].get("id")
        except Exception as e:
            print("parse accountId", e)
    print("accountId", account_id)

    if account_id is not None:
        el = s.get(
            f"{base}/api/email/list",
            params={
                "accountId": account_id,
                "allReceive": 0,
                "size": 20,
                "num": 1,
                "type": 1,
            },
            headers=headers,
            timeout=30,
            proxies=proxies,
        )
        print("email/list", el.status_code, el.text[:1200])

    wc = s.get(f"{base}/api/setting/websiteConfig", timeout=30, proxies=proxies)
    print("websiteConfig", wc.status_code, wc.text[:800])

    if not api_key:
        print("no capsolver key — skip add")
        return 0

    # try proxyless then proxy
    token = None
    for use_proxy in (False, True):
        try:
            print("=== CapSolver use_proxy=", use_proxy)
            token = capsolver_solve(
                api_key,
                base + "/",
                sitekey,
                proxy if use_proxy else "",
            )
            print("got token len", len(token))
            break
        except Exception as e:
            print("capsolver fail", e)
            token = None

    if not token:
        print("cannot solve turnstile — fixed-inbox only path remains")
        return 4

    uname = "tmp" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    new_email = f"{uname}@vip0.xyz"
    ar = s.post(
        f"{base}/api/account/add",
        json={"email": new_email, "password": "Aa123456!", "token": token},
        headers={**headers, "Content-Type": "application/json"},
        timeout=30,
        proxies=proxies,
    )
    print("account/add", ar.status_code, ar.text[:800])
    print("new_email", new_email)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
