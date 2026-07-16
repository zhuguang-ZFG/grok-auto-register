#!/usr/bin/env python3
"""
SSO cookie → CPA xAI auth JSON（纯 HTTP Device Flow，支持并行）

供 grok-auto-register 号池使用：默认输出到项目 cpa_auths/，代理默认读 config.json。

用法:
  # 批量 SSO，写出多个独立 auth 文件（xai-<email>.json → 默认 cpa_auths/）
  python sso_to_cpa.py --sso sso_list.txt

  # 并行转换（默认 5 线程，可用 --workers 调整）
  python sso_to_cpa.py --sso sso_list.txt --out-dir ./auth_out --workers 10

  # 并行 + 安静模式（成功任务只打印摘要，失败仍输出完整日志）
  python sso_to_cpa.py --sso sso_list.txt --out-dir ./auth_out --workers 10 --quiet

  # 合并到一个 json（key 带 sub 后缀，避免覆盖）
  python sso_to_cpa.py --sso sso_list.txt --out auth_merged.json --merge --workers 8

  # 单行 sso
  python sso_to_cpa.py --sso-cookie 'eyJ...' --out ./auth_out/one.json

  # 指定/禁用代理（默认读项目 config.json 的 proxy 字段）
  python sso_to_cpa.py --sso sso_list.txt --proxy http://127.0.0.1:7897
  python sso_to_cpa.py --sso sso_list.txt --proxy ""

  # 混排行会自动提取 JWT（支持 email|password|sso / 邮箱----密码----sso 等）
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from curl_cffi import requests

# Windows default consoles are often GBK; emoji/CJK in logs must not abort the batch.
def _harden_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_harden_stdio()

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

# 本地代理：--proxy 参数 > GROK_PROXY 环境变量 > 项目 config.json 的 proxy > 空（禁用）
def _default_proxy() -> str:
    env = os.environ.get("GROK_PROXY")
    if env is not None:
        return env.strip()
    try:
        cfg_path = Path(__file__).resolve().parent / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        return str(cfg.get("proxy") or "").strip()
    except Exception:
        return ""


PROXY = _default_proxy()
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else {}

# JWT / SSO cookie 提取
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_JWT_GENERIC_RE = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# 并发写文件 / 打印日志锁
_io_lock = threading.Lock()
_print_lock = threading.Lock()

# 全局进度（主线程/任务结束时更新）
_progress = {
    "total": 0,
    "done": 0,
    "ok": 0,
    "fail": 0,
    "running": 0,
}
_progress_lock = threading.Lock()

TASK_TIMEOUT = 120  # 单任务超时秒数（future.result）


def _safe_write(text: str) -> None:
    """Write to stdout; never raise UnicodeEncodeError on narrow consoles."""
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        data = text.encode(enc, errors="replace")
        buf = getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write(data)
            buf.flush()
        else:
            sys.stdout.write(data.decode(enc, errors="replace"))
            sys.stdout.flush()


def log_block(lines: list[str]) -> None:
    """整块输出一个任务的日志，避免并行交错。"""
    if not lines:
        return
    text = "\n".join(lines).rstrip() + "\n"
    with _print_lock:
        _safe_write(text)


def log_line(msg: str) -> None:
    """Print one line; never crash on Windows gbk consoles (emoji / CJK)."""
    with _print_lock:
        _safe_write(msg + ("\n" if not msg.endswith("\n") else ""))


def progress_snapshot() -> str:
    with _progress_lock:
        return (
            f"进度 {_progress['done']}/{_progress['total']} | "
            f"成功 {_progress['ok']} | 失败 {_progress['fail']} | "
            f"运行中 {_progress['running']}"
        )


def progress_print() -> None:
    # ASCII marker — Windows gbk cannot encode ⏱ and used to abort the whole batch
    log_line(f"[T] {progress_snapshot()}")


def progress_start_one() -> None:
    with _progress_lock:
        _progress["running"] += 1


def progress_finish_one(success: bool) -> None:
    with _progress_lock:
        _progress["running"] = max(0, _progress["running"] - 1)
        _progress["done"] += 1
        if success:
            _progress["ok"] += 1
        else:
            _progress["fail"] += 1


class TaskLog:
    """任务内缓冲日志，结束时一次性 flush。"""

    def __init__(self, tag: str = "") -> None:
        self.tag = tag
        self.lines: list[str] = []

    def __call__(self, msg: str) -> None:
        self.lines.append(f"  {msg}")

    def header(self, title: str) -> None:
        self.lines.append("=" * 60)
        self.lines.append(f"{self.tag}{title}")
        self.lines.append("=" * 60)

    def flush(self) -> None:
        log_block(self.lines)
        self.lines.clear()


def b64url_decode(seg: str) -> bytes:
    seg += "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg)


def decode_jwt_payload(token: str) -> dict:
    try:
        return json.loads(b64url_decode(token.split(".")[1]))
    except Exception:
        return {}


def looks_like_jwt(token: str) -> bool:
    token = token.strip().strip('"').strip("'")
    if token.count(".") != 2:
        return False
    parts = token.split(".")
    if not all(parts):
        return False
    return all(re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts)


def extract_sso_token(raw: str) -> str | None:
    """
    从混排文本中自动提取 SSO JWT。
    支持:
      - 纯 JWT
      - email|password|sso
      - 邮箱----密码----sso
      - 其它分隔符混排，只要行内含 eyJ... 三段式 token
    """
    if not raw:
        return None
    text = raw.strip().strip("\ufeff")
    if not text or text.startswith("#"):
        return None

    matches = _JWT_RE.findall(text)
    if matches:
        return max(matches, key=len)

    matches = _JWT_GENERIC_RE.findall(text)
    for m in matches:
        if looks_like_jwt(m):
            return m

    for sep in ("----", "|", "\t", ",", ";", " "):
        if sep in text:
            parts = [p.strip().strip('"').strip("'") for p in text.split(sep) if p.strip()]
            for part in reversed(parts):
                if looks_like_jwt(part):
                    return part
                m = _JWT_RE.search(part)
                if m:
                    return m.group(0)

    cleaned = text.strip('"').strip("'")
    if looks_like_jwt(cleaned):
        return cleaned

    return None


def extract_email(raw: str) -> str:
    """可选：从混排行提取邮箱。"""
    if not raw:
        return ""
    m = _EMAIL_RE.search(raw)
    return m.group(0) if m else ""


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


def request_device_code(
    session: requests.Session | None = None,
    proxies: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> dict | None:
    emit = log or (lambda m: None)
    p = proxies if proxies is not None else PROXIES
    try:
        caller = session if session is not None else requests
        kwargs: dict = {
            "data": {"client_id": CLIENT_ID, "scope": SCOPES},
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "timeout": 15,
        }
        if p:
            kwargs["proxies"] = p
        r = caller.post(f"{OIDC_ISSUER}/oauth2/device/code", **kwargs)
        return r.json()
    except Exception as e:
        emit(f"❌ device/code 异常: {e}")
        return None


def poll_token(
    device_code: str,
    interval: int,
    expires_in: int,
    timeout: int = 45,
    session: requests.Session | None = None,
    proxies: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> dict | None:
    emit = log or (lambda m: None)
    p = proxies if proxies is not None else PROXIES
    caller = session if session is not None else requests
    deadline = time.time() + min(expires_in, timeout)
    loop_count = 0
    while time.time() < deadline:
        time.sleep(interval)
        loop_count += 1
        remaining = max(0, int(deadline - time.time()))
        try:
            kwargs: dict = {
                "data": {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                },
                "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                "timeout": 10,
            }
            if p:
                kwargs["proxies"] = p
            r = caller.post(f"{OIDC_ISSUER}/oauth2/token", **kwargs)
            if r.ok:
                return r.json()
            err = r.json()
            error = err.get("error", "")
            if error == "authorization_pending":
                if loop_count % 3 == 0:
                    emit(f"⏳ 轮询中... 剩余 {remaining}s")
                continue
            if error == "slow_down":
                interval += 5
                continue
            emit(f"❌ token: {error}")
            return None
        except Exception as e:
            emit(f"❌ token 异常: {e}")
            time.sleep(2)
            continue
    emit("❌ 轮询超时")
    return None


def sso_to_token(
    sso_cookie: str,
    proxies: dict | None = None,
    session: requests.Session | None = None,
    log: Callable[[str], None] | None = None,
) -> dict | None:
    """SSO cookie → token dict (access/refresh/expires_in)"""
    emit = log or (lambda m: None)
    p = proxies if proxies is not None else PROXIES
    if session is not None:
        s = session
    else:
        s = requests.Session()
        if p:
            s.proxies = p
    s.cookies.set("sso", sso_cookie, domain=".x.ai")

    try:
        r = s.get("https://accounts.x.ai/", impersonate="chrome120", timeout=15)
    except Exception as e:
        emit(f"❌ 网络错误: {e}")
        return None
    if "sign-in" in r.url or "sign-up" in r.url:
        emit("❌ sso 无效")
        return None
    emit("✅ sso 有效")

    emit("🔑 Device Flow...")
    dc = request_device_code(session=s, proxies=p, log=emit)
    if not dc:
        return None
    emit(f"📋 user_code: {dc.get('user_code')}")

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
            emit(f"❌ verify 失败: {r.url}")
            return None
    except Exception as e:
        emit(f"❌ verify 异常: {e}")
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
            emit(f"❌ approve 失败: {r.url}")
            return None
        emit("✅ 授权确认")
    except Exception as e:
        emit(f"❌ approve 异常: {e}")
        return None

    token = poll_token(
        dc["device_code"],
        dc.get("interval", 5),
        dc.get("expires_in", 1800),
        session=s,
        proxies=p,
        log=emit,
    )
    if not token:
        return None
    emit(
        f"✅ access_token (expires_in={token.get('expires_in')}s)"
        + (" + refresh_token" if token.get("refresh_token") else "")
    )
    return token


def token_to_cpa_entry(token: dict, email: str = "") -> dict:
    """token → CPA xAI auth entry (对齐 schema.py build_cpa_xai_auth)."""
    access = token.get("access_token") or token.get("key") or ""
    refresh = token.get("refresh_token") or ""
    payload = decode_jwt_payload(access)

    sub = payload.get("sub") or payload.get("principal_id") or ""
    # JWT 里常带 email，优先补全
    jwt_email = (payload.get("email") or "").strip()
    email = (email or "").strip() or jwt_email

    if "exp" in payload:
        exp_ts = float(payload["exp"])
        expires_in = int(max(exp_ts - float(payload.get("iat", exp_ts - 21600)), 0))
        expired = datetime.fromtimestamp(exp_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        expires_in = int(token.get("expires_in") or 21600)
        expired = datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

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
        "email": email,
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
    text = json.dumps(entry, indent=2, ensure_ascii=False) + "\n"
    with _io_lock:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)


def merge_cpa_json(path: Path, entry: dict, unique: bool = True) -> None:
    """
    合并写入。unique=True 时 key 变成 xai::<email|sub>，避免多账号互相覆盖。
    使用锁保证并行安全。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    email = (entry.get("email") or "").strip()
    sub = (entry.get("sub") or "").strip()
    if unique:
        key = f"xai::{email or sub or str(int(time.time() * 1000))}"
    else:
        key = "xai"
    with _io_lock:
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing[key] = entry
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def load_sso_list(path: str | None, single: str | None = None) -> list[tuple[str, str]]:
    """
    返回 [(sso_token, email), ...]
    自动从混排行提取 JWT；无法提取的行跳过。
    """
    raw_lines: list[str] = []
    if single:
        raw_lines.append(single)
    elif path:
        raw_lines.extend(Path(path).read_text(encoding="utf-8").splitlines())
    else:
        return []

    out: list[tuple[str, str]] = []
    skipped = 0
    seen: set[str] = set()

    for i, line in enumerate(raw_lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        token = extract_sso_token(line)
        if not token:
            # 兼容 邮箱----密码----sso 最后一段
            if "----" in line:
                parts = line.split("----")
                cand = parts[-1].strip()
                if looks_like_jwt(cand):
                    token = cand
            if not token:
                skipped += 1
                preview = line if len(line) <= 80 else line[:77] + "..."
                log_line(f"⚠️  跳过第 {i} 行（未找到 JWT）: {preview}")
                continue
        if token in seen:
            continue
        seen.add(token)
        email = extract_email(line)
        out.append((token, email))

    if skipped:
        log_line(f"ℹ️  共跳过 {skipped} 行无法提取的内容")
    return out


def sso_to_auth_file(
    sso: str,
    out_dir: str = "auth_out3",
    proxies: dict | None = None,
    email: str = "",
    session: requests.Session | None = None,
) -> dict | None:
    """SSO cookie → CPA auth JSON 文件，返回 entry dict。
    供 grok.py 注册成功后调用。email 由调用方传入。
    """
    p = proxies if proxies is not None else PROXIES
    token = sso_to_token(sso, proxies=p, session=session)
    if not token:
        print("  ❌ sso2auth 失败")
        return None
    entry = token_to_cpa_entry(token, email=email)
    email = entry.get("email") or ""
    sub = entry.get("sub") or ""
    filepath = Path(out_dir) / credential_file_name(email, sub)
    write_cpa_json(filepath, entry)
    print(f"  💾 {filepath}")
    return entry


def process_one(
    index: int,
    total: int,
    sso: str,
    email: str,
    out: str | None,
    out_dir: str | None,
    merge: bool,
    multi: bool,
    quiet: bool = False,
    proxies: dict | None = None,
) -> tuple[bool, str]:
    """处理单个 SSO。日志缓冲后整块输出，避免并行交错。"""
    tag = f"[{index}/{total}] "
    tlog = TaskLog(tag=tag)
    progress_start_one()
    p = proxies if proxies is not None else PROXIES

    try:
        tlog.header("开始")
        if email:
            tlog(f"📧 {email}")
        tlog(f"🎫 sso={sso[:24]}...{sso[-12:]}" if len(sso) > 40 else f"🎫 sso={sso}")
        token = sso_to_token(sso, proxies=p, log=tlog)
        if not token:
            tlog("❌ 失败")
            success = False
            summary = f"{tag}失败"
        else:
            entry = token_to_cpa_entry(token, email=email)
            email_out = entry.get("email") or ""
            sub = entry.get("sub") or ""
            label = email_out[:24] if email_out else (sub[:12] if sub else "?")

            if out_dir:
                fp = Path(out_dir) / credential_file_name(email_out, sub)
                write_cpa_json(fp, entry)
                tlog(f"💾 {fp}")
            if out:
                if merge or multi:
                    merge_cpa_json(Path(out), entry, unique=True)
                    tlog(f"💾 merge → {out}")
                else:
                    write_cpa_json(Path(out), entry)
                    tlog(f"💾 {out}")

            tlog(f"✅ 完成 email={label}...")
            success = True
            summary = f"{tag}完成 email={label}..."
    except Exception as e:
        tlog(f"❌ 异常: {e}")
        success = False
        summary = f"{tag}异常: {e}"

    if quiet and success:
        head = tlog.lines[:3] if len(tlog.lines) >= 3 else tlog.lines[:]
        tail = [ln for ln in tlog.lines if "✅ 完成" in ln or "💾" in ln or "📧" in ln]
        tlog.lines = head + tail

    tlog.flush()
    progress_finish_one(success)
    progress_print()
    return success, summary


def main() -> int:
    ap = argparse.ArgumentParser(description="SSO cookie → CPA xAI auth JSON（纯 HTTP，支持并行）")
    ap.add_argument(
        "--sso",
        metavar="FILE",
        help="sso 列表文件（自动提取 JWT；支持纯 token / email|pass|sso / 邮箱----密码----sso）",
    )
    ap.add_argument(
        "--sso-cookie",
        metavar="TEXT",
        help="单个 sso cookie 或混排文本（自动提取 JWT）",
    )
    ap.add_argument("--out", default=None, help="输出路径（单账号或 --merge 合并文件）")
    ap.add_argument(
        "--out-dir",
        default=None,
        help="批量时每个账号写一个 xai-<email>.json",
    )
    ap.add_argument(
        "--merge",
        action="store_true",
        help="合并到 --out，key 用 xai::<email|sub>",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=5,
        help="并行线程数（默认 5；设为 1 则串行）",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=0,
        help="提交任务时的错峰间隔秒数（避免同时打满，默认 0）",
    )
    ap.add_argument(
        "--task-timeout",
        type=int,
        default=TASK_TIMEOUT,
        help=f"单任务 future 超时秒数（默认 {TASK_TIMEOUT}）",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="安静模式：成功任务只打印摘要，失败仍输出完整日志",
    )
    ap.add_argument(
        "--email",
        default="",
        help="写入 entry.email（可选；混排行已含邮箱时会自动提取并优先使用）",
    )
    ap.add_argument(
        "--proxy",
        default=None,
        help="HTTP 代理（默认读项目 config.json 的 proxy；传空字符串禁用）",
    )
    args = ap.parse_args()

    global PROXY, PROXIES
    if args.proxy is not None:
        PROXY = args.proxy.strip()
        PROXIES = {"http": PROXY, "https": PROXY} if PROXY else {}

    items = load_sso_list(args.sso, args.sso_cookie)
    if not items:
        ap.error("需要 --sso 或 --sso-cookie，且至少能提取到 1 个 JWT")

    if len(items) > 1 and not args.out_dir and not args.merge:
        args.out_dir = args.out_dir or str(Path(__file__).resolve().parent / "cpa_auths")
        print(f"批量模式默认 --out-dir {args.out_dir}")

    if args.out is None and args.out_dir is None and len(items) == 1:
        args.out_dir = str(Path(__file__).resolve().parent / "cpa_auths")

    workers = max(1, int(args.workers or 1))
    multi = len(items) > 1
    if len(items) == 1:
        workers = 1

    task_timeout = max(1, int(args.task_timeout or TASK_TIMEOUT))
    total = len(items)
    with _progress_lock:
        _progress["total"] = total
        _progress["done"] = 0
        _progress["ok"] = 0
        _progress["fail"] = 0
        _progress["running"] = 0

    print(
        f"[*] SSO -> CPA auth: {total} 个, workers={workers}, delay={args.delay}s, "
        f"task_timeout={task_timeout}s"
        + (f", proxy={PROXY}" if PROXY else ", proxy=off")
        + (", quiet" if args.quiet else "")
    )
    progress_print()

    ok = 0
    fail = 0
    timeout_count = 0

    def email_for(item_email: str) -> str:
        return item_email or args.email or ""

    if workers == 1:
        for i, (sso, item_email) in enumerate(items, 1):
            success, _ = process_one(
                i,
                total,
                sso,
                email_for(item_email),
                args.out,
                args.out_dir,
                args.merge,
                multi,
                quiet=args.quiet,
                proxies=PROXIES,
            )
            if success:
                ok += 1
            else:
                fail += 1
            if args.delay > 0 and i < total:
                time.sleep(args.delay)
    else:
        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, (sso, item_email) in enumerate(items, 1):
                fut = pool.submit(
                    process_one,
                    i,
                    total,
                    sso,
                    email_for(item_email),
                    args.out,
                    args.out_dir,
                    args.merge,
                    multi,
                    args.quiet,
                    PROXIES,
                )
                futures[fut] = i
                if args.delay > 0 and i < total:
                    time.sleep(args.delay)

            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    success, _ = fut.result(timeout=task_timeout)
                except Exception as e:
                    success = False
                    timeout_count += 1
                    log_line(f"⏰ [{idx}] 任务超时或异常: {e}")
                    # process_one 若已 finish 则不会再进来；超时/未执行完时补记
                    with _progress_lock:
                        # 若 running 仍含该任务，做一次兜底结算
                        if _progress["done"] < total and _progress["running"] > 0:
                            pass
                    progress_finish_one(False)
                    progress_print()
                if success:
                    ok += 1
                else:
                    fail += 1

    print(
        f"\n{'=' * 60}\n📊 完成: {ok}/{total} 成功, {fail} 失败"
        + (f", {timeout_count} 超时" if timeout_count else "")
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
