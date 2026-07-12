#!/usr/bin/env python3
"""
SSO cookie → CPA xai-*.json（纯 HTTP Device Flow，供 CLIProxy 号池）

用法:
  # 单个 / 批量 SSO，写出多个独立 auth 文件（每个可直接 cp 到 ~/.grok/auth.json）
  python3 sso_to_auth_json.py --sso sso_list.txt --out-dir ./auth_out

  # 合并到一个 json（key 带 user_id 后缀，避免覆盖）
  python3 sso_to_auth_json.py --sso sso_list.txt --out auth_merged.json --merge

  # 单行 sso
  python3 sso_to_auth_json.py --sso-cookie 'eyJ...' --out ~/.grok/auth.json
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

PROXY = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}
MAX_WORKERS = 5

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_ISSUER = "https://auth.x.ai"
SCOPES = (
    "openid profile email offline_access grok-cli:access "
    "api:access conversations:read conversations:write"
)

# CPA xAI auth schema constants
DEFAULT_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
DEFAULT_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:56121/callback"
DEFAULT_CLIENT_HEADERS: dict[str, str] = {
    "x-grok-client-version": "0.2.93",
    "x-xai-token-auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-shell",
    "User-Agent": "grok-shell/0.2.93 (linux; x86_64)",
}


def b64url_decode(seg: str) -> bytes:
    seg += "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg)


def decode_jwt_payload(token: str) -> dict:
    try:
        return json.loads(b64url_decode(token.split(".")[1]))
    except Exception:
        return {}


def _sanitize_file_segment(value: str) -> str:
    """CPA CredentialFileName 风格的文件名清理."""
    value = (value or "").strip()
    if not value:
        return ""
    out: list[str] = []
    for ch in value:
        if (
            ("a" <= ch <= "z")
            or ("A" <= ch <= "Z")
            or ("0" <= ch <= "9")
            or ch in {"@", ".", "_", "-"}
        ):
            out.append(ch)
        else:
            out.append("-")
    return "".join(out).strip("-")


def credential_file_name(email: str = "", sub: str = "") -> str:
    """xai-<email>.json，回退 sub 或时间戳."""
    email_s = _sanitize_file_segment(email)
    if email_s:
        return f"xai-{email_s}.json"
    sub_s = _sanitize_file_segment(sub)
    if sub_s:
        return f"xai-{sub_s}.json"
    ts = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return f"xai-{ts}.json"


def request_device_code(session: requests.Session | None = None, proxies: dict | None = None) -> dict | None:
    p = proxies or PROXY
    try:
        caller = session if session is not None else requests
        r = caller.post(
            f"{OIDC_ISSUER}/oauth2/device/code",
            data={"client_id": CLIENT_ID, "scope": SCOPES},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            proxies=p,
            timeout=15,
        )
        return r.json()
    except Exception as e:
        print(f"  ❌ device/code 异常: {e}")
        return None


def poll_token(device_code: str, interval: int, expires_in: int, timeout: int = 45, session: requests.Session | None = None, proxies: dict | None = None) -> dict | None:
    p = proxies or PROXY
    caller = session if session is not None else requests
    deadline = time.time() + min(expires_in, timeout)
    loop_count = 0
    while time.time() < deadline:
        time.sleep(interval)
        loop_count += 1
        remaining = max(0, int(deadline - time.time()))
        try:
            r = caller.post(
                f"{OIDC_ISSUER}/oauth2/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                proxies=p,
                timeout=10,
            )
            if r.ok:
                return r.json()
            err = r.json()
            error = err.get("error", "")
            if error == "authorization_pending":
                if loop_count % 3 == 0:
                    print(f"  ⏳ 轮询中... 剩余 {remaining}s")
                continue
            if error == "slow_down":
                interval += 5
                continue
            print(f"  ❌ token: {error}")
            return None
        except Exception as e:
            print(f"  ❌ token 异常: {e}")
            time.sleep(2)
            continue
    print("  ❌ 轮询超时")
    return None


def sso_to_token(sso_cookie: str, proxies: dict | None = None, session: requests.Session | None = None) -> dict | None:
    """SSO cookie → token dict (access/refresh/expires_in)"""
    p = proxies or PROXY
    if session is not None:
        s = session
    else:
        s = requests.Session()
        s.proxies = p
    s.cookies.set("sso", sso_cookie, domain=".x.ai")

    try:
        r = s.get("https://accounts.x.ai/", impersonate="chrome120", timeout=15)
    except Exception as e:
        print(f"  ❌ 网络错误: {e}")
        return None
    if "sign-in" in r.url or "sign-up" in r.url:
        print("  ❌ sso 无效")
        return None
    print("  ✅ sso 有效")

    print("  🔑 Device Flow...")
    dc = request_device_code(session=s, proxies=p)
    if not dc:
        return None
    print(f"  📋 user_code: {dc.get('user_code')}")

    try:
        s.get(dc["verification_uri_complete"], impersonate="chrome120", timeout=15)
        r = s.post(
            f"{OIDC_ISSUER}/oauth2/device/verify",
            data={"user_code": dc["user_code"]},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate="chrome120",
            timeout=15,
            allow_redirects=True,
        )
        if "consent" not in r.url:
            print(f"  ❌ verify 失败: {r.url}")
            return None
    except Exception as e:
        print(f"  ❌ verify 异常: {e}")
        return None

    try:
        r = s.post(
            f"{OIDC_ISSUER}/oauth2/device/approve",
            data={
                "user_code": dc["user_code"],
                "action": "allow",
                "principal_type": "User",
                "principal_id": "",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate="chrome120",
            timeout=15,
            allow_redirects=True,
        )
        if "done" not in r.url:
            print(f"  ❌ approve 失败: {r.url}")
            return None
        print("  ✅ 授权确认")
    except Exception as e:
        print(f"  ❌ approve 异常: {e}")
        return None

    token = poll_token(
        dc["device_code"],
        dc.get("interval", 5),
        dc.get("expires_in", 1800),
        session=s,
        proxies=p,
    )
    if not token:
        return None
    print(
        f"  ✅ access_token (expires_in={token.get('expires_in')}s)"
        + (" + refresh_token" if token.get("refresh_token") else "")
    )
    return token


def token_to_cpa_entry(token: dict, email: str = "") -> dict:
    """token → CPA xAI auth entry (对齐 schema.py build_cpa_xai_auth)."""
    access = token.get("access_token") or token.get("key") or ""
    refresh = token.get("refresh_token") or ""
    payload = decode_jwt_payload(access)

    sub = payload.get("sub") or payload.get("principal_id") or ""

    if "exp" in payload:
        exp_ts = float(payload["exp"])
        expires_in = int(max(exp_ts - float(payload.get("iat", exp_ts - 21600)), 0))
        expired = datetime.fromtimestamp(exp_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        expires_in = int(token.get("expires_in") or 21600)
        expired = datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    last_refresh = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entry = {
        "type": "xai",
        "auth_kind": "oauth",
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": int(expires_in),
        "expired": expired,
        "last_refresh": last_refresh,
        "email": (email or "").strip(),
        "sub": sub.strip(),
        "base_url": DEFAULT_BASE_URL,
        "token_endpoint": DEFAULT_TOKEN_ENDPOINT,
        "redirect_uri": DEFAULT_REDIRECT_URI,
        "disabled": False,
        "headers": dict(DEFAULT_CLIENT_HEADERS),
    }
    if token.get("id_token"):
        entry["id_token"] = token["id_token"].strip()
    return entry


def write_cpa_json(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_sso_list(path: str | None) -> list[str]:
    if not path:
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 兼容 邮箱----密码----sso
        if "----" in line:
            parts = line.split("----")
            line = parts[-1].strip()
        out.append(line)
    return out


_print_lock = threading.Lock()
_counter_lock = threading.Lock()


def _ts_print(*args, **kwargs):
    with _print_lock:
        kwargs.setdefault("flush", True)
        print(*args, **kwargs)


def sso_to_auth_file(sso: str, out_dir: str = "auth_out3", proxies: dict | None = None, email: str = "", session: requests.Session | None = None) -> dict | None:
    """SSO cookie → CPA auth JSON 文件，返回 entry dict。
    供 grok.py 注册成功后调用。email 由调用方传入。
    """
    p = proxies or PROXY
    token = sso_to_token(sso, proxies=p, session=session)
    if not token:
        print(f"  ❌ sso2auth 失败")
        return None
    entry = token_to_cpa_entry(token, email=email)
    email = entry.get("email") or ""
    sub = entry.get("sub") or ""
    filepath = Path(out_dir) / credential_file_name(email, sub)
    write_cpa_json(filepath, entry)
    print(f"  💾 {filepath}")
    return entry


def process_sso(idx: int, total: int, sso: str, out_dir: str, proxies: dict | None = None) -> tuple[int, bool]:
    """处理单个 SSO，返回 (index, success)"""
    _ts_print(f"\n{'=' * 60}\n[{idx}/{total}] ...\n{'=' * 60}")
    try:
        token = sso_to_token(sso, proxies=proxies)
        if not token:
            _ts_print(f"  ❌ [{idx}] 失败")
            return idx, False
        entry = token_to_cpa_entry(token)
        email = entry.get("email") or ""
        sub = entry.get("sub") or ""

        if out_dir:
            p = Path(out_dir) / credential_file_name(email, sub)
            write_cpa_json(p, entry)
            _ts_print(f"  💾 {p}")
        _ts_print(f"  ✅ [{idx}] 完成 email={email[:24] if email else sub[:12]}...")
        return idx, True
    except Exception as e:
        _ts_print(f"  ❌ [{idx}] 异常: {e}")
        return idx, False


TASK_TIMEOUT = 120  # 单任务超时秒数

def main():
    import argparse
    ap = argparse.ArgumentParser(description="SSO cookie → CPA xai-*.json for CLIProxy pool")
    ap.add_argument("--sso", default="", help="SSO list file (one cookie per line, or email----pass----sso)")
    ap.add_argument("--sso-cookie", default="", help="single SSO cookie")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "cpa_auths"), help="output CPA auth dir")
    ap.add_argument("--proxy", default="http://127.0.0.1:7890", help="HTTP proxy for xAI OIDC")
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = ap.parse_args()

    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
    global PROXY
    if proxies:
        PROXY = proxies

    cookies = []
    if args.sso_cookie:
        cookies = [args.sso_cookie.strip()]
    elif args.sso:
        cookies = load_sso_list(args.sso)
    else:
        ap.error("need --sso file or --sso-cookie")

    out_dir = args.out_dir
    total = len(cookies)
    print(f"SSO → CPA: {total} (workers={args.workers}, timeout={TASK_TIMEOUT}s) → {out_dir}")

    ok = 0
    fail = 0
    timeout_count = 0

    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures = {
            executor.submit(process_sso, i, total, sso, out_dir, PROXY): i
            for i, sso in enumerate(cookies, 1)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                _, success = future.result(timeout=TASK_TIMEOUT)
            except Exception as e:
                print(f"  ⏰ [{idx}] 任务超时或异常: {e}")
                success = False
                timeout_count += 1
            if success:
                with _counter_lock:
                    ok += 1
            else:
                with _counter_lock:
                    fail += 1

    print(f"\n{'=' * 60}\n📊 完成: {ok}/{total} 成功, {fail} 失败, {timeout_count} 超时")
    return 0 if fail == 0 else 1

if __name__ == '__main__':
    main()