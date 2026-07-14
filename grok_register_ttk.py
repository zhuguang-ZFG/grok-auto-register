#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grok 注册机 - TTK GUI 版本
整合 DrissionPage_example.py, openai_register.py, batch_open_nsfw.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import datetime
import time
import os
import sys
import gc
import queue
import secrets
import struct
import random
import re
import string
import json
from pathlib import Path

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")
# Windows file redirects / Git Bash often use gbk; force UTF-8 log lines.
try:
    import stdio_utf8  # noqa: F401
except Exception:
    pass

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
from curl_cffi import requests


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
MEMORY_CLEANUP_INTERVAL = 5

UI_BG = "#242424"
UI_PANEL_BG = "#2b2b2b"
UI_FG = "#f2f2f2"
UI_MUTED_FG = "#b8b8b8"
UI_ENTRY_BG = "#333333"
UI_BUTTON_BG = "#3a3a3a"
UI_ACTIVE_BG = "#4a6078"

DEFAULT_CONFIG = {
    "duckmail_api_key": "",
    "cloudflare_api_base": "",
    "cloudflare_api_key": "",
    "cloudflare_auth_mode": "none",
    "cloudflare_path_domains": "/api/domains",
    "cloudflare_path_accounts": "/api/new_address",
    "cloudflare_path_token": "/api/token",
    "cloudflare_path_messages": "/api/mails",
    "proxy": "http://127.0.0.1:7890",
    "enable_nsfw": True,
    "register_count": 1,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    # UA pool often hurts more than helps with real Chrome; viewport/tz safer A/B.
    "anti_detect_ua_pool": False,
    "anti_detect_viewport": True,
    "anti_detect_tz_locale": True,
    "clash_rotate_per_account": True,
    "clash_api": "http://127.0.0.1:9097",
    "clash_secret": "set-your-secret",
    "capsolver_api_key": "",
    "grok2api_auto_add_local": True,
    "grok2api_local_token_file": "",
    "grok2api_pool_name": "ssoBasic",
    "grok2api_auto_add_remote": False,
    "grok2api_remote_base": "",
    "grok2api_remote_app_key": "",
    "cpa_export_enabled": True,
    "cpa_auth_dir": "cpa_auths",
    "cpa_proxy": "",
    "cpa_headless": False,
    "cpa_probe_after_write": True,
    "cpa_mint_timeout_sec": 240,
    "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
    "cpa_force_standalone": False,
    "cpa_mint_cookie_inject": True,
    "cpa_mint_browser_reuse": True,
    "cpa_mint_browser_recycle_every": 15,
    "cpa_hotload_dir": "",
    "cpa_copy_to_hotload": False,
    "cpa_server_host": "",
    "cpa_server_user": "root",
    "cpa_server_password": "",
    "cpa_server_auth_dir": "",
    "token_only_file": "",
    "concurrent_count": 1,
    "browser_restart_every": 10,
    "cpa_probe_after_write": False,
    "cpa_mint_async": True,
    # mint worker pool (community R+M): 0=legacy unbound threads; -1/auto=min(concurrent,4)
    "cpa_mint_workers": -1,
    "cpa_mint_queue_max": -1,
    "cpa_mint_queue_block_sec": 30,
    "browser_use_custom_ua": False,
    # 注册/铸造浏览器移出屏幕，避免抢焦点挡操作（仍是有头，非 headless）
    "hide_window": True,
    # 可选：网络层拦图片/字体/媒体省带宽（默认关，验证页慎开）
    "block_media_fonts": False,
    "log_level": "info",
    "speed_log_interval_sec": 60,
    "auto_pipeline": True,
    "auto_loop": False,
    "auto_loop_count": 1,
    "auto_loop_pause_sec": 45,
    "auto_loop_max_rounds": 0,
    "local_grok_auth_auto": False,
    "local_grok_auth_path": "",
    "preferred_model": "grok-4.5",
    # Fixed-inbox OTP backup (mailsapi get-code). NOT bulk register.
    "mailsapi_email": "",
    "mailsapi_get_code_url": "",
    "mailsapi_entries": [],
    "mailsapi_lines": [],
    "mailsapi_credentials_file": "mail_credentials.txt",
    "mailsapi_direct": True,
    "mailsapi_accept_cached_code": False,
    "mailsapi_resend_after_sec": 45,
    # Hotmail/Outlook pool (optional; not default). See hotmail_pool.py
    "hotmail_pool_path": "data/hotmail_pool.txt",
    "hotmail_preflight_token": True,
    "hotmail_pop_max_try": 5,
    "hotmail_imap_host": "outlook.office365.com",
    "hotmail_client_id": "",
    "hotmail_resend_after_sec": 45,
    # Mix Hotmail into Cloudflare domain registration to diversify risk.
    # Only applies when email_provider is cloudflare/mixed (not pure hotmail).
    "email_mix_hotmail": True,
    "email_mix_hotmail_ratio": 0.6,
    # Mix community Cloud Mail (vip0.xyz multi-suffix) — buffer only, exclusive with hotmail roll.
    "email_mix_cloud_mail": True,
    "email_mix_cloud_mail_ratio": 0.1,
    "cloud_mail_credentials_file": "vip0_mail.local.json",
    "cloud_mail_domains": ["vip0.xyz"],
    "cloud_mail_domain_mode": "random",
    # 云梦无限邮箱 (ym-mail.ymmynb.com) — public temp domains, buffer only.
    "yunmeng_base": "https://ym-mail.ymmynb.com",
    "yunmeng_api_version": "1.4",
    "yunmeng_domain": "",
    "yunmeng_domains": [],
    "yunmeng_prefix_len": 12,
    "email_mix_yunmeng": False,
    "email_mix_yunmeng_ratio": 0.05,
    # TempMail.lol / mail.tm public buffers (small mix only)
    "email_mix_tempmail_lol": False,
    "email_mix_tempmail_lol_ratio": 0.05,
    "email_mix_mailtm": False,
    "email_mix_mailtm_ratio": 0.05,
    "mailtm_api_base": "https://api.mail.tm",
    "mailtm_domain": "",
    # GPTMail (mail.chatgpt.org.uk) — public key gpt-test may be disabled; set gptmail_api_key
    "gptmail_base": "https://mail.chatgpt.org.uk",
    "gptmail_api_key": "gpt-test",
    "email_mix_gptmail": False,
    "email_mix_gptmail_ratio": 0.03,
    # Long-run stability (community-aligned): metrics + SSO egress heal + daily cap
    "reg_metrics_enabled": True,
    "reg_metrics_path": "logs/reg_metrics.jsonl",
    "sso_cookie_timeout_sec": 150,
    "sso_timeout_rotate_enabled": True,
    "sso_timeout_rotate_after": 2,
    "register_daily_success_cap": 0,
}
config = DEFAULT_CONFIG.copy()

# Anti-detect thread-local fingerprint + timezone (set by create_browser_options,
# read by request/cookie layers to keep UA / sec-ch-ua / Accept-Language consistent).
_thread_fp = [None]  # type: ignore[var-annotated]
tz_env = [None]  # type: ignore[var-annotated]
_cf_domain_index = 0
_cf_domain_lock = threading.Lock()
_cf_email_api_base = {}  # email -> api_base used at create time
_CF_DOMAIN_INDEX_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".domain_rr_index"
)


def _load_domain_rr_index():
    global _cf_domain_index
    try:
        with open(_CF_DOMAIN_INDEX_FILE, "r", encoding="utf-8") as f:
            _cf_domain_index = max(int((f.read() or "0").strip() or "0"), 0)
    except Exception:
        _cf_domain_index = 0


def _save_domain_rr_index():
    try:
        with open(_CF_DOMAIN_INDEX_FILE, "w", encoding="utf-8") as f:
            f.write(str(int(_cf_domain_index)))
    except Exception:
        pass


_load_domain_rr_index()
_io_lock = threading.Lock()
_stats_lock = threading.Lock()
_cpa_threads_lock = threading.Lock()

_LOG_LEVEL_RANK = {
    "quiet": 10,
    "info": 20,
    "debug": 30,
}


class RegistrationCancelled(Exception):
    pass


class AccountRetryNeeded(Exception):
    pass


def get_log_level():
    raw = str(config.get("log_level", "info") or "info").strip().lower()
    return raw if raw in _LOG_LEVEL_RANK else "info"


def message_log_rank(message):
    """根据消息内容推断日志级别。"""
    text = str(message or "")
    if "[Debug]" in text:
        return _LOG_LEVEL_RANK["debug"]
    # quiet 仅保留关键进度/结果/警告
    if text.startswith("--- "):
        return _LOG_LEVEL_RANK["info"]
    quiet_prefixes = ("[+]", "[-]", "[!]")
    if text.lstrip().startswith(quiet_prefixes) or any(
        f" {p}" in text[:12] for p in quiet_prefixes
    ):
        return _LOG_LEVEL_RANK["quiet"]
    if "[*] 速度统计" in text or text.lstrip().startswith("[*] 速度统计"):
        return _LOG_LEVEL_RANK["quiet"]
    if any(
        key in text
        for key in (
            "[*] 1.",
            "[*] 2.",
            "[*] 3.",
            "[*] 4.",
            "[*] 5.",
            "[*] 6.",
            "[*] 终端模式",
            "[*] 配置已保存",
            "[*] 任务结束",
            "[*] 注册成功",
            "[+] 注册成功",
            "Worker-",
            "浏览器已启动",
            "开始执行",
            "成功账号将实时保存",
            "按 Ctrl+C",
            "Cloudflare 拦截",
        )
    ):
        return _LOG_LEVEL_RANK["quiet"]
    return _LOG_LEVEL_RANK["info"]


def should_emit_log(message, level=None):
    configured = _LOG_LEVEL_RANK[get_log_level()]
    if level is not None:
        msg_rank = _LOG_LEVEL_RANK.get(str(level).lower(), _LOG_LEVEL_RANK["info"])
    else:
        msg_rank = message_log_rank(message)
    return msg_rank <= configured


def emit_log(log_callback, message, *, level=None):
    if not log_callback:
        return
    if not should_emit_log(message, level=level):
        return
    log_callback(message)


class RateMeter:
    """按固定间隔汇总创建速度（全局一条，避免每 worker 各打一条）。"""

    def __init__(self, interval_sec=60):
        # 允许测试用更短间隔；生产默认 60s
        self.interval_sec = max(float(interval_sec or 60), 1.0)
        self.t0 = time.time()
        self.last_tick = self.t0
        self.last_success = 0
        self._lock = threading.Lock()

    def format_line(self, success, fail=0, force=False):
        now = time.time()
        with self._lock:
            elapsed = now - self.last_tick
            if not force and elapsed < self.interval_sec:
                return None
            success = int(success or 0)
            fail = int(fail or 0)
            delta = max(success - self.last_success, 0)
            # 正常按实际窗口折算；极短窗口（force 收尾/刚启动）用 interval 估，避免天文数字
            if elapsed >= 1.0:
                window = elapsed
            else:
                window = self.interval_sec
            rate = delta * 60.0 / window
            total_sec = max(now - self.t0, 0.0)
            total_min = total_sec / 60.0
            # 运行不足 1s 时平均速度与窗口速率对齐，避免 540/min 这类瞬时噪声
            if total_sec >= 1.0:
                avg = success * 60.0 / total_sec
            else:
                avg = rate
            self.last_tick = now
            self.last_success = success
            return (
                f"[*] 速度统计: 成功 {rate:.0f}/min | 本分钟成功 {delta} "
                f"| 累计成功 {success} | 累计失败 {fail} | 运行 {total_min:.1f}min | 平均 {avg:.1f}/min"
            )

    def maybe_log(self, log_callback, success, fail=0, force=False):
        line = self.format_line(success, fail=fail, force=force)
        if line:
            emit_log(log_callback, line, level="quiet")


def start_speed_logger(get_counts, log_callback, stop_event, interval_sec=60):
    """后台每 interval 打印一次全局速度；stop 后打印最终摘要。"""

    meter = RateMeter(interval_sec=interval_sec)

    def _loop():
        while True:
            if stop_event.wait(timeout=meter.interval_sec):
                break
            try:
                success, fail = get_counts()
            except Exception:
                success, fail = 0, 0
            meter.maybe_log(log_callback, success, fail, force=True)
        try:
            success, fail = get_counts()
        except Exception:
            success, fail = 0, 0
        meter.maybe_log(log_callback, success, fail, force=True)

    thread = threading.Thread(target=_loop, name="speed-logger", daemon=True)
    thread.start()
    return thread, meter


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            config = {**DEFAULT_CONFIG, **loaded}
        except Exception:
            config = DEFAULT_CONFIG.copy()
    return config


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"保存配置失败: {e}")


def ensure_stable_python_runtime():
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(
            f"[*] 检测到 Python {sys.version.split()[0]}，自动切换到更稳定的解释器: {candidate}"
        )
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        print(
            "[提示] 当前 Python 为 3.14+；若出现 Mail.tm TLS 异常，建议改用 Python 3.12 或 3.13。"
        )


ensure_stable_python_runtime()
warn_runtime_compatibility()

load_config()

EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)


DUCKMAIL_API_BASE = "https://api.duckmail.sbs"


def get_proxies():
    # Prefer runtime-selected HTTP proxy (http_proxy_pool / env), then config.proxy
    proxy = (
        str(config.get("_runtime_http_proxy") or "").strip()
        or str(__import__("os").environ.get("GROK_HTTP_PROXY") or "").strip()
        or str(config.get("proxy", "") or "").strip()
    )
    if proxy:
        return {"http": proxy, "https": proxy}
    return {}


def rotate_egress_proxy(log_fn=None):
    """Rotate Clash node and/or HTTP list proxy before each registration.

    Community HTTP lists (e.g. all_proxies.txt) are often overseas-only; they
    only apply when http_proxy_enabled and pick() succeeds.
    Returns dict {clash_node, http_proxy}.
    """
    out = {"clash_node": None, "http_proxy": None}
    prefer_http = bool(config.get("http_proxy_prefer_over_clash"))
    http_url = None
    clash_node = None
    # When throttle skips this round, we must NOT fall back to the HTTP pool
    # (overseas-only, unreachable from CN host) — keep the current egress as-is.
    clash_throttled = {"v": False}

    def _try_http():
        nonlocal http_url
        try:
            import http_proxy_pool as hpp

            if hpp.is_available(config):
                http_url = hpp.pick(config, log=log_fn)
                if http_url:
                    config["_runtime_http_proxy"] = http_url
                    config["proxy"] = http_url
                    out["http_proxy"] = http_url
                    if log_fn:
                        log_fn(f"[*] HTTP代理: {hpp.redact(http_url)}")
        except Exception as exc:
            if log_fn:
                safe = str(exc).encode("ascii", "backslashreplace").decode("ascii")
                log_fn(f"[*] HTTP代理池跳过: {safe}")

    def _try_clash():
        nonlocal clash_node
        if not config.get("clash_rotate_per_account", True):
            return
        # Frequency throttle: rotate only every N accounts to reduce host impact.
        every_n = int(config.get("clash_rotate_every_n", 1) or 1)
        if every_n > 1:
            cnt = int(config.get("_clash_rotate_counter", 0)) + 1
            config["_clash_rotate_counter"] = cnt
            if cnt % every_n != 0:
                # Keep current exit; signal "handled" so we don't fall to HTTP pool.
                clash_throttled["v"] = True
                if log_fn:
                    log_fn(f"[*] 出口沿用当前节点 (每{every_n}号换一次, {cnt%every_n}/{every_n})")
                return
        try:
            from clash_proxy import rotate_node, is_available

            if is_available():
                clash_node = rotate_node(
                    log=log_fn,
                    verify_ip=bool(config.get("clash_verify_ip", False)),
                    # Isolation: dedicated group + no global-mode / no conn flush.
                    selector=(config.get("clash_selector") or None),
                    force_global=bool(config.get("clash_force_global", False)),
                    close_conns=bool(config.get("clash_close_conns", False)),
                )
                if clash_node:
                    out["clash_node"] = clash_node
                    safe = str(clash_node).encode("ascii", "backslashreplace").decode("ascii")
                    if log_fn:
                        log_fn(f"[*] 出口节点: {safe}")
        except Exception as exc:
            if log_fn:
                safe = str(exc).encode("ascii", "backslashreplace").decode("ascii")
                log_fn(f"[*] Clash 轮换跳过: {safe}")

    if prefer_http:
        _try_http()
        if not http_url:
            _try_clash()
    else:
        _try_clash()
        # If clash missing/failed and HTTP pool configured, fall back — but NOT
        # when throttle intentionally skipped this round (keep current exit).
        if not clash_node and not clash_throttled["v"]:
            _try_http()
        elif config.get("http_proxy_also_with_clash"):
            _try_http()
    return out


def report_egress_result(ok: bool, egress: dict | None = None) -> None:
    egress = egress or {}
    try:
        if egress.get("clash_node"):
            from clash_proxy import report_success, report_fail

            (report_success if ok else report_fail)(egress.get("clash_node"))
    except Exception:
        pass
    try:
        if egress.get("http_proxy"):
            import http_proxy_pool as hpp

            (hpp.report_success if ok else hpp.report_fail)(egress.get("http_proxy"))
    except Exception:
        pass


def _reg_metrics_path() -> Path:
    raw = str(config.get("reg_metrics_path") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent / "logs" / "reg_metrics.jsonl"


def record_reg_metric(event: str, reason: str = "", **extra) -> None:
    """Append one JSONL metric for long-term success/fail analysis (community ops)."""
    if not bool(config.get("reg_metrics_enabled", True)):
        return
    try:
        path = _reg_metrics_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "reason": reason or "",
        }
        if extra:
            row.update(extra)
        with _io_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def classify_register_fail(exc: BaseException | None) -> str:
    msg = str(exc or "")
    low = msg.lower()
    if "sso cookie" in low or "未获取到 sso" in msg:
        return "sso_timeout"
    if "验证码" in msg or "otp" in low:
        return "otp"
    if "turnstile" in low or "cloudflare" in low:
        return "cloudflare"
    if "超时" in msg or "timeout" in low:
        return "timeout"
    if "region" in low or "403" in msg:
        return "blocked"
    return "other"


def force_rotate_egress_for_sso(log_fn=None) -> None:
    """Force Clash node rotate after consecutive SSO timeouts (community: bad IP → switch)."""
    if not bool(config.get("sso_timeout_rotate_enabled", True)):
        return
    if not bool(config.get("clash_rotate_per_account", True)):
        return
    try:
        from clash_proxy import rotate_node, is_available

        if not is_available():
            if log_fn:
                log_fn("[*] SSO超时换节点: Clash API 不可用")
            return
        node = rotate_node(
            log=log_fn,
            verify_ip=bool(config.get("clash_verify_ip", False)),
            selector=(config.get("clash_selector") or None),
            force_global=bool(config.get("clash_force_global", False)),
            close_conns=bool(config.get("clash_close_conns", False)),
        )
        config["_clash_rotate_counter"] = 0
        if log_fn:
            if node:
                safe = str(node).encode("ascii", "backslashreplace").decode("ascii")
                log_fn(f"[*] SSO连续超时，已强制换节点: {safe}")
            else:
                log_fn("[*] SSO连续超时，换节点未返回名称（可能已切换）")
        record_reg_metric("egress_rotate", reason="sso_timeout_streak")
    except Exception as exc:
        if log_fn:
            safe = str(exc).encode("ascii", "backslashreplace").decode("ascii")
            log_fn(f"[*] SSO超时换节点失败: {safe}")


def note_register_outcome(
    ok: bool, exc: BaseException | None = None, log_fn=None
) -> None:
    """Metrics + SSO-timeout streak → optional forced egress rotate."""
    reason = "ok" if ok else classify_register_fail(exc)
    record_reg_metric("success" if ok else "fail", reason=reason)
    if ok:
        config["_sso_timeout_streak"] = 0
        return
    if reason != "sso_timeout":
        return
    streak = int(config.get("_sso_timeout_streak", 0) or 0) + 1
    config["_sso_timeout_streak"] = streak
    threshold = max(1, int(config.get("sso_timeout_rotate_after", 2) or 2))
    if log_fn:
        log_fn(f"[*] SSO超时 streak={streak}/{threshold}")
    if streak >= threshold:
        force_rotate_egress_for_sso(log_fn)
        config["_sso_timeout_streak"] = 0


def daily_success_count_from_metrics() -> int:
    """Count today's success events from reg_metrics.jsonl (best-effort)."""
    try:
        path = _reg_metrics_path()
        if not path.is_file():
            return 0
        today = datetime.date.today().isoformat()
        n = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or '"success"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("event") == "success" and str(row.get("ts", "")).startswith(
                    today
                ):
                    n += 1
        return n
    except Exception:
        return 0


def check_daily_success_cap(log_fn=None) -> bool:
    """Return False when daily success cap reached (0 = disabled)."""
    cap = int(config.get("register_daily_success_cap", 0) or 0)
    if cap <= 0:
        return True
    n = daily_success_count_from_metrics()
    if n >= cap:
        if log_fn:
            log_fn(f"[!] 已达日成功上限 {n}/{cap}，本轮停止（长期稳跑）")
        record_reg_metric("cap_hit", reason="daily_success", count=n, cap=cap)
        return False
    return True


def get_duckmail_api_key():
    return config.get("duckmail_api_key", "")

def get_cloudflare_api_base():
    return str(config.get("cloudflare_api_base", "") or "").rstrip("/")


def get_cloudflare_api_key():
    return config.get("cloudflare_api_key", "")


def get_cloudflare_auth_mode():
    return str(config.get("cloudflare_auth_mode", "none") or "none").lower()


def get_cloudflare_path(key, default_path):
    raw = str(config.get(key, default_path) or default_path).strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def cloudflare_build_headers(content_type=False):
    headers = {"Content-Type": "application/json"} if content_type else {}
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key:
        if mode == "x-api-key":
            headers["X-API-Key"] = key
        elif mode == "x-admin-auth":
            headers["x-admin-auth"] = key
        elif mode != "none":
            headers["Authorization"] = f"Bearer {key}"
    return headers


def cloudflare_apply_auth_params(params=None):
    merged = dict(params or {})
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key and mode == "query-key":
        merged["key"] = key
    return merged


def _parse_domain_list(raw):
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        items = [str(x).strip() for x in raw]
    else:
        text = str(raw or "").replace(";", ",").replace("\n", ",")
        items = [x.strip() for x in text.replace(" ", ",").split(",")]
    out, seen = [], set()
    for item in items:
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def get_mail_backends():
    """多邮件后端（四域名负载均衡）。未配置 mail_backends 时回退单后端。"""
    raw = config.get("mail_backends")
    backends = []
    if isinstance(raw, list) and raw:
        for item in raw:
            if not isinstance(item, dict):
                continue
            base = str(item.get("api_base") or "").strip().rstrip("/")
            if not base:
                continue
            backends.append(
                {
                    "api_base": base,
                    "domains": _parse_domain_list(item.get("domains") or []),
                    "path_accounts": str(
                        item.get("path_accounts")
                        or item.get("cloudflare_path_accounts")
                        or "/api/new_address"
                    ).strip(),
                    "path_messages": str(
                        item.get("path_messages")
                        or item.get("cloudflare_path_messages")
                        or "/api/mails"
                    ).strip(),
                    "auth_mode": str(
                        item.get("auth_mode") or item.get("cloudflare_auth_mode") or "none"
                    ),
                    "api_key": str(
                        item.get("api_key") or item.get("cloudflare_api_key") or ""
                    ),
                }
            )
    if not backends:
        base = get_cloudflare_api_base()
        if base:
            backends.append(
                {
                    "api_base": base,
                    "domains": _parse_domain_list(config.get("defaultDomains", "")),
                    "path_accounts": get_cloudflare_path(
                        "cloudflare_path_accounts", "/api/new_address"
                    ),
                    "path_messages": get_cloudflare_path(
                        "cloudflare_path_messages", "/api/mails"
                    ),
                    "auth_mode": get_cloudflare_auth_mode(),
                    "api_key": get_cloudflare_api_key(),
                }
            )
    return backends


def _rebuild_domain_backend_map():
    m = {}
    ordered = []
    seen = set()
    for be in get_mail_backends():
        for d in be.get("domains") or []:
            key = d.lower()
            if key not in m:
                m[key] = be
            if key not in seen:
                seen.add(key)
                ordered.append(d)
    for d in _parse_domain_list(config.get("defaultDomains", "")):
        key = d.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(d)
            if key not in m and get_mail_backends():
                m[key] = get_mail_backends()[0]
    config["_cf_domain_backend_map"] = m
    config["_cf_domains_cache"] = ordered
    return ordered, m


def resolve_backend_for_domain(domain: str):
    domain = (domain or "").strip().lower()
    m = config.get("_cf_domain_backend_map") or {}
    if not m:
        _, m = _rebuild_domain_backend_map()
    if domain in m:
        return m[domain]
    _, m = _rebuild_domain_backend_map()
    if domain in m:
        return m[domain]
    backends = get_mail_backends()
    return backends[0] if backends else None


def resolve_api_base_for_email(email: str) -> str:
    email = (email or "").strip().lower()
    if email in _cf_email_api_base:
        return _cf_email_api_base[email]
    if "@" in email:
        be = resolve_backend_for_domain(email.split("@", 1)[1])
        if be:
            return be["api_base"]
    return get_cloudflare_api_base()


def cloudflare_next_default_domain():
    """选择 Cloudflare 临时邮箱域名。

    优先按池中各域名已有账号数做负载均衡（选最少的），避免单域名堆积触发风控。
    池数据不可用时回退到轮询。支持 mail_backends 多后端域名列表。
    会跳过 domain_health 临时降权的域名（全降权时回退全部）。
    """
    global _cf_domain_index
    domains, _ = _rebuild_domain_backend_map()
    if not domains:
        domains = _parse_domain_list(config.get("defaultDomains", ""))
    if not domains:
        return ""
    try:
        import domain_health as _dh

        domains = _dh.filter_active_domains(list(domains), cfg=config)
    except Exception:
        pass
    if not domains:
        return ""
    if len(domains) == 1:
        return domains[0]

    # Count existing CPA files per domain (anti-accumulation)
    try:
        from pathlib import Path
        counts: dict[str, int] = {d: 0 for d in domains}
        cpa_dir = Path(str(config.get("cpa_auth_dir") or "cpa_auths"))
        if not cpa_dir.is_absolute():
            cpa_dir = Path(os.path.dirname(os.path.abspath(__file__))) / cpa_dir
        if cpa_dir.is_dir():
            for p in cpa_dir.glob("xai-*.json"):
                name = p.stem.replace("xai-", "")
                if "@" in name:
                    dom = name.split("@", 1)[1]
                    if dom in counts:
                        counts[dom] += 1
        min_count = min(counts.values())
        candidates = [d for d, c in counts.items() if c == min_count]
        import random as _rnd
        return _rnd.choice(candidates)
    except Exception:
        with _cf_domain_lock:
            domain = domains[_cf_domain_index % len(domains)]
            _cf_domain_index += 1
            _save_domain_rr_index()
            return domain


def cloudflare_is_admin_create_path(path):
    """判断当前创建邮箱路径是否为 cloudflare_temp_email 管理员创建接口。"""
    p = str(path or "").rstrip("/").lower()
    return p.endswith("/admin/new_address") or p == "/admin/new_address"


def _pick_list_payload(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return data.get("results")
        if isinstance(data.get("hydra:member"), list):
            return data.get("hydra:member")
        if isinstance(data.get("data"), list):
            return data.get("data")
        if isinstance(data.get("messages"), list):
            return data.get("messages")
        if isinstance(data.get("data"), dict):
            nested = data.get("data")
            if isinstance(nested.get("messages"), list):
                return nested.get("messages")
    return []


def _backend_headers(backend: dict, content_type: bool = True) -> dict:
    headers = {"Content-Type": "application/json"} if content_type else {}
    key = str(backend.get("api_key") or "").strip()
    mode = str(backend.get("auth_mode") or "none").lower()
    if key:
        if mode == "x-api-key":
            headers["X-API-Key"] = key
        elif mode == "x-admin-auth":
            headers["x-admin-auth"] = key
        elif mode != "none":
            headers["Authorization"] = f"Bearer {key}"
    return headers


def _create_address_on_backend(backend: dict, domain: str):
    path = str(backend.get("path_accounts") or "/api/new_address").strip()
    if not path.startswith("/"):
        path = "/" + path
    url = f"{backend['api_base']}{path}"
    is_admin = cloudflare_is_admin_create_path(path)
    headers = _backend_headers(backend, content_type=True)
    if str(backend.get("auth_mode") or "none").lower() == "none":
        headers = {"Content-Type": "application/json"}
    if is_admin:
        payload = {"name": generate_username(10), "enablePrefix": True}
        if domain:
            payload["domain"] = domain
    else:
        payload = {"name": generate_username(10), "enablePrefix": True}
        if domain:
            payload["domain"] = domain
    resp = http_post(url, json=payload, headers=headers)
    status = int(getattr(resp, "status_code", 0) or 0)
    body = ""
    try:
        body = resp.text[:300]
    except Exception:
        body = ""
    if status >= 400:
        if "Invalid domain" in body or "invalid domain" in body.lower():
            raise ValueError(f"Invalid domain: {domain}")
        resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare {path} 返回非JSON: {body}")
    address = data.get("address")
    jwt = data.get("jwt")
    if not address or not jwt:
        raise Exception(f"Cloudflare {path} 缺少 address/jwt: {data}")
    return address, jwt


def cloudflare_create_temp_address(api_base=None):
    """多后端 + 多域名负载均衡创建临时邮箱。

    1) 按池内最少域名选 domain（防堆积）
    2) 路由到该域名所属 mail_backend 创建
    3) 记录 email->api_base，收信走同一后端
    失败时轮询其它域名/后端。
    """
    global _cf_domain_index
    domains, _ = _rebuild_domain_backend_map()
    if not domains:
        # 兼容旧调用：单 api_base
        base = (api_base or get_cloudflare_api_base() or "").rstrip("/")
        if not base:
            raise Exception("无可用邮箱后端/域名")
        domains = _parse_domain_list(config.get("defaultDomains", "")) or [""]

    try:
        import domain_health as _dh

        domains = _dh.filter_active_domains(list(domains), cfg=config) or list(domains)
    except Exception:
        pass

    # 首选最少域名，再准备 fallback 顺序
    preferred = cloudflare_next_default_domain() or domains[0]
    ordered = [preferred] + [d for d in domains if d != preferred]

    last_err = None
    for domain in ordered:
        backend = resolve_backend_for_domain(domain)
        if not backend:
            if api_base:
                backend = {
                    "api_base": str(api_base).rstrip("/"),
                    "path_accounts": get_cloudflare_path(
                        "cloudflare_path_accounts", "/api/new_address"
                    ),
                    "auth_mode": get_cloudflare_auth_mode(),
                    "api_key": get_cloudflare_api_key(),
                }
            else:
                last_err = f"{domain}: no backend"
                continue
        try:
            address, jwt = _create_address_on_backend(backend, domain)
            with _cf_domain_lock:
                _cf_domain_index += 1
                _save_domain_rr_index()
            _cf_email_api_base[str(address).lower()] = backend["api_base"]
            try:
                import domain_health as _dh

                _dh.record_event(domain, kind="mail_ok", cfg=config)
            except Exception:
                pass
            return address, jwt
        except ValueError as exc:
            last_err = str(exc)
            try:
                import domain_health as _dh

                _dh.record_event(domain, kind="mail_fail", reason=str(exc), cfg=config)
            except Exception:
                pass
            continue
        except Exception as exc:
            last_err = f"{domain}@{backend.get('api_base')}: {exc}"
            try:
                import domain_health as _dh

                _dh.record_event(domain, kind="mail_fail", reason=str(exc), cfg=config)
            except Exception:
                pass
            continue
    raise Exception(f"创建邮箱失败（多域名负载均衡）: {last_err}")


def get_user_agent():
    return config.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    )


def resolve_grok2api_local_token_file():
    configured = str(config.get("grok2api_local_token_file", "") or "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(__file__), "token.json")


def _normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def add_token_to_grok2api_local_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    token_file = resolve_grok2api_local_token_file()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip()
    if not pool_name:
        pool_name = "ssoBasic"
    parent_dir = os.path.dirname(token_file)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with _io_lock:
        data = {}
        if os.path.exists(token_file):
            try:
                with open(token_file, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        pool = data.get(pool_name)
        if not isinstance(pool, list):
            pool = []
        existing = set()
        for item in pool:
            if isinstance(item, str):
                existing.add(_normalize_sso_token(item))
            elif isinstance(item, dict):
                existing.add(_normalize_sso_token(item.get("token", "")))
        if token in existing:
            if log_callback:
                log_callback(f"[*] grok2api 本地池已存在 token: {pool_name}")
            return True
        entry = {"token": token, "tags": ["auto-register"], "note": email}
        pool.append(entry)
        data[pool_name] = pool
        with open(token_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    if log_callback:
        log_callback(f"[+] 已写入 grok2api 本地池: {pool_name} ({token_file})")
    return True


def get_grok2api_remote_api_bases(base):
    """生成 grok2api 管理 API 候选根路径。

    参数:
      - base str: 用户配置的 grok2api 远端地址

    返回:
      - list[str]: 依次尝试的管理 API 根路径
    """
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return []
    lower = normalized.lower()
    candidates = [normalized]
    if lower.endswith("/admin/api"):
        return candidates
    if lower.endswith("/admin"):
        candidates.append(f"{normalized}/api")
    else:
        candidates.append(f"{normalized}/admin/api")
    seen = set()
    unique = []
    for item in candidates:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def add_token_to_grok2api_remote_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    base = str(config.get("grok2api_remote_base", "") or "").strip().rstrip("/")
    app_key = str(config.get("grok2api_remote_app_key", "") or "").strip()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip() or "ssoBasic"
    if not base or not app_key:
        if log_callback:
            log_callback("[Debug] grok2api 远端未配置 base/app_key，跳过")
        return False
    headers = {"Content-Type": "application/json"}
    query = {"app_key": app_key}
    pool_map = {"ssoBasic": "basic", "ssoSuper": "super"}
    remote_pool = pool_map.get(pool_name, "basic")
    api_bases = get_grok2api_remote_api_bases(base)
    add_errors = []
    # 优先使用 add 接口，避免全量覆盖远端池
    add_payload = {"tokens": [token], "pool": remote_pool, "tags": ["auto-register"]}
    for api_base in api_bases:
        endpoint = f"{api_base}/tokens/add"
        try:
            resp_add = http_post(
                endpoint,
                headers=headers,
                params=query,
                json=add_payload,
                timeout=30,
                proxies={},
            )
            resp_add.raise_for_status()
            if log_callback:
                log_callback(f"[+] 已写入 grok2api 远端池: {pool_name} ({endpoint})")
            return True
        except Exception as add_exc:
            add_errors.append(f"{endpoint}: {add_exc}")
    if log_callback:
        log_callback(f"[Debug] /tokens/add 写入失败，尝试 /tokens 全量模式: {'; '.join(add_errors)}")

    # 兜底：旧版全量保存接口
    current = {}
    fallback_base = api_bases[0] if api_bases else base
    for api_base in api_bases or [base]:
        try:
            resp = http_get(f"{api_base}/tokens", headers=headers, params=query, timeout=20, proxies={})
            if resp.status_code == 200:
                payload = resp.json()
                current = payload.get("tokens", {}) if isinstance(payload, dict) else {}
                fallback_base = api_base
                break
        except Exception:
            continue
    if not isinstance(current, dict):
        current = {}
    pool = current.get(pool_name)
    if not isinstance(pool, list):
        pool = []
    existing = set()
    for item in pool:
        if isinstance(item, str):
            existing.add(_normalize_sso_token(item))
        elif isinstance(item, dict):
            existing.add(_normalize_sso_token(item.get("token", "")))
    if token not in existing:
        pool.append({"token": token, "tags": ["auto-register"], "note": email})
    current[pool_name] = pool
    save_errors = []
    save_bases = []
    for item in [fallback_base, *(api_bases or [base])]:
        if item and item not in save_bases:
            save_bases.append(item)
    for api_base in save_bases:
        try:
            resp2 = http_post(f"{api_base}/tokens", headers=headers, params=query, json=current, timeout=30, proxies={})
            resp2.raise_for_status()
            if log_callback:
                log_callback(f"[+] 已写入 grok2api 远端池: {pool_name} ({api_base}/tokens)")
            return True
        except Exception as save_exc:
            save_errors.append(f"{api_base}/tokens: {save_exc}")
    raise RuntimeError(f"grok2api 远端 /tokens 全量模式写入失败: {'; '.join(save_errors)}")


def add_token_to_grok2api_pools(raw_token, email="", log_callback=None):
    if config.get("grok2api_auto_add_local", True):
        try:
            add_token_to_grok2api_local_pool(raw_token, email=email, log_callback=log_callback)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 写入 grok2api 本地池失败: {exc}")
    if config.get("grok2api_auto_add_remote", False):
        try:
            add_token_to_grok2api_remote_pool(raw_token, email=email, log_callback=log_callback)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 写入 grok2api 远端池失败: {exc}")


def add_token_to_token_only_file(raw_token, log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    token_only_file = str(config.get("token_only_file", "") or "").strip()
    if not token_only_file:
        token_only_file = os.path.join(os.path.dirname(__file__), "tokens.txt")
    try:
        with _io_lock:
            with open(token_only_file, "a", encoding="utf-8") as f:
                f.write(f"{token}\n")
        if log_callback:
            log_callback(f"[+] 已写入 token 文件: {token_only_file}")
        return True
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 写入 token 文件失败: {exc}")
        return False


def upload_to_cpa_server(local_path, log_callback=None):
    host = str(config.get("cpa_server_host", "") or "").strip()
    user = str(config.get("cpa_server_user", "root") or "root").strip()
    password = str(config.get("cpa_server_password", "") or "").strip()
    remote_dir = str(config.get("cpa_server_auth_dir", "") or "").strip()
    if not host or not remote_dir:
        return False
    try:
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username=user, password=password, timeout=15)
        sftp = ssh.open_sftp()
        filename = os.path.basename(local_path)
        remote_path = remote_dir.rstrip("/") + "/" + filename
        sftp.put(local_path, remote_path)
        try:
            sftp.chmod(remote_path, 0o600)
        except Exception:
            pass
        sftp.close()
        ssh.close()
        if log_callback:
            log_callback(f"[cpa] 已上传到服务器: {host}:{remote_path}")
        return True
    except Exception as exc:
        if log_callback:
            log_callback(f"[cpa] 上传到服务器失败: {exc}")
        return False


def export_cpa_xai_for_account(email, password, sso=None, log_callback=None, page=None):
    if not config.get("cpa_export_enabled", True):
        if log_callback:
            log_callback("[cpa] CPA 导出已禁用，跳过")
        return {"ok": False, "skipped": True, "reason": "disabled"}
    try:
        from cpa_export import export_cpa_xai_for_account as _export
        return _export(
            email, password,
            sso=sso,
            page=page,
            config=config,
            log_callback=log_callback,
        )
    except Exception as exc:
        if log_callback:
            log_callback(f"[cpa] CPA xAI 导出失败: {exc}")
        return {"ok": False, "error": str(exc)}


def write_local_grok_from_cpa(cpa_result, log_callback=None):
    """Write CPA OIDC tokens into ~/.grok/auth.json for local grok CLI.

    When the current auth.json already has a non-expired OIDC token, skip
    overwrite so bulk registration does not thrash the live Grok CLI session.
    Force with GROK_FORCE_LOCAL_AUTH=1 (quota_watch rotate still writes via
    local_grok_auth directly and is unaffected).
    """
    if str(os.environ.get("GROK_SKIP_LOCAL_AUTH", "")).strip() in ("1", "true", "yes", "on"):
        return {"ok": False, "skipped": True, "reason": "GROK_SKIP_LOCAL_AUTH"}
    if not config.get("local_grok_auth_auto", False):
        return {"ok": False, "skipped": True, "reason": "disabled"}
    force = str(os.environ.get("GROK_FORCE_LOCAL_AUTH", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not force:
        try:
            from local_grok_auth import default_auth_path, load_auth_file
            from datetime import datetime, timezone

            auth_path = default_auth_path()
            if auth_path.is_file():
                data = load_auth_file(auth_path)
                entry = None
                if isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, dict) and (
                            v.get("access_token") or v.get("key") or v.get("refresh_token")
                        ):
                            entry = v
                            break
                if entry:
                    exp_raw = str(entry.get("expires") or entry.get("expired") or "").strip()
                    still_valid = False
                    if exp_raw:
                        try:
                            exp_s = exp_raw.replace("Z", "+00:00")
                            exp_dt = datetime.fromisoformat(exp_s)
                            still_valid = exp_dt.timestamp() > (
                                datetime.now(tz=timezone.utc).timestamp() + 120
                            )
                        except Exception:
                            still_valid = bool(
                                entry.get("access_token") or entry.get("key")
                            )
                    else:
                        still_valid = bool(entry.get("access_token") or entry.get("key"))
                    if still_valid:
                        email = entry.get("email") or "current"
                        if log_callback:
                            log_callback(
                                f"[*] 本机 Grok auth 仍有效，跳过覆盖 ({email})"
                            )
                        return {
                            "ok": True,
                            "skipped": True,
                            "reason": "preserve_healthy_auth",
                            "email": email,
                        }
        except Exception:
            pass
    try:
        from local_grok_auth import write_from_config_and_cpa_result
        return write_from_config_and_cpa_result(
            config,
            cpa_result if cpa_result else {},
            log=log_callback,
        )
    except Exception as exc:
        if log_callback:
            log_callback(f"[!] 本机 Grok auth 写入失败: {exc}")
        return {"ok": False, "error": str(exc)}


def run_post_register_pipeline(sso, email, log_callback=None, cpa_result=None):
    """Full auto path after one successful registration.

    1) tokens.txt
    2) grok2api local/remote pools
    3) local grok auth.json (when CPA result available)
    """
    results = {}
    results["token_file"] = add_token_to_token_only_file(sso, log_callback=log_callback)
    add_token_to_grok2api_pools(sso, email=email, log_callback=log_callback)
    if cpa_result is not None:
        results["local_grok"] = write_local_grok_from_cpa(
            cpa_result, log_callback=log_callback
        )
    return results


def create_browser_options():
    """创建尽量贴近真实浏览器的启动参数。

    TUN 系统代理时请保持 config.proxy 为空，让 Chromium 走系统网络栈。
    不要默认 new_env / 强制 UA / 过多 flag，容易触发 Cloudflare「故障排除」。
    """
    options = ChromiumOptions()
    options.set_timeouts(base=1)
    # 并发时为每个 worker 分配独立资料目录，避免 cookie/会话互相污染
    profile_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser_profiles")
    try:
        os.makedirs(profile_root, exist_ok=True)
        wid = _get_worker_id()
        profile_dir = os.path.join(
            profile_root,
            f"w{wid}_{os.getpid()}_{threading.get_ident()}_{int(time.time() * 1000) % 1000000}",
        )
        options.set_user_data_path(profile_dir)
    except Exception:
        pass
    # set_user_data_path 可能清掉 auto_port，必须放在后面重新启用
    options.auto_port()
    for flag in (
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
    ):
        options.set_argument(flag)
    # Optional slim flags (community register_cli / TabPool path). Default off —
    # aggressive flags like --disable-images can hurt signup UX; enable via config.
    if config.get("chromium_slim", False):
        for flag in (
            "--mute-audio",
            "--disable-background-networking",
            "--disable-dev-shm-usage",
            "--disable-software-rasterizer",
        ):
            try:
                options.set_argument(flag)
            except Exception:
                pass
    # Mild always-on resource trims (safe with current CF success rate)
    if config.get("chromium_mute_audio", True):
        try:
            options.set_argument("--mute-audio")
        except Exception:
            pass
    # 藏窗：仅影响本进程启动的隔离 profile Chromium，不动日常 Chrome
    if config.get("hide_window", True):
        try:
            options.set_argument("--window-position=-32000,-32000")
        except Exception:
            pass
    proxy = str(config.get("proxy", "") or "").strip()
    if proxy:
        try:
            options.set_proxy(proxy)
        except Exception:
            options.set_argument(f"--proxy-server={proxy}")
    # 默认使用浏览器真实 UA；仅当用户显式打开时才覆盖
    if config.get("browser_use_custom_ua", False):
        ua = get_user_agent()
        if ua:
            try:
                options.set_user_agent(ua)
            except Exception:
                options.set_argument(f"--user-agent={ua}")
    # 反检测：viewport / tz 可独立开；UA 池默认关（真浏览器 UA 更稳）。
    want_ua = bool(config.get("anti_detect_ua_pool", False))
    want_vp = bool(config.get("anti_detect_viewport", True))
    want_tz = bool(config.get("anti_detect_tz_locale", True))
    if want_ua or want_vp or want_tz:
        try:
            from anti_detect import pick_fingerprint

            fp = pick_fingerprint()
            if want_ua:
                try:
                    options.set_user_agent(fp.user_agent)
                except Exception:
                    options.set_argument(f"--user-agent={fp.user_agent}")
                options.set_argument(f"--user-agent={fp.user_agent}")
            # 视口（Canvas/WebGL 指纹输入）— 可在无 UA 池时单独启用
            if want_vp:
                options.set_argument(f"--window-size={fp.window_size}")
            # 时区 + 语言
            if want_tz:
                options.set_argument(f"--lang={fp.lang_code}")
                tz_env[0] = fp.timezone
            _thread_fp[0] = fp
        except Exception:
            pass
    if os.path.exists(EXTENSION_PATH):
        options.add_extension(EXTENSION_PATH)
    return options


def _is_daily_chrome_profile(ud: str) -> bool:
    """True if path looks like the user's normal Chrome/Edge profile (do not hide)."""
    udn = os.path.normcase(os.path.abspath(str(ud)))
    # Explicit allow: our project profiles + DrissionPage auto temp ports
    if ".browser_profiles" in udn:
        return False
    if "drissionpage" in udn and "autoportdata" in udn:
        return False
    if "drissionpage" in udn and "userdata" in udn:
        return False
    # Deny real daily browsers
    markers = (
        r"\google\chrome\user data",
        r"\microsoft\edge\user data",
        r"\chrome\user data",
    )
    return any(m in udn for m in markers)


def apply_register_window_hide(browser=None, page=None, log_callback=None) -> bool:
    """Hide ONLY this DrissionPage browser window (off-screen / CDP hide).

    Instance-scoped: only the browser/page we just launched. Refuses known
    daily Chrome/Edge User Data paths. Allows .browser_profiles and
    Temp\\DrissionPage\\autoPortData (isolated automation profiles).
    """
    if not config.get("hide_window", True):
        return False
    br = browser if browser is not None else _get_browser()
    pg = page if page is not None else _get_page()
    if br is None and pg is None:
        return False

    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    try:
        ud = getattr(br, "user_data_path", None) if br is not None else None
        if ud and _is_daily_chrome_profile(ud):
            _log(f"[!] hide_window skipped: refuses daily browser profile ({ud})")
            return False
        if ud:
            _log(f"[Debug] hide_window profile={ud}")
    except Exception:
        pass

    # Prefer page.set.window (CDP) — instance scoped only
    targets = []
    if pg is not None:
        targets.append(("page", pg))
    if br is not None:
        targets.append(("browser", br))
    last_err = None
    for label, obj in targets:
        try:
            win = getattr(getattr(obj, "set", None), "window", None)
            if win is None:
                continue
            # location first: more reliable than hide() on some Windows builds
            if hasattr(win, "location"):
                try:
                    win.normal()
                except Exception:
                    pass
                try:
                    win.location(-32000, -32000)
                    _log(f"[*] 注册浏览器已移出屏幕(set.window.location via {label})")
                    return True
                except Exception as e:
                    last_err = e
            if hasattr(win, "hide"):
                try:
                    win.hide()
                    _log(f"[*] 注册浏览器已隐藏(set.window.hide via {label})")
                    return True
                except Exception as e:
                    last_err = e
            if hasattr(win, "mini"):
                try:
                    win.mini()
                    _log(f"[*] 注册浏览器已最小化(set.window.mini via {label})")
                    return True
                except Exception as e:
                    last_err = e
        except Exception as e:
            last_err = e
            continue
    if last_err and log_callback:
        _log(f"[!] hide_window failed: {last_err}")
    return False


def _build_request_kwargs(**kwargs):
    request_kwargs = dict(kwargs)
    proxies = request_kwargs.pop("proxies", None)
    if proxies is None:
        proxies = get_proxies()
    if proxies:
        request_kwargs["proxies"] = proxies
    request_kwargs.setdefault("timeout", 15)
    return request_kwargs


def http_get(url, **kwargs):
    try:
        return requests.get(url, **_build_request_kwargs(**kwargs))
    except Exception as exc:
        err = str(exc)
        # 代理不可用时自动回退为直连，避免整个流程直接失败
        if "127.0.0.1 port 7890" in err or "Could not connect to server" in err:
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.get(url, **_build_request_kwargs(**retry_kwargs))
        raise


def http_post(url, **kwargs):
    try:
        return requests.post(url, **_build_request_kwargs(**kwargs))
    except Exception as exc:
        err = str(exc)
        if "127.0.0.1 port 7890" in err or "Could not connect to server" in err:
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.post(url, **_build_request_kwargs(**retry_kwargs))
        raise


def raise_if_cancelled(cancel_callback=None):
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled("用户停止注册")


def sleep_with_cancel(seconds, cancel_callback=None):
    deadline = time.time() + max(seconds, 0)
    while True:
        raise_if_cancelled(cancel_callback)
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def get_domains(api_key=None):
    headers = {}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = http_get(f"{DUCKMAIL_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def create_account(address, password, api_key=None, expires_in=0):
    headers = {"Content-Type": "application/json"}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = {"address": address, "password": password, "expiresIn": expires_in}
    resp = http_post(f"{DUCKMAIL_API_BASE}/accounts", json=data, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_token(address, password):
    data = {"address": address, "password": password}
    resp = http_post(f"{DUCKMAIL_API_BASE}/token", json=data)
    resp.raise_for_status()
    return resp.json().get("token")


def get_messages(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def get_message_detail(token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_domains(api_base, api_key=None):
    headers = cloudflare_build_headers(content_type=False)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_domains", "/domains")
    params = cloudflare_apply_auth_params()
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    return _pick_list_payload(resp.json())


def cloudflare_create_account(api_base, address, password, api_key=None, expires_in=0):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    payload = {"address": address, "password": password, "expiresIn": expires_in}
    path = get_cloudflare_path("cloudflare_path_accounts", "/accounts")
    params = cloudflare_apply_auth_params()
    resp = http_post(f"{api_base}{path}", json=payload, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_token(api_base, address, password, api_key=None):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_token", "/token")
    resp = http_post(
        f"{api_base}{path}",
        json={"address": address, "password": password},
        headers=headers,
        params=cloudflare_apply_auth_params(),
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        if data.get("token"):
            return data.get("token")
        if isinstance(data.get("data"), dict) and data["data"].get("token"):
            return data["data"].get("token")
    return None


def cloudflare_get_messages(api_base, token):
    headers = {"Authorization": f"Bearer {token}"}
    path = get_cloudflare_path("cloudflare_path_messages", "/messages")
    params = {"limit": 20, "offset": 0}
    params = cloudflare_apply_auth_params(params)
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare messages 返回非JSON: {resp.text[:300]}")
    return _pick_list_payload(data)


def cloudflare_get_message_detail(api_base, token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    candidates = [
        f"{api_base}/api/mail/{message_id}",
        f"{api_base}{get_cloudflare_path('cloudflare_path_messages', '/messages')}/{message_id}",
    ]
    last_err = None
    for url in candidates:
        try:
            resp = http_get(
                url,
                headers=headers,
                params=cloudflare_apply_auth_params(),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                return data["data"]
            return data
        except Exception as exc:
            last_err = exc
            continue
    raise Exception(f"Cloudflare 获取邮件详情失败: {last_err}")


YYDS_API_BASE = "https://maliapi.215.im/v1"


def get_yyds_api_key():
    return config.get("yyds_api_key", "")


def get_yyds_jwt():
    return config.get("yyds_jwt", "")


def yyds_get_domains(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if data.get("success") else []


def yyds_create_account(address=None, domain=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    payload = {}
    if address:
        payload["address"] = address
    if domain:
        payload["domain"] = domain
    elif key or token:
        payload["autoDomainStrategy"] = "prefer_owned"
    resp = http_post(f"{YYDS_API_BASE}/accounts", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鍒涘缓閭澶辫触: {data}")


def yyds_get_token(address, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_post(
        f"{YYDS_API_BASE}/token", json={"address": address}, headers=headers
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("token")
    raise Exception(f"YYDS 鑾峰彇token澶辫触: {data}")


def yyds_get_messages(address, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(
        f"{YYDS_API_BASE}/messages",
        params={"address": address},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("messages", [])
    return []


def yyds_get_message_detail(message_id, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鑾峰彇閭欢璇︽儏澶辫触: {data}")


def yyds_generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def yyds_pick_domain(api_key=None, jwt=None):
    domains = yyds_get_domains(api_key=api_key, jwt=jwt)
    if not domains:
        raise Exception("YYDS 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("isVerified") and not d.get("isPublic")]
    if private:
        return private[0]["domain"]
    public = [d for d in domains if d.get("isVerified") and d.get("isPublic")]
    if public:
        return public[0]["domain"]
    verified = [d for d in domains if d.get("isVerified")]
    if verified:
        return verified[0]["domain"]
    raise Exception("YYDS 鏃犲凡楠岃瘉鍩熷悕鍙敤")


def yyds_get_email_and_token(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    if not token and not key:
        raise Exception("YYDS API Key 或 JWT 未配置")
    domain = yyds_pick_domain(api_key=key, jwt=token)
    username = yyds_generate_username(10)
    result = yyds_create_account(
        address=username, domain=domain, api_key=key, jwt=token
    )
    address = result.get("address") or f"{username}@{domain}"
    temp_token = result.get("token")
    if not temp_token:
        temp_token = yyds_get_token(address, api_key=key, jwt=token)
    if not temp_token:
        raise Exception("鑾峰彇 YYDS token 澶辫触")
    print(f"[*] 宸插垱寤?YYDS 閭: {address}")
    return address, temp_token


def yyds_get_oai_code(
    token,
    address,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    jwt=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = yyds_get_messages(address, token=token, jwt=jwt)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] YYDS 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            to_addrs = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if address.lower() not in to_addrs:
                continue
            try:
                detail = yyds_get_message_detail(msg_id, token=token, jwt=jwt)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] YYDS 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] YYDS 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] YYDS 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"YYDS 在 {timeout}s 内未收到验证码邮件")


def generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def pick_domain(api_key=None):
    domains = get_domains(api_key=api_key)
    if not domains:
        raise Exception("DuckMail 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("ownerId")]
    verified_private = [d for d in private if d.get("isVerified")]
    if verified_private:
        return verified_private[0]["domain"]
    public = [d for d in domains if d.get("isVerified")]
    if public:
        return public[0]["domain"]
    raise Exception("DuckMail 鏃犲凡楠岃瘉鍩熷悕鍙敤")


def get_email_provider():
    return str(config.get("email_provider", "duckmail") or "duckmail").strip().lower()


def _hotmail_ms_domains():
    return ("hotmail.com", "outlook.com", "live.com", "msn.com")


def _is_ms_mail_address(email: str) -> bool:
    em = (email or "").strip().lower()
    if "@" not in em:
        return False
    dom = em.rsplit("@", 1)[-1]
    return any(dom == d or dom.endswith("." + d) for d in _hotmail_ms_domains())


def _is_hotmail_session(dev_token, email: str = "") -> bool:
    """True when this signup used Hotmail pool (mixed or pure hotmail)."""
    tok = str(dev_token or "").strip()
    if tok.startswith("{") and "refresh_token" in tok:
        return True
    if _is_ms_mail_address(email) and tok.startswith("{"):
        return True
    return False


def _email_mix_hotmail_enabled() -> bool:
    """Whether Cloudflare/mixed registration should sometimes pop Hotmail."""
    v = config.get("email_mix_hotmail", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def _email_mix_hotmail_ratio() -> float:
    try:
        r = float(config.get("email_mix_hotmail_ratio", 0.3) or 0.0)
    except Exception:
        r = 0.3
    return max(0.0, min(1.0, r))


def _email_mix_cloud_mail_enabled() -> bool:
    """Whether Cloudflare/mixed registration should sometimes mint Cloud Mail (vip0)."""
    v = config.get("email_mix_cloud_mail", False)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def _email_mix_cloud_mail_ratio() -> float:
    try:
        r = float(config.get("email_mix_cloud_mail_ratio", 0.0) or 0.0)
    except Exception:
        r = 0.0
    return max(0.0, min(1.0, r))


def _email_mix_yunmeng_enabled() -> bool:
    """Whether Cloudflare/mixed registration should sometimes mint Yunmeng buffer mail."""
    v = config.get("email_mix_yunmeng", False)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def _email_mix_yunmeng_ratio() -> float:
    try:
        r = float(config.get("email_mix_yunmeng_ratio", 0.0) or 0.0)
    except Exception:
        r = 0.0
    return max(0.0, min(1.0, r))


def _email_mix_flag(key: str, default: bool = False) -> bool:
    v = config.get(key, default)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def _email_mix_ratio(key: str, default: float = 0.0) -> float:
    try:
        r = float(config.get(key, default) or 0.0)
    except Exception:
        r = float(default or 0.0)
    return max(0.0, min(1.0, r))


def _try_hotmail_inbox():
    import hotmail_pool as _hp

    return _hp.pick_inbox(config)


def _try_cloud_mail_inbox():
    import cloud_mail_otp as _cm

    return _cm.create_inbox(config, root=Path(__file__).resolve().parent)


def _try_yunmeng_inbox():
    import yunmeng_mail_otp as _ym

    return _ym.create_inbox(config)


def _try_tempmail_lol_inbox():
    import tempmail_lol as _tml

    return _tml.create_inbox(config)


def _try_mailtm_inbox():
    import mailtm_otp as _mt

    return _mt.create_inbox(config)


def _try_gptmail_inbox():
    import gptmail_otp as _gm

    return _gm.create_inbox(config)


def _cf_mix_buckets():
    """Ordered exclusive mix buckets for cloudflare/mixed (name, ratio, factory)."""
    buckets = []
    if _email_mix_hotmail_enabled():
        buckets.append(("hotmail", _email_mix_hotmail_ratio(), _try_hotmail_inbox))
    if _email_mix_cloud_mail_enabled():
        buckets.append(("cloud_mail", _email_mix_cloud_mail_ratio(), _try_cloud_mail_inbox))
    if _email_mix_yunmeng_enabled():
        buckets.append(("yunmeng", _email_mix_yunmeng_ratio(), _try_yunmeng_inbox))
    if _email_mix_flag("email_mix_tempmail_lol", False):
        buckets.append(
            ("tempmail_lol", _email_mix_ratio("email_mix_tempmail_lol_ratio", 0.05), _try_tempmail_lol_inbox)
        )
    if _email_mix_flag("email_mix_mailtm", False):
        buckets.append(
            ("mailtm", _email_mix_ratio("email_mix_mailtm_ratio", 0.05), _try_mailtm_inbox)
        )
    if _email_mix_flag("email_mix_gptmail", False):
        buckets.append(
            ("gptmail", _email_mix_ratio("email_mix_gptmail_ratio", 0.03), _try_gptmail_inbox)
        )
    # scale down if sum>1, keep order (hotmail first)
    total = sum(r for _, r, _ in buckets)
    if total > 1.0 and total > 0:
        scale = 1.0 / total
        buckets = [(n, r * scale, f) for n, r, f in buckets]
    return buckets


def get_email_and_token(api_key=None):
    provider = get_email_provider()
    # Diversify under cloudflare/mixed: exclusive buckets then CF residual.
    if provider in ("cloudflare", "mixed", "mix", "cf+hotmail", "cf+mix"):
        buckets = _cf_mix_buckets()
        roll = random.random()
        acc = 0.0
        chosen = None
        for i, (name, ratio, factory) in enumerate(buckets):
            if ratio <= 0:
                continue
            if roll < acc + ratio:
                chosen = i
                break
            acc += ratio
        if chosen is not None:
            # try chosen + later buckets on failure, then residual CF
            for name, ratio, factory in buckets[chosen:]:
                if ratio <= 0:
                    continue
                try:
                    email, tok = factory()
                    try:
                        print(f"[*] email_mix: {name} {email}", flush=True)
                    except Exception:
                        pass
                    return email, tok
                except Exception as exc:
                    try:
                        print(f"[!] email_mix {name} failed, fallback next: {exc}", flush=True)
                    except Exception:
                        pass
        # residual → CF below
    if provider == "yyds":
        return yyds_get_email_and_token(api_key=api_key, jwt=get_yyds_jwt())
    if provider in ("cloudflare", "mixed", "mix", "cf+hotmail", "cf+mix"):
        if not get_mail_backends() and not get_cloudflare_api_base():
            raise Exception("Cloudflare API Base / mail_backends 未配置")
        # 多后端四域名负载均衡（mail_backends + 池内最少域名优先）
        return cloudflare_create_temp_address()
    if provider in ("tempmail_lol", "tempmail.lol", "tempmail"):
        try:
            import tempmail_lol as _tml
        except ImportError as e:
            raise Exception(f"tempmail_lol module missing: {e}") from e
        return _tml.create_inbox(config)
    if provider in ("mailtm", "mail.tm", "mail_tm", "mailgw", "mail.gw"):
        try:
            import mailtm_otp as _mt
        except ImportError as e:
            raise Exception(f"mailtm_otp module missing: {e}") from e
        return _mt.create_inbox(config)
    if provider in ("gptmail", "gpt_mail", "gpt-mail", "chatgpt_org_uk"):
        try:
            import gptmail_otp as _gm
        except ImportError as e:
            raise Exception(f"gptmail_otp module missing: {e}") from e
        return _gm.create_inbox(config)
    if provider in ("mailsapi", "mailsapi_otp", "fixed_otp"):
        try:
            import mailsapi_otp as _mo
        except ImportError as e:
            raise Exception(f"mailsapi_otp module missing: {e}") from e
        # (fixed_email, get_code_url) — not a random mailbox
        return _mo.pick_inbox(config, root=Path(__file__).resolve().parent)
    if provider in ("hotmail", "outlook", "hotmail_pool", "ms_mail"):
        try:
            import hotmail_pool as _hp
        except ImportError as e:
            raise Exception(f"hotmail_pool module missing: {e}") from e
        # Pop one Hotmail line: (email, json account blob for IMAP OTP)
        return _hp.pick_inbox(config)
    if provider in ("cloud_mail", "cloudmail", "vip0", "vip0_xyz", "skymail"):
        try:
            import cloud_mail_otp as _cm
        except ImportError as e:
            raise Exception(f"cloud_mail_otp module missing: {e}") from e
        # Community Cloud Mail (vip0.xyz etc.) — buffer only, not own domains
        return _cm.create_inbox(config, root=Path(__file__).resolve().parent)
    if provider in ("yunmeng", "ym", "yunmeng_mail", "ymmail", "ymmynb"):
        try:
            import yunmeng_mail_otp as _ym
        except ImportError as e:
            raise Exception(f"yunmeng_mail_otp module missing: {e}") from e
        # 云梦公开临时域 — buffer only
        return _ym.create_inbox(config)
    key = api_key or get_duckmail_api_key()
    domain = pick_domain(api_key=key)
    username = generate_username(10)
    address = f"{username}@{domain}"
    password = secrets.token_urlsafe(12)
    create_account(address, password, api_key=key, expires_in=0)
    token = get_token(address, password)
    if not token:
        raise Exception("鑾峰彇 DuckMail token 澶辫触")
    return address, token


def get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    provider = get_email_provider()
    # Route by actual mailbox used (mixed CF+Hotmail/CloudMail must not use wrong poller)
    if _is_hotmail_session(dev_token, email) or (
        provider in ("hotmail", "outlook", "hotmail_pool", "ms_mail")
    ):
        try:
            import hotmail_pool as _hp
        except ImportError as e:
            raise Exception(f"hotmail_pool module missing: {e}") from e
        return _hp.wait_code(
            dev_token,
            email,
            cfg=config,
            timeout=timeout,
            poll_interval=max(3.0, float(poll_interval or 5)),
            log=log_callback,
            cancel=cancel_callback,
            resend=resend_callback,
        )
    # Cloud Mail mix under cloudflare: session blob must win before CF poll
    try:
        import cloud_mail_otp as _cm_early

        if _cm_early.is_cloud_mail_token(dev_token) or provider in (
            "cloud_mail",
            "cloudmail",
            "vip0",
            "vip0_xyz",
            "skymail",
        ):
            return _cm_early.wait_code(
                dev_token,
                email,
                cfg=config,
                timeout=timeout,
                poll_interval=max(2.0, float(poll_interval or 3)),
                log=log_callback,
                cancel=cancel_callback,
                resend=resend_callback,
            )
    except ImportError:
        pass
    # Yunmeng mix under cloudflare / pure yunmeng
    try:
        import yunmeng_mail_otp as _ym_early

        if _ym_early.is_yunmeng_token(dev_token) or provider in (
            "yunmeng",
            "ym",
            "yunmeng_mail",
            "ymmail",
            "ymmynb",
        ):
            return _ym_early.wait_code(
                dev_token,
                email,
                cfg=config,
                timeout=timeout,
                poll_interval=max(1.0, float(poll_interval or 2)),
                log=log_callback,
                cancel=cancel_callback,
                resend=resend_callback,
            )
    except ImportError:
        pass
    # mail.tm / mail.gw session
    try:
        import mailtm_otp as _mt_early

        if _mt_early.is_mailtm_token(dev_token) or provider in (
            "mailtm",
            "mail.tm",
            "mail_tm",
            "mailgw",
            "mail.gw",
        ):
            return _mt_early.wait_code(
                dev_token,
                email,
                cfg=config,
                timeout=timeout,
                poll_interval=max(1.0, float(poll_interval or 2)),
                log=log_callback,
                cancel=cancel_callback,
                resend=resend_callback,
            )
    except ImportError:
        pass
    # GPTMail session
    try:
        import gptmail_otp as _gm_early

        if _gm_early.is_gptmail_token(dev_token) or provider in (
            "gptmail",
            "gpt_mail",
            "gpt-mail",
            "chatgpt_org_uk",
        ):
            return _gm_early.wait_code(
                dev_token,
                email,
                cfg=config,
                timeout=timeout,
                poll_interval=max(1.0, float(poll_interval or 2)),
                log=log_callback,
                cancel=cancel_callback,
                resend=resend_callback,
            )
    except ImportError:
        pass
    if provider == "yyds":
        return yyds_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            jwt=get_yyds_jwt(),
            cancel_callback=cancel_callback,
        )
    if provider in ("cloudflare", "mixed", "mix", "cf+hotmail", "cf+mix"):
        # Optional: fixed Gmail/OTP map for a known email without switching provider
        try:
            import mailsapi_otp as _mo

            _otp_root = Path(__file__).resolve().parent
            if _mo.resolve_url(email, config, root=_otp_root):
                return _mo.wait_code(
                    dev_token if str(dev_token or "").startswith("http") else "",
                    email,
                    cfg=config,
                    timeout=timeout,
                    poll_interval=poll_interval,
                    log=log_callback,
                    cancel=cancel_callback,
                    resend=resend_callback,
                    root=_otp_root,
                )
        except ImportError:
            pass
        # Mapped mailsapi emails must not fall through to CF inbox poll
        return cloudflare_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    if provider in ("tempmail_lol", "tempmail.lol", "tempmail"):
        try:
            import tempmail_lol as _tml
        except ImportError as e:
            raise Exception(f"tempmail_lol module missing: {e}") from e
        return _tml.wait_code(
            dev_token,
            cfg=config,
            timeout=timeout,
            poll_interval=max(0.3, float(poll_interval or 0.5)),
            log=log_callback,
            cancel=cancel_callback,
            resend=resend_callback,
        )
    if provider in ("mailsapi", "mailsapi_otp", "fixed_otp"):
        try:
            import mailsapi_otp as _mo
        except ImportError as e:
            raise Exception(f"mailsapi_otp module missing: {e}") from e
        return _mo.wait_code(
            dev_token,
            email,
            cfg=config,
            timeout=timeout,
            poll_interval=poll_interval,
            log=log_callback,
            cancel=cancel_callback,
            resend=resend_callback,
            root=Path(__file__).resolve().parent,
        )
    # Cloud Mail session blob (also when mixed provider left a cloud_mail token)
    try:
        import cloud_mail_otp as _cm

        if _cm.is_cloud_mail_token(dev_token) or provider in (
            "cloud_mail",
            "cloudmail",
            "vip0",
            "vip0_xyz",
            "skymail",
        ):
            return _cm.wait_code(
                dev_token,
                email,
                cfg=config,
                timeout=timeout,
                poll_interval=max(2.0, float(poll_interval or 3)),
                log=log_callback,
                cancel=cancel_callback,
                resend=resend_callback,
            )
    except ImportError:
        pass
    # pure hotmail already handled at top via _is_hotmail_session
    return duckmail_get_oai_code(
        dev_token,
        email,
        timeout=timeout,
        poll_interval=poll_interval,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
    )


def extract_verification_code(text, subject=""):
    if subject:
        match = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.IGNORECASE)
        if match:
            return match.group(1)
    match = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    patterns = [
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def duckmail_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = get_messages(dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if email.lower() not in recipients:
                continue
            try:
                detail = get_message_detail(dev_token, msg_id)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"在 {timeout}s 内未收到验证码邮件")


def cloudflare_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    # 多后端：收信必须走创建该邮箱时的 api_base
    api_base = resolve_api_base_for_email(email) or get_cloudflare_api_base()
    if not api_base:
        raise Exception("Cloudflare API Base 未配置")
    if log_callback:
        log_callback(f"[Debug] 收信后端: {api_base} email={email}")
    deadline = time.time() + timeout
    # 同一封邮件正文可能延迟可读，允许多次重试解析，避免偶发漏码
    seen_attempts = {}
    next_resend_at = time.time() + 35
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                if log_callback:
                    log_callback("[*] 已触发重新发送验证码")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + 35
        try:
            messages = cloudflare_get_messages(api_base, dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Cloudflare 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        if log_callback:
            log_callback(f"[Debug] Cloudflare 本轮邮件数量: {len(messages)}")

        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id:
                continue
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 5:
                continue
            seen_attempts[msg_id] = attempt + 1
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            msg_addr = str(msg.get("address", "")).lower()
            # 优先匹配目标邮箱；若结构不一致也允许继续解析，避免接口字段漂移导致漏码
            address_matched = True
            if recipients:
                address_matched = email.lower() in recipients
            elif msg_addr:
                address_matched = msg_addr == email.lower()
            if not address_matched and log_callback:
                log_callback(f"[Debug] 跳过疑似非目标邮件 id={msg_id} address={msg_addr} to={recipients}")
                continue
            parts = []
            # 先直接从列表项取内容，避免 detail 接口差异导致漏码
            for field in ("text", "raw", "content", "intro", "body", "snippet"):
                value = msg.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
            html_list = msg.get("html") or []
            if isinstance(html_list, str):
                html_list = [html_list]
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            subject = str(msg.get("subject", "") or "")
            combined = "\n".join(parts)
            # 再尝试 detail 接口补全内容
            try:
                detail = cloudflare_get_message_detail(api_base, dev_token, msg_id)
                for field in ("text", "raw", "content", "intro", "body", "snippet"):
                    value = detail.get(field)
                    if isinstance(value, str) and value.strip():
                        combined += "\n" + value
                html_list2 = detail.get("html") or []
                if isinstance(html_list2, str):
                    html_list2 = [html_list2]
                for h in html_list2:
                    combined += "\n" + re.sub(r"<[^>]+>", " ", h)
                if not subject:
                    subject = str(detail.get("subject", "") or "")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Cloudflare detail接口失败，改用列表内容解析: {exc}")
            if log_callback:
                log_callback(f"[Debug] Cloudflare 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] Cloudflare 从邮件中提取到验证码: {code}")
                return code
            elif log_callback:
                log_callback(f"[Debug] 邮件已解析但未提取到验证码 id={msg_id} attempt={seen_attempts[msg_id]}")
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"Cloudflare 在 {timeout}s 内未收到验证码邮件")


def generate_random_birthdate():
    import datetime as dt

    today = dt.date.today()
    age = random.randint(20, 40)
    birth_year = today.year - age
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year}-{birth_month:02d}-{birth_day:02d}T16:00:00.000Z"


def response_preview(res, limit=200):
    try:
        text = str(res.text or "")
    except Exception:
        text = ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def is_cloudflare_block_response(res):
    try:
        headers = {str(k).lower(): str(v).lower() for k, v in dict(res.headers).items()}
        text = str(res.text or "").lower()
        server = headers.get("server", "")
        content_type = headers.get("content-type", "")
        return (
            res.status_code in (403, 429, 503)
            and (
                "cloudflare" in server
                or "cloudflare" in text
                or "cf-error" in text
                or "__cf_chl" in text
                or "text/html" in content_type
            )
        )
    except Exception:
        return False


def set_birth_date(session, log_callback=None):
    url = "https://grok.com/rest/auth/set-birth-date"
    new_headers = {
        "content-type": "application/json",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    payload = {"birthDate": generate_random_birthdate()}
    try:
        res = session.post(url, json=payload, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] set_birth_date status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_birth_date 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_birth_date HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_birth_date] 异常: {e}")
        return False, f"set_birth_date 异常: {e}"


def set_tos_accepted(session, log_callback=None):
    url = "https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion"
    payload = struct.pack("B", (2 << 3) | 0) + struct.pack("B", 1)
    data = b"\x00" + struct.pack(">I", len(payload)) + payload
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "origin": "https://accounts.x.ai",
        "referer": "https://accounts.x.ai/accept-tos",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(f"[Debug] set_tos_accepted status: {res.status_code}")
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_tos_accepted 被 accounts.x.ai 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_tos_accepted HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_tos_accepted] 异常: {e}")
        return False, f"set_tos_accepted 异常: {e}"


def encode_grpc_nsfw_settings():
    field1_content = bytes([0x10, 0x01])
    field1 = bytes([0x0A, len(field1_content)]) + field1_content
    nsfw_string = b"always_show_nsfw_content"
    field2_inner = bytes([0x0A, len(nsfw_string)]) + nsfw_string
    field2 = bytes([0x12, len(field2_inner)]) + field2_inner
    payload = field1 + field2
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def update_nsfw_settings(session, log_callback=None):
    url = "https://grok.com/auth_mgmt.AuthManagement/UpdateUserFeatureControls"
    data = encode_grpc_nsfw_settings()
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] update_nsfw status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "update_nsfw_settings 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"update_nsfw_settings HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[update_nsfw] 异常: {e}")
        return False, f"update_nsfw_settings 异常: {e}"


def enable_nsfw_for_token(token, cf_clearance="", log_callback=None):
    proxies = get_proxies()
    user_agent = get_user_agent()
    try:
        with requests.Session(impersonate="chrome120", proxies=proxies) as session:
            cookie_parts = [f"sso={token}", f"sso-rw={token}"]
            if cf_clearance:
                cookie_parts.append(f"cf_clearance={cf_clearance}")
            session.headers.update(
                {
                    "user-agent": user_agent,
                    "cookie": "; ".join(cookie_parts),
                }
            )
            ok, message = set_tos_accepted(session, log_callback)
            if not ok:
                return False, message
            ok, message = set_birth_date(session, log_callback)
            if not ok:
                return False, message
            ok, message = update_nsfw_settings(session, log_callback)
            if not ok:
                return False, message
            return True, "成功开启 NSFW"
    except Exception as e:
        return False, f"异常: {str(e)}"


SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

_tls = threading.local()
_cpa_async_threads: list = []


def _wait_cpa_async_threads(timeout=300, log_callback=None, skip_if_stopping=None):
    global _cpa_async_threads
    # Prefer bounded mint pool if used
    try:
        from cpa_mint_pool import get_mint_pool

        pool = get_mint_pool()
        if pool.started:
            pool.wait_done(
                timeout=float(timeout or 0),
                log=log_callback,
                skip_if=skip_if_stopping,
            )
            if log_callback:
                log_callback(pool.summary_line())
            # still join any legacy threads
    except Exception:
        pass
    if skip_if_stopping and skip_if_stopping():
        timeout = min(float(timeout or 0), 5.0)
        if log_callback and _cpa_async_threads:
            log_callback(f"[*] 停止中，仅短暂等待 CPA mint 线程（{timeout:.0f}s）...")
    with _cpa_threads_lock:
        threads = [t for t in _cpa_async_threads if t.is_alive()]
        _cpa_async_threads = [t for t in _cpa_async_threads if t.is_alive()]
    if not threads:
        return
    if log_callback and not (skip_if_stopping and skip_if_stopping()):
        log_callback(f"[*] 等待 {len(threads)} 个异步 CPA mint 线程完成...")
    deadline = time.time() + max(float(timeout or 0), 0)
    for t in threads:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        t.join(timeout=remaining)
    alive = [t for t in threads if t.is_alive()]
    if log_callback:
        if alive:
            log_callback(f"[!] {len(alive)} 个 CPA mint 线程超时未完成")
        else:
            log_callback("[+] 所有 CPA mint 线程已完成")


def _track_cpa_async_thread(thread):
    with _cpa_threads_lock:
        _cpa_async_threads.append(thread)


def _enqueue_cpa_mint(email, password, sso, log_fn, page=None):
    """Async CPA mint via bounded worker pool when enabled; else legacy thread.

    Returns cpa_result dict when sync; None when async queued.
    """
    if not config.get("cpa_export_enabled", True):
        return None
    cpa_async = bool(config.get("cpa_mint_async", True))
    if not cpa_async:
        log_fn("[*] 6. CPA xAI 导出 (同步)")
        cpa_result = export_cpa_xai_for_account(
            email, password, sso=sso, log_callback=log_fn, page=page
        )
        if cpa_result.get("ok"):
            log_fn(f"[+] CPA xAI 导出成功: {cpa_result.get('path', '')}")
        elif not cpa_result.get("skipped"):
            log_fn(f"[!] CPA xAI 导出失败: {cpa_result.get('error', '未知错误')}")
        return cpa_result

    # async path
    try:
        from cpa_mint_pool import (
            MintJob,
            get_mint_pool,
            resolve_queue_max,
            resolve_worker_count,
        )

        workers = resolve_worker_count(config)
    except Exception:
        workers = 0

    if workers > 0:
        try:
            from cpa_mint_pool import MintJob, get_mint_pool, resolve_queue_max

            pool = get_mint_pool()
            qmax = resolve_queue_max(config, workers)
            pool.ensure_started(
                workers=workers,
                queue_max=qmax,
                export_fn=export_cpa_xai_for_account,
                write_local_fn=write_local_grok_from_cpa,
                log=log_fn,
            )
            log_fn(f"[*] 6. CPA xAI 导出 (mint pool w={workers} qmax={qmax})")
            block_sec = float(config.get("cpa_mint_queue_block_sec") or 30)
            ok = pool.submit(
                MintJob(
                    email=email,
                    password=password or "",
                    sso=sso or "",
                    log=log_fn,
                    delay_sec=float(config.get("cpa_mint_delay_sec") or 5),
                ),
                block_sec=block_sec,
                log=log_fn,
            )
            if not ok:
                log_fn("[!] mint 队列满，回退同步导出")
                cpa_result = export_cpa_xai_for_account(
                    email, password, sso=sso, log_callback=log_fn, page=None
                )
                if cpa_result.get("ok"):
                    log_fn(f"[+] CPA xAI 导出成功: {cpa_result.get('path', '')}")
                    write_local_grok_from_cpa(cpa_result, log_callback=log_fn)
                elif not cpa_result.get("skipped"):
                    log_fn(f"[!] CPA xAI 导出失败: {cpa_result.get('error', '未知错误')}")
                return cpa_result
            return None
        except Exception as exc:
            log_fn(f"[!] mint pool 不可用，回退旧异步线程: {exc}")

    # legacy: unbounded daemon thread per account
    log_fn("[*] 6. CPA xAI 导出 (异步线程)")
    def _cpa_mint_bg():
        time.sleep(float(config.get("cpa_mint_delay_sec") or 5))
        try:
            r = export_cpa_xai_for_account(
                email, password, sso=sso, log_callback=log_fn, page=None
            )
            if r.get("ok"):
                log_fn(f"[+] CPA xAI 导出成功: {r.get('path', '')}")
                write_local_grok_from_cpa(r, log_callback=log_fn)
            elif not r.get("skipped"):
                log_fn(f"[!] CPA xAI 导出失败: {r.get('error', '未知错误')}")
        except Exception as e:
            log_fn(f"[!] CPA xAI 导出异常: {e}")

    _t = threading.Thread(target=_cpa_mint_bg, daemon=True)
    _t.start()
    _track_cpa_async_thread(_t)
    return None


def _join_threads_interruptible(threads, should_stop=None, timeout=None, poll=0.5):
    """可被 stop/Ctrl+C 打断的线程等待，避免 join() 永久阻塞。"""
    threads = [t for t in (threads or []) if t is not None]
    if not threads:
        return
    deadline = None if timeout is None else (time.time() + max(float(timeout), 0))
    while any(t.is_alive() for t in threads):
        if should_stop and should_stop():
            # 给 worker 一点时间走 finally/stop_browser，再返回
            grace_deadline = time.time() + 3
            while any(t.is_alive() for t in threads) and time.time() < grace_deadline:
                for t in threads:
                    t.join(timeout=poll)
            return
        if deadline is not None and time.time() >= deadline:
            return
        for t in threads:
            t.join(timeout=poll)


def _get_browser():
    return getattr(_tls, 'browser', None)


def _set_browser(b):
    _tls.browser = b


def _get_page():
    return getattr(_tls, 'page', None)


def _set_page(p):
    _tls.page = p


def _get_worker_id():
    return getattr(_tls, 'worker_id', 0)


def _set_worker_id(wid):
    _tls.worker_id = wid


def setup_light_theme(root):
    try:
        root.option_add("*Background", UI_BG)
        root.option_add("*Foreground", UI_FG)
        root.option_add("*selectBackground", UI_ACTIVE_BG)
        root.option_add("*selectForeground", UI_FG)
        root.option_add("*insertBackground", UI_FG)
        root.option_add("*Entry.Background", UI_ENTRY_BG)
        root.option_add("*Text.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Foreground", UI_FG)
        style = ttk.Style(root)
        available = set(style.theme_names())
        if "clam" in available:
            style.theme_use("clam")
        elif "default" in available:
            style.theme_use("default")
        root.configure(bg=UI_BG)
        style.configure(".", background=UI_BG, foreground=UI_FG, fieldbackground=UI_ENTRY_BG)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabelframe", background=UI_BG, foreground=UI_FG)
        style.configure("TLabelframe.Label", background=UI_BG, foreground=UI_FG)
        style.configure("TLabel", background=UI_BG, foreground=UI_FG)
        style.configure("TCheckbutton", background=UI_BG, foreground=UI_FG)
        style.configure("TButton", background=UI_BUTTON_BG, foreground=UI_FG)
        style.configure("TEntry", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TCombobox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TSpinbox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
    except Exception:
        pass


def tk_label(parent, text="", **kwargs):
    return tk.Label(parent, text=text, bg=kwargs.pop("bg", UI_BG), fg=kwargs.pop("fg", UI_FG), **kwargs)


def tk_entry(parent, textvariable=None, width=30, **kwargs):
    return tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        disabledbackground="#2f2f2f",
        disabledforeground=UI_MUTED_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
        **kwargs,
    )


def tk_button(parent, text="", command=None, state=tk.NORMAL, **kwargs):
    return tk.Button(
        parent,
        text=text,
        command=command,
        state=state,
        bg=UI_BUTTON_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        disabledforeground="#777777",
        relief=tk.RAISED,
        padx=10,
        pady=3,
        **kwargs,
    )


def tk_checkbutton(parent, text="", variable=None, **kwargs):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        activeforeground=UI_FG,
        selectcolor="#3d7be0",
        **kwargs,
    )


def tk_option_menu(parent, variable, values, width=12):
    menu = tk.OptionMenu(parent, variable, *values)
    menu.configure(
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
    )
    menu["menu"].configure(bg=UI_ENTRY_BG, fg=UI_FG, activebackground=UI_ACTIVE_BG, activeforeground=UI_FG)
    return menu


# CDN / static patterns safe to block on signup (CF challenge + x.ai HTML still load).
# Do NOT block cloudflare/challenge scripts or accounts.x.ai document itself.
_BANDWIDTH_BLOCK_URLS = (
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.svg",
    "*.ico",
    "*.bmp",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.otf",
    "*.eot",
    "*.mp4",
    "*.webm",
    "*.mp3",
    "*.wav",
    "*.ogg",
    "*.m4a",
    "*googlevideo.com*",
    "*doubleclick.net*",
    "*google-analytics.com*",
    "*googletagmanager.com*",
    "*facebook.net*",
    "*hotjar.com*",
    "*segment.io*",
    "*sentry.io*",
    "*clarity.ms*",
)


def apply_bandwidth_saver(page=None, log_callback=None) -> bool:
    """Block heavy static/media URLs via CDP Network.setBlockedURLs (DrissionPage).

    Controlled by config ``block_media_fonts`` (default False historically; ops may
    enable when pool is full and traffic is the bottleneck). Safe patterns only —
    never blocks Cloudflare/x.ai challenge documents.
    """
    if not config.get("block_media_fonts", False):
        return False
    pg = page if page is not None else _get_page()
    if pg is None:
        return False

    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    try:
        setter = getattr(pg, "set", None)
        if setter is None or not hasattr(setter, "blocked_urls"):
            _log("[!] bandwidth saver: page.set.blocked_urls unavailable")
            return False
        setter.blocked_urls(list(_BANDWIDTH_BLOCK_URLS))
        _log(
            f"[*] bandwidth saver on: blocked {len(_BANDWIDTH_BLOCK_URLS)} url patterns "
            "(images/fonts/media/analytics)"
        )
        return True
    except Exception as exc:
        _log(f"[!] bandwidth saver failed: {exc}")
        return False


def start_browser(log_callback=None):
    last_exc = None
    for attempt in range(1, 5):
        try:
            _set_browser(Chromium(create_browser_options()))
            tabs = _get_browser().get_tabs()
            _set_page(tabs[-1] if tabs else _get_browser().new_tab())
            apply_bandwidth_saver(_get_page(), log_callback=log_callback)
            if log_callback and getattr(_get_browser(), "user_data_path", None):
                log_callback(f"[Debug] 当前浏览器资料目录: {_get_browser().user_data_path}")
            # start_browser 仍返回 (browser, page)；不再调用 apply_register_window_hide
            if log_callback and attempt > 1:
                log_callback(f"[*] 浏览器第 {attempt} 次启动成功")
            return _get_browser(), _get_page()
        except Exception as exc:
            last_exc = exc
            if log_callback:
                log_callback(f"[Debug] 浏览器启动失败(第{attempt}/4次): {exc}")
            try:
                if _get_browser() is not None:
                    _get_browser().quit(del_data=True)
            except Exception:
                pass
            _set_browser(None)
            _set_page(None)
            time.sleep(min(1.5 * attempt, 4))
    raise Exception(f"浏览器启动失败，已重试4次: {last_exc}")


def stop_browser():
    profile_path = None
    browser = _get_browser()
    if browser is not None:
        try:
            profile_path = getattr(browser, "user_data_path", None)
        except Exception:
            profile_path = None
        try:
            browser.quit(del_data=True)
        except Exception:
            pass
    _set_browser(None)
    _set_page(None)
    if profile_path:
        try:
            import shutil

            root = os.path.abspath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser_profiles")
            )
            abs_profile = os.path.abspath(str(profile_path))
            if abs_profile.startswith(root) and os.path.isdir(abs_profile):
                shutil.rmtree(abs_profile, ignore_errors=True)
        except Exception:
            pass


def restart_browser(log_callback=None):
    stop_browser()
    return start_browser(log_callback=log_callback)


def prepare_clean_browser_session(log_callback=None, cancel_callback=None):
    """轻量清理：避免预访问 xAI/grok 触发 Cloudflare，同时尽量清掉残留登录态。"""
    raise_if_cancelled(cancel_callback)
    page = _get_page()
    browser = _get_browser()
    if page is None or browser is None:
        start_browser(log_callback=log_callback)
        page = _get_page()
        browser = _get_browser()
    try:
        if page is not None:
            try:
                page.get("about:blank")
            except Exception:
                pass
            try:
                page.run_js(
                    """
try { localStorage.clear(); } catch (e) {}
try { sessionStorage.clear(); } catch (e) {}
"""
                )
            except Exception:
                pass
        # 尽量清 cookie，但不主动打开 accounts.x.ai / grok.com（容易先撞 CF）
        if browser is not None and hasattr(browser, "set_cookies"):
            try:
                browser.set_cookies(False)
            except Exception:
                pass
        if page is not None and hasattr(page, "set_cookies"):
            try:
                page.set_cookies(False)
            except Exception:
                pass
        if log_callback:
            log_callback("[Debug] 已做轻量会话清理，准备打开注册页")
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 清理浏览器会话失败，将重启浏览器: {exc}")
        restart_browser(log_callback=log_callback)


def detect_cloudflare_block_page(log_callback=None):
    """检测当前页是否为 Cloudflare 拦截/故障排除页。"""
    page = _get_page()
    if page is None:
        return False, ""
    try:
        info = page.run_js(
            r"""
const body = ((document.body && (document.body.innerText || document.body.textContent)) || '')
  .replace(/\s+/g, ' ').trim().slice(0, 500);
const title = document.title || '';
const html = (document.documentElement && document.documentElement.innerHTML || '').slice(0, 2000);
return { url: location.href || '', title, body, html };
"""
        )
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 读取页面检测 CF 失败: {exc}")
        return False, ""
    if not isinstance(info, dict):
        return False, ""
    blob = " ".join(
        [
            str(info.get("url") or ""),
            str(info.get("title") or ""),
            str(info.get("body") or ""),
            str(info.get("html") or ""),
        ]
    ).lower()
    markers = (
        "故障排除",
        "attention required",
        "cf-error",
        "cf-error-details",
        "sorry, you have been blocked",
        "you have been blocked",
        "checking your browser before accessing",
        "enable javascript and cookies",
        "cloudflare ray id",
        "error code 1020",
        "error code 1005",
        "access denied",
    )
    hit = next((m for m in markers if m in blob), "")
    if not hit:
        return False, ""
    detail = f"url={info.get('url') or ''}; marker={hit}; title={info.get('title') or ''}"
    return True, detail


def cleanup_runtime_memory(log_callback=None, reason="定期清理"):
    if log_callback:
        log_callback(f"[*] {reason}: 关闭浏览器并清理内存")
    stop_browser()
    collected = gc.collect()
    if log_callback:
        log_callback(f"[*] Python GC 已回收对象数: {collected}")


def refresh_active_page():
    if _get_browser() is None:
        restart_browser()
    try:
        tabs = _get_browser().get_tabs()
        if tabs:
            _set_page(tabs[-1])
        else:
            _set_page(_get_browser().new_tab())
        apply_bandwidth_saver(_get_page())
    except Exception:
        restart_browser()
    return _get_page()


_EMAIL_SIGNUP_JS = r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('value'),
        node.getAttribute('href'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const text = nodeText(node);
    const compact = text.replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册') || compact.includes('用邮箱注册') || compact.includes('邮箱注册')) return 100;
    if (lower.includes('signupwithemail') || lower.includes('sign-up-with-email') || lower.includes('sign_up_with_email')) return 95;
    if (lower.includes('continuewithemail') || lower.includes('continue-with-email')) return 90;
    if ((lower.includes('email') || compact.includes('邮箱')) &&
        (lower.includes('sign') || lower.includes('continue') || lower.includes('use') || lower.includes('with') || compact.includes('注册') || compact.includes('继续'))) {
        return 80;
    }
    if (lower === 'email' || lower === '邮箱' || compact.includes('电子邮箱')) return 70;
    return 0;
}
function emailInputReady() {
    const selectors = [
        'input[data-testid="email"]',
        'input[name="email"]',
        'input[type="email"]',
        'input[autocomplete="email"]',
        'input[placeholder*="mail" i]',
        'input[aria-label*="mail" i]',
        'input[aria-label*="邮箱"]',
        'input[placeholder*="邮箱"]',
    ];
    for (const sel of selectors) {
        const node = document.querySelector(sel);
        if (node && isVisible(node) && !node.disabled && !node.readOnly) return true;
    }
    return false;
}
function collectCandidates() {
    const nodes = Array.from(document.querySelectorAll(
        'button, a, [role="button"], input[type="button"], input[type="submit"], div[role="button"], span[role="button"]'
    ));
    return nodes
        .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
        .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
        .filter((item) => item.score > 0)
        .sort((a, b) => b.score - a.score);
}
const url = location.href || '';
const title = document.title || '';
const bodyText = (document.body && (document.body.innerText || document.body.textContent) || '').replace(/\s+/g, ' ').trim().slice(0, 240);
const candidates = collectCandidates();
const buttons = candidates.slice(0, 8).map((item) => item.text || '').filter(Boolean);
if (emailInputReady()) {
    return {
        state: 'email-form-ready',
        url,
        title,
        buttons,
        body: bodyText,
    };
}
const target = candidates[0] || null;
if (!target) {
    return {
        state: 'not-found',
        url,
        title,
        buttons: Array.from(document.querySelectorAll('button, a, [role="button"]'))
            .filter((node) => isVisible(node))
            .map(nodeText)
            .filter(Boolean)
            .slice(0, 10),
        body: bodyText,
    };
}
try { target.node.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
target.node.click();
return {
    state: 'clicked',
    text: target.text || true,
    url,
    title,
    buttons,
    body: bodyText,
};
"""


def _signup_page_snapshot(log_callback=None):
    page = _get_page()
    if page is None:
        return {"url": "none", "title": "", "buttons": [], "body": ""}
    try:
        snap = page.run_js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
  return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('title'), node.getAttribute('href')]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
return {
  url: location.href || '',
  title: document.title || '',
  buttons: Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((n) => isVisible(n))
    .map(nodeText)
    .filter(Boolean)
    .slice(0, 12),
  body: ((document.body && (document.body.innerText || document.body.textContent)) || '').replace(/\s+/g, ' ').trim().slice(0, 300),
  hasEmail: !!document.querySelector('input[type="email"], input[name="email"], input[data-testid="email"]'),
};
"""
        )
        if isinstance(snap, dict):
            return snap
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 读取注册页快照失败: {exc}")
    try:
        return {
            "url": getattr(page, "url", "") or "",
            "title": "",
            "buttons": [],
            "body": (page.html or "")[:300],
            "hasEmail": False,
        }
    except Exception:
        return {"url": "none", "title": "", "buttons": [], "body": "", "hasEmail": False}


def click_email_signup_button(timeout=18, log_callback=None, cancel_callback=None):
    deadline = time.time() + timeout
    last_diag = 0.0
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        blocked, detail = detect_cloudflare_block_page(log_callback=log_callback)
        if blocked:
            raise Exception(f"Cloudflare 拦截页，无法点击邮箱注册: {detail}")
        if log_callback:
            log_callback("[Debug] 尝试查找“使用邮箱注册”按钮...")

        try:
            clicked = _get_page().run_js(_EMAIL_SIGNUP_JS)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 查找邮箱注册按钮异常: {exc}")
            clicked = None

        state = clicked.get("state") if isinstance(clicked, dict) else clicked
        if state in ("clicked", True) or (isinstance(clicked, str) and clicked):
            detail = ""
            if isinstance(clicked, dict):
                detail = f": {clicked.get('text')}" if clicked.get("text") else ""
            elif isinstance(clicked, str):
                detail = f": {clicked}"
            if log_callback:
                log_callback(f"[*] 已点击「使用邮箱注册」按钮{detail}")
            sleep_with_cancel(1.5, cancel_callback)
            return True
        if state == "email-form-ready":
            if log_callback:
                log_callback("[*] 已处于邮箱注册表单，跳过入口按钮点击")
            return True

        now = time.time()
        if log_callback and now - last_diag >= 2:
            last_diag = now
            snap = clicked if isinstance(clicked, dict) else _signup_page_snapshot(log_callback)
            url = (snap or {}).get("url") or (_get_page().url if _get_page() else "none")
            buttons = " | ".join((snap or {}).get("buttons") or []) or "none"
            body = ((snap or {}).get("body") or "")[:160]
            log_callback(f"[Debug] 当前URL: {url}; buttons={buttons}; body={body}")

        # 页面若仍空白/未加载完，主动再刷一次注册页
        try:
            url_now = (_get_page().url if _get_page() else "") or ""
            if "about:blank" in url_now or not url_now:
                _get_page().get(SIGNUP_URL)
                _get_page().wait.doc_loaded()
        except Exception:
            pass
        sleep_with_cancel(0.8, cancel_callback)

    blocked, detail = detect_cloudflare_block_page(log_callback=log_callback)
    if blocked:
        raise Exception(f"Cloudflare 拦截页，无法点击邮箱注册: {detail}")
    snap = _signup_page_snapshot(log_callback)
    if log_callback:
        log_callback(
            f"[Debug] 页面内容片段: url={snap.get('url')}; title={snap.get('title')}; "
            f"buttons={' | '.join(snap.get('buttons') or []) or 'none'}; body={(snap.get('body') or '')[:300]}"
        )
    fail_url = str(snap.get("url") or "unknown")
    fail_buttons = " | ".join(snap.get("buttons") or []) or "none"
    residual_hint = ""
    low = fail_url.lower()
    if any(k in low for k in ("tos-gate", "accept-tos", "/tos", "grok.com")) or any(
        k in fail_buttons for k in ("知道了", "Got it", "I understand")
    ):
        residual_hint = "；疑似上号会话/TOS 残留（非缺点击流程），账号结束后将完整重启浏览器"
    raise Exception(
        "未找到「使用邮箱注册」按钮"
        f"（url={fail_url}; buttons={fail_buttons}{residual_hint}）"
    )


def open_signup_page(log_callback=None, cancel_callback=None):
    raise_if_cancelled(cancel_callback)
    if _get_browser() is None:
        start_browser(log_callback=log_callback)
        if log_callback:
            log_callback("[*] 浏览器已启动")
        if not os.path.exists(EXTENSION_PATH) and log_callback:
            log_callback("[!] 未找到 turnstilePatch 扩展目录，Turnstile 辅助可能不可用")
    prepare_clean_browser_session(log_callback=log_callback, cancel_callback=cancel_callback)
    last_exc = None
    opened = False
    for attempt in range(1, 4):
        raise_if_cancelled(cancel_callback)
        try:
            browser = _get_browser()
            if browser is None:
                start_browser(log_callback=log_callback)
                browser = _get_browser()
            try:
                tabs = browser.get_tabs()
                _set_page(tabs[0] if tabs else browser.new_tab())
            except Exception:
                _set_page(browser.new_tab())
            apply_bandwidth_saver(_get_page(), log_callback=log_callback)
            _get_page().get(SIGNUP_URL)
            _get_page().wait.doc_loaded()
            # 给 CF/前端一点渲染时间
            sleep_with_cancel(1.2, cancel_callback)
            blocked, detail = detect_cloudflare_block_page(log_callback=log_callback)
            if blocked:
                last_exc = Exception(f"Cloudflare 拦截页: {detail}")
                if log_callback:
                    log_callback(f"[!] 检测到 Cloudflare 拦截/故障排除页，重启浏览器重试 ({attempt}/3): {detail}")
                restart_browser(log_callback=log_callback)
                sleep_with_cancel(1.5, cancel_callback)
                continue
            last_exc = None
            opened = True
            break
        except RegistrationCancelled:
            raise
        except Exception as e:
            last_exc = e
            if log_callback:
                log_callback(f"[Debug] 打开注册页失败(第{attempt}/3次): {e}")
            try:
                restart_browser(log_callback=log_callback)
            except Exception as e2:
                if log_callback:
                    log_callback(f"[Debug] 重启浏览器失败: {e2}")
            sleep_with_cancel(1, cancel_callback)
    if not opened:
        raise Exception(f"打开注册页失败: {last_exc}")

    _deadline = time.time() + 10
    while time.time() < _deadline:
        raise_if_cancelled(cancel_callback)
        blocked, detail = detect_cloudflare_block_page(log_callback=log_callback)
        if blocked:
            if log_callback:
                log_callback(f"[!] 注册页加载后仍是 Cloudflare 拦截页: {detail}")
            raise Exception(f"Cloudflare 拦截页: {detail}")
        try:
            _ready = _get_page().run_js(
                "return !!document.querySelector('button, input[type=\"email\"], a[href*=\"sign\"], a[href*=\"email\"], form')"
            )
            if _ready:
                break
        except Exception:
            pass
        time.sleep(0.3)
    if log_callback:
        log_callback(f"[*] 当前URL: {_get_page().url}")
    click_email_signup_button(
        log_callback=log_callback, cancel_callback=cancel_callback
    )


def has_profile_form(log_callback=None):
    refresh_active_page()
    try:
        return bool(
            _get_page().run_js(
                """
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]');
return !!(givenInput && familyInput && passwordInput);
            """
            )
        )
    except Exception:
        return False


def fill_email_and_submit(timeout=45, log_callback=None, cancel_callback=None):
    raise_if_cancelled(cancel_callback)
    email, dev_token = get_email_and_token()
    if not email or not dev_token:
        raise Exception("获取邮箱失败")
    if log_callback:
        log_callback(f"[*] 已创建邮箱: {email}")
    deadline = time.time() + timeout
    last_diag_time = 0
    last_reclick_time = 0
    last_snapshot = None
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = _get_page().run_js(
            r"""
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function describeInput(node) {
    return [
        `type=${node.getAttribute('type') || ''}`,
        `name=${node.getAttribute('name') || ''}`,
        `id=${node.getAttribute('id') || ''}`,
        `placeholder=${node.getAttribute('placeholder') || ''}`,
        `aria=${node.getAttribute('aria-label') || ''}`,
        `testid=${node.getAttribute('data-testid') || ''}`,
    ].join(' ').replace(/\s+/g, ' ').trim().slice(0, 160);
}
function describeAction(node) {
    return textOf(node).slice(0, 120);
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const visibleInputs = Array.from(document.querySelectorAll('input, textarea'))
    .filter((node) => isVisible(node) && !node.disabled && !node.readOnly)
    .map(describeInput)
    .slice(0, 8);
const visibleActions = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map(describeAction)
    .filter(Boolean)
    .slice(0, 10);
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) {
    return {
        state: 'not-ready',
        url: location.href,
        title: document.title,
        inputs: visibleInputs,
        buttons: visibleActions,
    };
}
input.focus(); input.click();
const valueProto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const valueSetter = Object.getOwnPropertyDescriptor(valueProto, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
const inputType = (input.getAttribute('type') || '').toLowerCase();
const isValid = inputType !== 'email' || input.checkValidity();
if ((input.value || '').trim() !== email || !isValid) {
    return {
        state: 'fill-failed',
        value: input.value || '',
        valid: isValid,
        input: describeInput(input),
        url: location.href,
    };
}
input.blur();
return {
    state: 'filled',
    input: describeInput(input),
    url: location.href,
};
            """,
            email,
        )
        state = filled.get("state") if isinstance(filled, dict) else filled
        if isinstance(filled, dict):
            last_snapshot = filled
        if state == "not-ready":
            now = time.time()
            if now - last_reclick_time >= 3:
                try:
                    reclicked = _get_page().run_js(_EMAIL_SIGNUP_JS)
                except Exception:
                    reclicked = None
                last_reclick_time = now
                re_state = reclicked.get("state") if isinstance(reclicked, dict) else reclicked
                if re_state == "email-form-ready":
                    if log_callback:
                        log_callback("[Debug] 邮箱输入框检测中：页面已进入邮箱表单")
                elif re_state in ("clicked", True) or (isinstance(reclicked, str) and reclicked):
                    detail = ""
                    if isinstance(reclicked, dict) and reclicked.get("text"):
                        detail = f": {reclicked.get('text')}"
                    elif isinstance(reclicked, str):
                        detail = f": {reclicked}"
                    if log_callback:
                        log_callback(f"[Debug] 邮箱输入框未出现，已再次触发邮箱注册入口{detail}")
            if log_callback and now - last_diag_time >= 5:
                last_diag_time = now
                inputs = " | ".join((filled or {}).get("inputs", [])[:6]) if isinstance(filled, dict) else ""
                buttons = " | ".join((filled or {}).get("buttons", [])[:8]) if isinstance(filled, dict) else ""
                url = (filled or {}).get("url", _get_page().url if _get_page() else "") if isinstance(filled, dict) else (_get_page().url if _get_page() else "")
                log_callback(f"[Debug] 等待邮箱输入框: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if state != "filled":
            if log_callback:
                log_callback(f"[Debug] 邮箱输入框已出现，但写入失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        sleep_with_cancel(0.8, cancel_callback)
        clicked = _get_page().run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input || !(input.value || '').trim()) return false;
const inputType = (input.getAttribute('type') || '').toLowerCase();
if (inputType === 'email' && !input.checkValidity()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
    const text = textOf(node).replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text === '注册' ||
        text.includes('注册') ||
        text.includes('继续') ||
        text.includes('下一步') ||
        text.includes('确认') ||
        lower.includes('signup') ||
        lower.includes('sign up') ||
        lower.includes('continue') ||
        lower.includes('next') ||
        lower.includes('createaccount') ||
        lower.includes('submit')
    );
});
if (submitButton) {
    submitButton.click();
    return textOf(submitButton) || true;
}
const form = input.closest('form');
if (form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return 'form-submit';
}
input.focus();
input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
return 'enter';
            """
        )
        if clicked:
            if log_callback:
                detail = f" ({clicked})" if isinstance(clicked, str) else ""
                log_callback(f"[*] 已填写邮箱并提交: {email}{detail}")
            return email, dev_token
        sleep_with_cancel(0.5, cancel_callback)
    if last_snapshot:
        inputs = " | ".join(last_snapshot.get("inputs", [])[:6])
        buttons = " | ".join(last_snapshot.get("buttons", [])[:8])
        url = last_snapshot.get("url", _get_page().url if _get_page() else "")
        raise Exception(
            f"未找到邮箱输入框或注册按钮，最后页面: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}"
        )
    raise Exception("未找到邮箱输入框或注册按钮")


def fill_code_and_submit(email, dev_token, timeout=180, log_callback=None, cancel_callback=None):
    def _resend_code():
        _get_page().run_js(
            r"""
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = nodes.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('重新发送') || t.includes('resend') || t.includes('再次发送');
});
if (target && !target.disabled) { target.click(); return true; }
return false;
            """
        )

    code = get_oai_code(
        dev_token,
        email,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
        resend_callback=_resend_code,
    )
    if not code:
        raise Exception("获取验证码失败")
    clean_code = str(code).replace("-", "").strip()
    deadline = time.time() + timeout

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = _get_page().run_js(
            """
const code = String(arguments[0] || '').trim();
if (!code) return 'empty-code';

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function setInputValue(input, value) {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp=\"true\"], input[name=\"code\"], input[autocomplete=\"one-time-code\"], input[inputmode=\"numeric\"], input[inputmode=\"text\"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 6) > 1);

if (aggregate) {
    aggregate.focus();
    aggregate.click();
    setInputValue(aggregate, code);
    return String(aggregate.value || '').replace(/\\s+/g, '') ? 'filled-aggregate' : 'aggregate-failed';
}

const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    const ac = String(node.autocomplete || '').toLowerCase();
    return maxLength === 1 || ac === 'one-time-code';
});

if (otpBoxes.length >= code.length) {
    for (let i = 0; i < code.length; i += 1) {
        const ch = code[i] || '';
        const box = otpBoxes[i];
        box.focus();
        box.click();
        setInputValue(box, ch);
        box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ch }));
        box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ch }));
    }
    const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value || '').trim()).join('');
    return merged.length ? 'filled-boxes' : 'boxes-failed';
}

return 'not-ready';
            """,
            clean_code,
        )

        if filled == "not-ready":
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if "failed" in str(filled):
            if log_callback:
                log_callback(f"[Debug] 验证码填写失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue

        clicked = _get_page().run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const buttons = Array.from(document.querySelectorAll('button[type=\"submit\"], button')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});

const btn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return (
        t.includes('确认邮箱') ||
        t.includes('继续') ||
        t.includes('下一步') ||
        t.includes('confirm') ||
        t.includes('continue') ||
        t.includes('next')
    );
});

if (!btn) return 'no-button';
btn.focus();
btn.click();
return 'clicked';
            """
        )

        if clicked == "clicked" or clicked == "no-button":
            if log_callback:
                log_callback(f"[*] 已填写验证码并提交: {code}")
            sleep_with_cancel(1.5, cancel_callback)
            return code

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("验证码已获取，但自动填写/提交失败")


def solve_turnstile_capsolver(website_url=None, sitekey=None, log_callback=None, cancel_callback=None):
    """通过 CapSolver API 解决 Turnstile 验证码"""
    api_key = config.get("capsolver_api_key", "").strip()
    if not api_key:
        raise Exception("capsolver_api_key 未配置")

    if not website_url:
        website_url = SIGNUP_URL

    if not sitekey and _get_page():
        try:
            sitekey = _get_page().run_js(
                """
try {
  const el = document.querySelector('[data-sitekey]');
  if (el) return el.getAttribute('data-sitekey');
  const iframes = document.querySelectorAll('iframe[src*="turnstile"]');
  for (const f of iframes) {
    const m = f.src.match(/[?&]k=([^&]+)/);
    if (m) return m[1];
  }
  return '';
} catch(e) { return ''; }
                """
            )
        except Exception:
            sitekey = ""

    if not sitekey:
        sitekey = "0x4AAAAAAAhr9JGVDZbrZOo0"

    if log_callback:
        log_callback(f"[*] CapSolver: 创建任务 sitekey={sitekey[:16]}...")

    import urllib.request
    import urllib.error

    proxy = config.get("proxy", "")

    create_payload = json.dumps({
        "clientKey": api_key,
        "task": {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": sitekey,
        }
    }).encode()

    req = urllib.request.Request(
        "https://api.capsolver.com/createTask",
        data=create_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except Exception as e:
        raise Exception(f"CapSolver createTask 失败: {e}")

    if result.get("errorId", 0) != 0:
        raise Exception(f"CapSolver 错误: {result.get('errorDescription', result)}")

    task_id = result.get("taskId")
    if not task_id:
        raise Exception(f"CapSolver 未返回 taskId: {result}")

    if log_callback:
        log_callback(f"[*] CapSolver: 任务已创建 taskId={task_id[:12]}... 等待结果")

    poll_payload = json.dumps({
        "clientKey": api_key,
        "taskId": task_id,
    }).encode()

    for i in range(60):
        raise_if_cancelled(cancel_callback)
        time.sleep(2)

        req2 = urllib.request.Request(
            "https://api.capsolver.com/getTaskResult",
            data=poll_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                status = json.loads(resp2.read())
        except Exception:
            continue

        if status.get("errorId", 0) != 0:
            raise Exception(f"CapSolver 轮询错误: {status.get('errorDescription', status)}")

        if status.get("status") == "ready":
            token = status.get("solution", {}).get("token", "")
            if token and len(token) >= 80:
                if log_callback:
                    log_callback(f"[*] CapSolver: 成功获取 token, 长度={len(token)}")
                return token
            raise Exception(f"CapSolver 返回无效 token: len={len(token)}")

        if status.get("status") == "failed":
            raise Exception(f"CapSolver 任务失败: {status.get('errorDescription', '')}")

    raise Exception("CapSolver 超时(120s)")


def getTurnstileToken(log_callback=None, cancel_callback=None):
    if _get_page() is None:
        raise Exception("页面未就绪，无法执行 Turnstile")

    try:
        _get_page().run_js(
            """
try {
  const inp = document.querySelector('input[name="cf-turnstile-response"]');
  const oldVal = inp ? String(inp.value || '').trim() : '';
  const oldResp = (window.turnstile && typeof turnstile.getResponse === 'function') ? String(turnstile.getResponse() || '').trim() : '';
  window.__stale_turnstile_token = oldVal || oldResp || '';
  if (window.__orig_turnstile_getResponse && window.turnstile) {
    turnstile.getResponse = window.__orig_turnstile_getResponse;
    delete window.__orig_turnstile_getResponse;
  }
  if (window.turnstile && typeof turnstile.reset === 'function') turnstile.reset();
  if (inp) inp.value = '';
  delete window.__capsolver_token;
} catch(e) {}
            """
        )
    except Exception:
        pass

    for _ in range(0, 20):
        raise_if_cancelled(cancel_callback)
        try:
            token = _get_page().run_js(
                """
try {
  const stale = window.__stale_turnstile_token || '';
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput && byInput !== stale) return byInput;
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    const resp = String(turnstile.getResponse() || '').trim();
    if (resp && resp !== stale) return resp;
  }
  return '';
} catch(e) { return ''; }
                """
            )
            token = str(token or "").strip()
            if len(token) >= 80:
                if log_callback:
                    log_callback(f"[*] Turnstile 已通过，token长度={len(token)}")
                return token

            challenge_input = _get_page().ele("@name=cf-turnstile-response")
            if challenge_input:
                wrapper = challenge_input.parent()
                iframe = None
                try:
                    iframe = wrapper.shadow_root.ele("tag:iframe")
                except Exception:
                    iframe = None
                if iframe:
                    try:
                        iframe.run_js(
                            """
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
let sx = getRandomInt(800, 1200);
let sy = getRandomInt(400, 700);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: sx });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: sy });
                            """
                        )
                    except Exception:
                        pass
                    try:
                        body_sr = iframe.ele("tag:body").shadow_root
                        btn = body_sr.ele("tag:input")
                        if btn:
                            btn.click()
                    except Exception:
                        pass
            else:
                # 兜底：尝试触发页面上可见的 Turnstile 容器
                _get_page().run_js(
                    """
const nodes = Array.from(document.querySelectorAll('div,span,iframe')).filter((n) => {
  const txt = (n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute?.('src') || '');
  return String(txt).toLowerCase().includes('turnstile');
});
if (nodes.length && typeof nodes[0].click === 'function') nodes[0].click();
                    """
                )
        except Exception:
            pass
        sleep_with_cancel(1, cancel_callback)

    # 浏览器方式失败，尝试 CapSolver 作为后备
    api_key = config.get("capsolver_api_key", "")
    if api_key:
        if log_callback:
            log_callback("[*] 浏览器解 Turnstile 失败，尝试 CapSolver...")
        try:
            sitekey = _get_page().run_js(
                """
try {
  const el = document.querySelector('[data-sitekey]');
  if (el) return el.getAttribute('data-sitekey');
  const iframes = document.querySelectorAll('iframe[src*="turnstile"]');
  for (const f of iframes) {
    const m = f.src.match(/[?&]k=([^&]+)/);
    if (m) return m[1];
  }
  return '';
} catch(e) { return ''; }
                """
            )
            sitekey = str(sitekey or "").strip()
            if not sitekey:
                sitekey = "0x4AAAAAAAhr9JGVDZbrZOo0"  # x.ai 已知 sitekey

            page_url = _get_page().url or SIGNUP_URL
            token = solve_turnstile_capsolver(website_url=page_url, sitekey=sitekey, log_callback=log_callback, cancel_callback=cancel_callback)
            if token:
                # 注入 token 到页面
                _get_page().run_js(
                    f"""
const inp = document.querySelector('input[name="cf-turnstile-response"]');
if (inp) {{
  inp.value = {json.dumps(token)};
  inp.dispatchEvent(new Event('input', {{bubbles: true}}));
  inp.dispatchEvent(new Event('change', {{bubbles: true}}));
}}
if (window.turnstile && typeof turnstile.getResponse === 'function') {{
  window.__capsolver_token = {json.dumps(token)};
  if (!window.__orig_turnstile_getResponse) {{
    window.__orig_turnstile_getResponse = turnstile.getResponse;
  }}
  const orig = window.__orig_turnstile_getResponse;
  turnstile.getResponse = function() {{ return window.__capsolver_token || orig.call(this); }};
}}
                    """
                )
                if log_callback:
                    log_callback(f"[*] CapSolver Turnstile 成功，token长度={len(token)}")
                return token
        except Exception as e:
            if log_callback:
                log_callback(f"[!] CapSolver 失败: {e}")

    raise Exception("Turnstile 获取 token 失败")


def build_profile():
    given_name_pool = [
        "Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo",
        "Owen", "Aiden", "Elio", "Aron", "Ivan", "Nolan", "Evan", "Kai",
        "Caleb", "Adam", "Ezra", "Miles", "Logan", "Carter", "Hunter", "Jason",
        "Brian", "Dylan", "Alex", "Colin", "Blake", "Gavin", "Henry", "Julian",
        "Kevin", "Louis", "Marcus", "Nathan", "Oscar", "Peter", "Quinn", "Robin",
        "Simon", "Tristan", "Victor", "Wesley", "Xavier", "Yuri", "Zane", "Felix",
        "Aaron", "Damian",
    ]
    family_name_pool = [
        "Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun",
        "Guo", "He", "Yang", "Wu", "Zhou", "Tang", "Qin", "Shi",
        "Fang", "Peng", "Cao", "Deng", "Fan", "Fu", "Gao", "Han",
        "Hu", "Jiang", "Kong", "Lu", "Ma", "Nie", "Pan", "Qiao",
        "Ren", "Shao", "Tian", "Xie", "Yan", "Yao", "Yu", "Zeng",
        "Bai", "Duan", "Hou", "Jin", "Kang", "Luo", "Mao", "Song",
        "Wei", "Xiong",
    ]
    given_name = random.choice(given_name_pool)
    family_name = random.choice(family_name_pool)
    password = "N" + secrets.token_hex(4) + "!a7#" + secrets.token_urlsafe(6)
    return given_name, family_name, password


def fill_profile_and_submit(timeout=120, log_callback=None, cancel_callback=None):
    given_name, family_name, password = build_profile()
    deadline = time.time() + timeout
    form_filled_once = False
    wait_cf_since = None
    last_cf_retry_at = 0.0

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if not form_filled_once:
            filled = _get_page().run_js(
                """
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}

function setInputValue(input, value) {
    if (!input) return false;
    input.focus();
    input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.blur();
    return String(input.value || '').trim() === String(value || '').trim();
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"], input[aria-label*="名"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"], input[aria-label*="姓"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');

if (!givenInput || !familyInput || !passwordInput) return 'not-ready';

const ok1 = setInputValue(givenInput, givenName);
const ok2 = setInputValue(familyInput, familyName);
const ok3 = setInputValue(passwordInput, password);

if (!ok1 || !ok2 || !ok3) return 'fill-failed';

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});

// 必须等待 Cloudflare 校验通过后再提交
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

if (submitBtn) {
    return 'ready-to-submit';
}
return 'filled-no-submit';
            """,
                given_name,
                family_name,
                password,
            )

            if isinstance(filled, str) and filled.startswith("wait-cloudflare"):
                form_filled_once = True
                token_len = filled.split(":", 1)[1] if ":" in filled else "0"
                if log_callback:
                    log_callback(f"[*] 资料已填写，等待 Cloudflare 人机验证通过... 当前token长度={token_len}")
                now = time.time()
                if wait_cf_since is None:
                    wait_cf_since = now
                # Community-tuned: trigger Turnstile quickly (~1.2s) instead of
                # idle-waiting 12s. Keep CapSolver/stale-token safeguards in getTurnstileToken.
                if now - last_cf_retry_at >= 1.2:
                    if log_callback and (now - wait_cf_since) >= 1.2:
                        log_callback("[*] Cloudflare 验证卡住，开始二次复用 Turnstile...")
                    try:
                        token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                        if token:
                            synced = _get_page().run_js(
                                """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                                """,
                                token,
                            )
                            if log_callback:
                                log_callback(f"[*] Turnstile 二次复用完成，回填长度={synced}")
                    except Exception as cf_exc:
                        if log_callback:
                            log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                    last_cf_retry_at = now
                sleep_with_cancel(0.45, cancel_callback)
                continue

            if filled in ("ready-to-submit", "filled-no-submit"):
                form_filled_once = True
            elif filled == "fill-failed" and log_callback:
                log_callback("[Debug] 资料输入失败，重试中...")
                sleep_with_cancel(0.5, cancel_callback)
                continue
            elif filled == "not-ready":
                sleep_with_cancel(0.5, cancel_callback)
                continue

        submit_state = _get_page().run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'no-submit-button:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'submitted';
            """
        )

        if isinstance(submit_state, str) and submit_state.startswith("wait-cloudflare"):
            if log_callback:
                token_len = submit_state.split(":", 1)[1] if ":" in submit_state else "0"
                log_callback(f"[*] 等待 Cloudflare 人机验证通过后再提交... 当前token长度={token_len}")
            now = time.time()
            if wait_cf_since is None:
                wait_cf_since = now
            if now - last_cf_retry_at >= 1.2:
                if log_callback and (now - wait_cf_since) >= 1.2:
                    log_callback("[*] 提交前仍卡住，自动再次复用 Turnstile...")
                try:
                    token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                    if token:
                        synced = _get_page().run_js(
                            """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                            """,
                            token,
                        )
                        if log_callback:
                            log_callback(f"[*] Turnstile 二次复用完成，回填长度={synced}")
                except Exception as cf_exc:
                    if log_callback:
                        log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                last_cf_retry_at = now
            sleep_with_cancel(0.45, cancel_callback)
            continue

        if submit_state == "submitted":
            if log_callback:
                log_callback(f"[*] 已填写注册资料并提交: {given_name} {family_name}")
            return {"given_name": given_name, "family_name": family_name, "password": password}
        wait_cf_since = None
        if isinstance(submit_state, str) and submit_state.startswith("no-submit-button") and log_callback:
            visible_buttons = submit_state.split(":", 1)[1] if ":" in submit_state else ""
            suffix = f" 可见按钮: {visible_buttons}" if visible_buttons else ""
            log_callback(f"[Debug] 未找到提交按钮，继续等待页面稳定...{suffix}")

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("最终注册页资料填写失败")


def wait_for_sso_cookie(timeout=None, log_callback=None, cancel_callback=None):
    if timeout is None:
        timeout = int(config.get("sso_cookie_timeout_sec", 150) or 150)
    timeout = max(60, int(timeout))
    deadline = time.time() + timeout
    last_seen_names = set()
    last_submit_retry = 0.0
    last_cf_retry_at = 0.0
    last_warmup_at = 0.0
    last_accounts_nav_at = 0.0
    final_no_submit_state = ""
    final_no_submit_since = None
    final_no_submit_timeout = 25
    if log_callback:
        log_callback(f"[*] SSO 等待窗口: {timeout}s")

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            refresh_active_page()
            if _get_page() is None:
                sleep_with_cancel(1, cancel_callback)
                continue

            # Community-style human signal while waiting for SSO (mouse/scroll warmup)
            now = time.time()
            if now - last_warmup_at >= 8.0:
                try:
                    _get_page().run_js(
                        r"""
try {
  const x = 120 + Math.floor(Math.random() * 400);
  const y = 100 + Math.floor(Math.random() * 280);
  window.scrollBy(0, (Math.random() > 0.5 ? 1 : -1) * (40 + Math.floor(Math.random() * 80)));
  document.dispatchEvent(new MouseEvent('mousemove', {clientX: x, clientY: y, bubbles: true}));
} catch (e) {}
"""
                    )
                except Exception:
                    pass
                last_warmup_at = now

            # If already past signup but cookie lagging, nudge accounts.x.ai once
            if now - last_accounts_nav_at >= 20.0:
                try:
                    url = str(_get_page().url or "")
                    if "accounts.x.ai" in url and "sign-up" not in url and "sso" not in last_seen_names:
                        # soft reload current account page to surface cookies
                        _get_page().refresh()
                        if log_callback:
                            log_callback("[*] SSO 等待中：刷新 accounts 页以促发 cookie")
                        last_accounts_nav_at = now
                except Exception:
                    pass

            # 仍停留在“完成注册”页时，若 Cloudflare 已通过，周期性重试点击提交
            if now - last_submit_retry >= 2.5:
                retried = _get_page().run_js(
                    r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const titleHit = !!Array.from(document.querySelectorAll('h1,h2,div,span')).find((el) => {
    const t = (el.textContent || '').replace(/\s+/g, '');
    const lower = t.toLowerCase();
    return t.includes('完成注册') || lower.includes('completeyoursignup') || lower.includes('completesignup');
});
if (!titleHit) return 'not-final-page';

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solved = token.length >= 80;
    if (!solved) return 'final-page-wait-cf:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'final-page-no-submit:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'final-page-clicked-submit';
                    """
                )
                last_submit_retry = now
                if log_callback and (retried == "final-page-clicked-submit" or (isinstance(retried, str) and retried.startswith("final-page-no-submit"))):
                    log_callback(f"[Debug] 最终页状态: {retried}")
                if isinstance(retried, str) and retried.startswith("final-page-no-submit"):
                    if retried != final_no_submit_state:
                        final_no_submit_state = retried
                        final_no_submit_since = now
                    elif final_no_submit_since and now - final_no_submit_since >= final_no_submit_timeout:
                        raise AccountRetryNeeded(
                            f"最终注册页状态 {final_no_submit_timeout}s 未变化且未找到提交按钮，重试当前账号: {retried}"
                        )
                else:
                    final_no_submit_state = ""
                    final_no_submit_since = None
                if log_callback and isinstance(retried, str) and retried.startswith("final-page-wait-cf"):
                    token_len = retried.split(":", 1)[1] if ":" in retried else "0"
                    log_callback(f"[Debug] 最终页状态: final-page-wait-cf, token长度={token_len}")
                    if now - last_cf_retry_at >= 10:
                        if log_callback:
                            log_callback("[*] 最终页 Cloudflare 卡住，自动二次复用 Turnstile...")
                        try:
                            token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                            if token:
                                synced = _get_page().run_js(
                                    """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return 'no-input';
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));

// 触发 Turnstile success callback
try {
  const container = document.querySelector('.cf-turnstile, [data-sitekey]');
  if (container) {
    const cbName = container.getAttribute('data-callback');
    if (cbName && typeof window[cbName] === 'function') window[cbName](token);
  }
} catch(e) {}

// 注入后立刻尝试提交
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (submitBtn) { submitBtn.focus(); submitBtn.click(); }

return 'injected-len=' + String(cfInput.value || '').trim().length + (submitBtn ? ',submitted' : ',no-btn');
                                    """,
                                    token,
                                )
                                if log_callback:
                                    log_callback(f"[*] 最终页 Turnstile 二次复用完成: {synced}")
                        except Exception as cf_exc:
                            if log_callback:
                                log_callback(f"[Debug] 最终页 Turnstile 二次复用失败: {cf_exc}")
                        last_cf_retry_at = now

            cookies = _get_page().cookies(all_domains=True, all_info=True) or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()

                if name:
                    last_seen_names.add(name)

                if name == "sso" and value:
                    if log_callback:
                        log_callback("[*] 已获取到 sso cookie")
                    return value
        except PageDisconnectedError:
            refresh_active_page()
        except AccountRetryNeeded:
            raise
        except Exception:
            pass

        sleep_with_cancel(1, cancel_callback)

    raise Exception(
        f"等待超时：未获取到 sso cookie。已看到 cookies: {sorted(last_seen_names)}"
    )


class GrokRegisterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Grok 注册机")
        self.root.geometry("1120x900")
        self.root.minsize(960, 700)
        self.is_running = False
        self.batch_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        self.stop_requested = False
        self.ui_queue = queue.Queue()
        self.accounts_output_file = ""
        self.setup_ui()

    def setup_ui(self):
        load_config()
        main_frame = tk.Frame(self.root, bg=UI_BG, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(3, weight=1)

        config_frame = tk.LabelFrame(
            main_frame,
            text="配置",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=10,
            pady=10,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        config_frame.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        config_frame.grid_columnconfigure(1, weight=1, minsize=260)
        config_frame.grid_columnconfigure(3, weight=1, minsize=260)

        def add_label(row, column, text):
            tk_label(config_frame, text=text, bg=UI_PANEL_BG).grid(
                row=row,
                column=column,
                sticky=tk.W,
                padx=(0, 6),
                pady=3,
            )

        def add_field(widget, row, column, columnspan=1, sticky=tk.EW):
            widget.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky=sticky,
                padx=(0, 14),
                pady=3,
            )

        add_label(0, 0, "邮箱服务商:")
        self.email_provider_var = tk.StringVar(value=config.get("email_provider", "duckmail"))
        self.email_provider_combo = tk_option_menu(
            config_frame,
            self.email_provider_var,
            [
                "duckmail",
                "yyds",
                "cloudflare",
                "tempmail_lol",
                "mailtm",
                "gptmail",
                "mailsapi",
                "hotmail",
                "cloud_mail",
                "yunmeng",
            ],
            width=12,
        )
        add_field(self.email_provider_combo, 0, 1, sticky=tk.W)

        add_label(0, 2, "注册数量:")
        self.count_var = tk.StringVar(value=str(config.get("register_count", 1)))
        self.count_spinbox = tk.Spinbox(
            config_frame,
            from_=1,
            to=2500,
            width=8,
            textvariable=self.count_var,
            bg=UI_ENTRY_BG,
            fg=UI_FG,
            insertbackground=UI_FG,
            buttonbackground=UI_BUTTON_BG,
            disabledbackground="#2f2f2f",
            disabledforeground=UI_MUTED_FG,
            relief=tk.SOLID,
        )
        add_field(self.count_spinbox, 0, 3, sticky=tk.W)

        add_label(1, 0, "注册选项:")
        self.nsfw_var = tk.BooleanVar(value=config.get("enable_nsfw", True))
        self.nsfw_check = tk_checkbutton(config_frame, text="注册后开启 NSFW", variable=self.nsfw_var)
        add_field(self.nsfw_check, 1, 1, sticky=tk.W)

        add_label(1, 2, "代理（可选）:")
        self.proxy_var = tk.StringVar(value=config.get("proxy", ""))
        self.proxy_entry = tk_entry(config_frame, textvariable=self.proxy_var, width=34)
        add_field(self.proxy_entry, 1, 3)

        add_label(2, 0, "DuckMail API Key:")
        self.api_key_var = tk.StringVar(value=config.get("duckmail_api_key", ""))
        self.api_key_entry = tk_entry(config_frame, textvariable=self.api_key_var, width=34)
        add_field(self.api_key_entry, 2, 1)

        add_label(2, 2, "Cloudflare 鉴权模式:")
        self.cloudflare_auth_mode_var = tk.StringVar(value=config.get("cloudflare_auth_mode", "none"))
        self.cloudflare_auth_mode_combo = tk_option_menu(
            config_frame, self.cloudflare_auth_mode_var, ["query-key", "bearer", "x-api-key", "x-admin-auth", "none"], width=12
        )
        add_field(self.cloudflare_auth_mode_combo, 2, 3, sticky=tk.W)

        add_label(3, 0, "Cloudflare API Base:")
        self.cloudflare_api_base_var = tk.StringVar(value=config.get("cloudflare_api_base", ""))
        self.cloudflare_api_base_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_base_var, width=72)
        add_field(self.cloudflare_api_base_entry, 3, 1, columnspan=3)

        add_label(4, 0, "Cloudflare API Key:")
        self.cloudflare_api_key_var = tk.StringVar(value=config.get("cloudflare_api_key", ""))
        self.cloudflare_api_key_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_key_var, width=34)
        add_field(self.cloudflare_api_key_entry, 4, 1)

        add_label(4, 2, "CF 路径:")
        self.cloudflare_paths_var = tk.StringVar(
            value=",".join(
                [
                    config.get("cloudflare_path_domains", "/api/domains"),
                    config.get("cloudflare_path_accounts", "/api/new_address"),
                    config.get("cloudflare_path_token", "/api/token"),
                    config.get("cloudflare_path_messages", "/api/mails"),
                ]
            )
        )
        self.cloudflare_paths_entry = tk_entry(config_frame, textvariable=self.cloudflare_paths_var, width=34)
        add_field(self.cloudflare_paths_entry, 4, 3)

        add_label(5, 0, "grok2api 本地入池:")
        self.grok2api_local_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_local", True)))
        self.grok2api_local_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_local_auto_var)
        add_field(self.grok2api_local_auto_check, 5, 1, sticky=tk.W)

        add_label(5, 2, "grok2api 池名:")
        self.grok2api_pool_name_var = tk.StringVar(value=str(config.get("grok2api_pool_name", "ssoBasic")))
        self.grok2api_pool_name_combo = tk_option_menu(
            config_frame, self.grok2api_pool_name_var, ["ssoBasic", "ssoSuper"], width=12
        )
        add_field(self.grok2api_pool_name_combo, 5, 3, sticky=tk.W)

        add_label(6, 0, "本地 token.json:")
        self.grok2api_local_file_var = tk.StringVar(value=str(config.get("grok2api_local_token_file", "")))
        self.grok2api_local_file_entry = tk_entry(config_frame, textvariable=self.grok2api_local_file_var, width=72)
        add_field(self.grok2api_local_file_entry, 6, 1, columnspan=3)

        add_label(7, 0, "grok2api 远端入池:")
        self.grok2api_remote_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_remote", False)))
        self.grok2api_remote_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_remote_auto_var)
        add_field(self.grok2api_remote_auto_check, 7, 1, sticky=tk.W)

        add_label(8, 0, "grok2api 远端 Base:")
        self.grok2api_remote_base_var = tk.StringVar(value=str(config.get("grok2api_remote_base", "")))
        self.grok2api_remote_base_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_base_var, width=72)
        add_field(self.grok2api_remote_base_entry, 8, 1, columnspan=3)

        add_label(9, 0, "grok2api 远端 app_key:")
        self.grok2api_remote_key_var = tk.StringVar(value=str(config.get("grok2api_remote_app_key", "")))
        self.grok2api_remote_key_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_key_var, width=72)
        add_field(self.grok2api_remote_key_entry, 9, 1, columnspan=3)

        btn_frame = tk.Frame(main_frame, bg=UI_BG)
        btn_frame.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        self.start_btn = tk_button(btn_frame, text="开始注册", command=self.start_registration)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk_button(btn_frame, text="停止", command=self.stop_registration, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.clear_btn = tk_button(btn_frame, text="清空日志", command=self.clear_log)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        status_frame = tk.Frame(main_frame, bg=UI_BG)
        status_frame.grid(row=2, column=0, sticky=tk.EW, pady=(0, 6))
        self.status_var = tk.StringVar(value="就绪")
        tk_label(status_frame, text="状态: ").pack(side=tk.LEFT)
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, bg=UI_BG, fg="green")
        self.status_label.pack(side=tk.LEFT)
        self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0")
        tk.Label(status_frame, textvariable=self.stats_var, bg=UI_BG, fg=UI_FG).pack(side=tk.RIGHT)
        log_frame = tk.LabelFrame(
            main_frame,
            text="日志",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=5,
            pady=5,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        log_frame.grid(row=3, column=0, sticky=tk.NSEW)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=18,
            width=60,
            bg="#111111",
            fg="#f5f5f5",
            insertbackground="#f5f5f5",
            selectbackground="#345a8a",
            selectforeground="#ffffff",
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#555555",
        )
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        self.log("[*] GUI 已就绪，配置已加载")
        self.log(f"[*] 当前邮箱服务商: {self.email_provider_var.get()} | 注册数量: {self.count_var.get()}")

    def log(self, message):
        if not should_emit_log(message):
            return
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        try:
            self.log_text.insert(tk.END, f"{line}\n")
            # 防止长时间运行日志区无限增长导致卡顿
            try:
                line_count = int(float(str(self.log_text.index("end-1c").split(".")[0])))
                if line_count > 5000:
                    self.log_text.delete("1.0", f"{line_count - 4000}.0")
            except Exception:
                pass
            self.log_text.see(tk.END)
        except Exception:
            pass

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def update_stats(self):
        self.stats_var.set(f"成功: {self.success_count} | 失败: {self.fail_count}")

    def _set_running_ui(self, running):
        self.is_running = running
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.status_var.set("运行中..." if running else "就绪")
        self.status_label.config(foreground="blue" if running else "green")

    def should_stop(self):
        return self.stop_requested or not self.is_running

    def start_registration(self):
        if self.is_running:
            self.log("[!] 当前已有任务在运行")
            return

        config["email_provider"] = self.email_provider_var.get().strip() or "duckmail"
        config["enable_nsfw"] = bool(self.nsfw_var.get())
        config["proxy"] = self.proxy_var.get().strip()
        config["duckmail_api_key"] = self.api_key_var.get().strip()
        config["cloudflare_api_base"] = self.cloudflare_api_base_var.get().strip()
        config["cloudflare_api_key"] = self.cloudflare_api_key_var.get().strip()
        config["cloudflare_auth_mode"] = self.cloudflare_auth_mode_var.get().strip() or "none"
        config["grok2api_auto_add_local"] = bool(self.grok2api_local_auto_var.get())
        config["grok2api_local_token_file"] = self.grok2api_local_file_var.get().strip()
        config["grok2api_pool_name"] = self.grok2api_pool_name_var.get().strip() or "ssoBasic"
        config["grok2api_auto_add_remote"] = bool(self.grok2api_remote_auto_var.get())
        config["grok2api_remote_base"] = self.grok2api_remote_base_var.get().strip()
        config["grok2api_remote_app_key"] = self.grok2api_remote_key_var.get().strip()
        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]
        if len(raw_paths) >= 4:
            config["cloudflare_path_domains"] = raw_paths[0] if raw_paths[0].startswith("/") else ("/" + raw_paths[0])
            config["cloudflare_path_accounts"] = raw_paths[1] if raw_paths[1].startswith("/") else ("/" + raw_paths[1])
            config["cloudflare_path_token"] = raw_paths[2] if raw_paths[2].startswith("/") else ("/" + raw_paths[2])
            config["cloudflare_path_messages"] = raw_paths[3] if raw_paths[3].startswith("/") else ("/" + raw_paths[3])
        save_config()
        if config["email_provider"] == "cloudflare" and not config["cloudflare_api_base"]:
            self.log("[!] Cloudflare 模式需要先填写 Cloudflare API Base")
            return
        try:
            count = int(self.count_var.get())
        except Exception:
            self.log("[!] 注册数量无效")
            return
        config["register_count"] = count
        save_config()
        self.stop_requested = False
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.accounts_output_file = os.path.join(
            os.path.dirname(__file__), f"accounts_{now}.txt"
        )
        self.update_stats()
        self._set_running_ui(True)
        self.log(f"[*] 配置已保存，开始执行。目标数量: {count}")
        self.log(f"[*] 成功账号将实时保存到: {self.accounts_output_file}")
        threading.Thread(
            target=self.run_registration,
            args=(count,),
            daemon=True,
        ).start()

    def stop_registration(self):
        self.stop_requested = True
        self.log("[!] 用户停止注册")

    def run_registration(self, count):
        stop_speed = threading.Event()
        interval = float(config.get("speed_log_interval_sec", 60) or 60)
        def _gui_counts():
            with _stats_lock:
                return self.success_count, self.fail_count

        speed_thread, _meter = start_speed_logger(
            get_counts=_gui_counts,
            log_callback=self.log,
            stop_event=stop_speed,
            interval_sec=interval,
        )
        try:
            concurrent = max(1, int(config.get("concurrent_count", 1) or 1))
            self.log(f"[*] 日志级别: {get_log_level()} | 速度统计间隔: {int(interval)}s")
            if concurrent <= 1:
                self._run_single_worker(count, worker_id=0)
            else:
                self._run_concurrent_workers(count, concurrent)
        except Exception as exc:
            self.log(f"[!] 任务异常: {exc}")
        finally:
            stop_speed.set()
            try:
                speed_thread.join(timeout=2)
            except Exception:
                pass
            _wait_cpa_async_threads(
                timeout=5 if self.should_stop() else 300,
                log_callback=self.log,
                skip_if_stopping=self.should_stop,
            )
            self._set_running_ui(False)
            self.log(
                f"[*] 任务结束。成功 {self.success_count} | 失败 {self.fail_count}"
            )

    def _run_concurrent_workers(self, total_count, worker_count):
        import queue
        task_queue = queue.Queue()
        for idx in range(total_count):
            task_queue.put(idx)
        threads = []
        for wid in range(worker_count):
            if self.should_stop():
                break
            t = threading.Thread(
                target=self._worker_loop,
                args=(wid, task_queue, total_count),
                daemon=True,
            )
            t.start()
            threads.append(t)
            sleep_with_cancel(2, self.should_stop)
        _join_threads_interruptible(
            threads,
            should_stop=self.should_stop,
            timeout=None,
            poll=0.5,
        )
        if self.should_stop():
            _join_threads_interruptible(threads, should_stop=None, timeout=5, poll=0.5)

    def _worker_loop(self, worker_id, task_queue, total_count):
        _set_worker_id(worker_id)
        prefix = f"[W{worker_id}]"
        log_fn = lambda msg: self.log(f"{prefix} {msg}")
        try:
            start_browser(log_callback=log_fn)
            log_fn(f"[*] Worker-{worker_id} 浏览器已启动")
        except Exception as e:
            log_fn(f"[!] Worker-{worker_id} 浏览器启动失败: {e}")
            return
        restart_every = int(config.get("browser_restart_every", 10) or 0)
        local_success = 0
        local_attempts = 0
        max_slot_retry = 3
        try:
            while not self.should_stop():
                try:
                    task_queue.get_nowait()
                except Exception:
                    break
                slot_done = False
                retry_count_for_slot = 0
                while not slot_done and not self.should_stop():
                    try:
                        self._register_one_account(log_fn, worker_id, local_success)
                        local_success += 1
                        slot_done = True
                    except RegistrationCancelled:
                        return
                    except AccountRetryNeeded as exc:
                        retry_count_for_slot += 1
                        if retry_count_for_slot <= max_slot_retry:
                            log_fn(
                                f"[!] 账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                            )
                            restart_browser(log_callback=log_fn)
                            continue
                        with _stats_lock:
                            self.fail_count += 1
                        log_fn(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
                        slot_done = True
                    except Exception as exc:
                        with _stats_lock:
                            self.fail_count += 1
                        log_fn(f"[-] 注册失败: {exc}")
                        # domain stats: email often not in scope on GUI fail path
                        slot_done = True
                    finally:
                        local_attempts += 1
                        self.update_stats()
                        if self.should_stop():
                            break
                        # 与稳定版/单 worker 一致：每账号完整重启，避免 SSO/TOS 会话残留落到 tos-gate
                        if _get_browser() is None:
                            start_browser(log_callback=log_fn)
                        else:
                            if restart_every > 0 and local_attempts % restart_every == 0:
                                log_fn(
                                    f"[*] Worker-{worker_id} 已处理 {local_attempts} 个账号，周期重启浏览器"
                                )
                            restart_browser(log_callback=log_fn)
                        sleep_with_cancel(1, self.should_stop)
        finally:
            stop_browser()

    def _register_one_account(self, log_fn, worker_id=0, local_success=0):
        email = ""
        dev_token = ""
        code = ""
        mail_ok = False
        egress = rotate_egress_proxy(log_fn)
        clash_node = egress.get("clash_node")
        try:
            result = self._register_one_account_body(
                log_fn, worker_id, local_success, clash_node
            )
            report_egress_result(True, egress)
            note_register_outcome(True, log_fn=log_fn)
            return result
        except Exception as exc:
            report_egress_result(False, egress)
            note_register_outcome(False, exc, log_fn=log_fn)
            raise
    def _register_one_account_body(self, log_fn, worker_id=0, local_success=0, clash_node=None):
        email = ""
        dev_token = ""
        code = ""
        mail_ok = False
        max_mail_retry = 3
        for mail_try in range(1, max_mail_retry + 1):
            log_fn(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
            open_signup_page(log_callback=log_fn, cancel_callback=self.should_stop)
            # Human-like pause between page open and email create
            sleep_with_cancel(random.uniform(0.8, 2.5), self.should_stop)
            log_fn("[*] 2. 创建邮箱并提交")
            email, dev_token = fill_email_and_submit(
                log_callback=log_fn, cancel_callback=self.should_stop
            )
            log_fn(f"[*] 邮箱: {email}")
            try:
                with _io_lock:
                    with open(
                        os.path.join(os.path.dirname(__file__), "mail_credentials.txt"),
                        "a", encoding="utf-8",
                    ) as f:
                        f.write(f"{email}\t{dev_token}\n")
            except Exception:
                pass
            log_fn("[*] 3. 拉取验证码")
            try:
                code = fill_code_and_submit(
                    email, dev_token,
                    log_callback=log_fn, cancel_callback=self.should_stop,
                )
                mail_ok = True
                break
            except Exception as mail_exc:
                msg = str(mail_exc)
                if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                    log_fn(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                    restart_browser(log_callback=log_fn)
                    sleep_with_cancel(1, self.should_stop)
                    continue
                raise
        if not mail_ok:
            raise Exception("验证码阶段失败，已达到最大重试次数")
        log_fn(f"[*] 验证码: {code}")
        log_fn("[*] 4. 填写资料")
        profile = fill_profile_and_submit(
            log_callback=log_fn, cancel_callback=self.should_stop
        )
        log_fn(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
        log_fn("[*] 5. 等待 sso cookie")
        sso = wait_for_sso_cookie(
            log_callback=log_fn, cancel_callback=self.should_stop
        )
        _cpa_page = _get_page()
        cpa_result = _enqueue_cpa_mint(
            email, profile.get("password", ""), sso, log_fn, page=_cpa_page
        )
        if config.get("enable_nsfw", True):
            log_fn("[*] 6. 开启 NSFW")
            nsfw_ok, nsfw_msg = enable_nsfw_for_token(sso, log_callback=log_fn)
            if nsfw_ok:
                log_fn(f"[+] NSFW 开启成功: {nsfw_msg}")
            else:
                log_fn(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
        with _stats_lock:
            self.results.append({"email": email, "sso": sso, "profile": profile})
        try:
            line = f"{email}----{profile.get('password','')}----{sso}\n"
            with _io_lock:
                with open(self.accounts_output_file, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as file_exc:
            log_fn(f"[Debug] 保存账号文件失败: {file_exc}")
        run_post_register_pipeline(
            sso, email, log_callback=log_fn, cpa_result=cpa_result
        )
        with _stats_lock:
            self.success_count += 1
        log_fn(f"[+] 注册成功: {email}")
        try:
            import domain_health as _dh

            _dh.record_success(email, cfg=config)
        except Exception:
            pass
        # Community proxy_pool pattern: credit the exit node on success
        try:
            from clash_proxy import report_success
            report_success(clash_node)
        except Exception:
            pass

    def _run_single_worker(self, count, worker_id=0):
        _set_worker_id(worker_id)
        start_browser(log_callback=self.log)
        self.log("[*] 浏览器已启动")
        restart_every = int(config.get("browser_restart_every", 10) or 0)
        i = 0
        retry_count_for_slot = 0
        max_slot_retry = 3
        while i < count:
            if self.should_stop():
                break
            if not check_daily_success_cap(self.log):
                break
            self.log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
            try:
                self._register_one_account(self.log, worker_id, i)
                retry_count_for_slot = 0
                i += 1
                if restart_every > 0 and i > 0 and i % restart_every == 0:
                    self.log(f"[*] 已注册 {i} 个账号，重启浏览器")
                    restart_browser(log_callback=self.log)
                if (
                    self.success_count > 0
                    and self.success_count % MEMORY_CLEANUP_INTERVAL == 0
                    and i < count
                ):
                    cleanup_runtime_memory(
                        log_callback=self.log,
                        reason=f"已成功 {self.success_count} 个账号，执行定期清理",
                    )
            except RegistrationCancelled:
                self.log("[!] 注册被用户停止")
                break
            except AccountRetryNeeded as exc:
                retry_count_for_slot += 1
                if retry_count_for_slot <= max_slot_retry:
                    self.log(f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}")
                else:
                    with _stats_lock:
                        self.fail_count += 1
                    self.log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
                    retry_count_for_slot = 0
                    i += 1
            except Exception as exc:
                with _stats_lock:
                    self.fail_count += 1
                retry_count_for_slot = 0
                i += 1
                self.log(f"[-] 注册失败: {exc}")
            finally:
                self.update_stats()
                if self.should_stop():
                    break
                if _get_browser() is None:
                    start_browser(log_callback=self.log)
                else:
                    restart_browser(log_callback=self.log)
                sleep_with_cancel(1, self.should_stop)
        stop_browser()


class CliStopController:
    def __init__(self):
        self.stop_requested = False
        self._sigint_count = 0
        self._lock = threading.Lock()

    def should_stop(self):
        return self.stop_requested

    def stop(self):
        with self._lock:
            self.stop_requested = True

    def handle_sigint(self, signum=None, frame=None):
        """第一次 Ctrl+C 请求优雅停止；第二次强制退出。"""
        with self._lock:
            self._sigint_count += 1
            count = self._sigint_count
            self.stop_requested = True
        if count == 1:
            cli_log("[!] 收到 Ctrl+C，正在停止...（再按一次强制退出）")
            return
        cli_log("[!] 再次收到 Ctrl+C，强制退出")
        try:
            os._exit(1)
        except Exception:
            raise SystemExit(1)


def cli_log(message):
    if not should_emit_log(message):
        return
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def _install_cli_sigint_handler(controller):
    """安装可重入的 Ctrl+C 处理。Windows/Git Bash 下尽量可用。"""
    previous = None
    try:
        import signal

        previous = signal.getsignal(signal.SIGINT)

        def _handler(signum, frame):
            controller.handle_sigint(signum, frame)

        signal.signal(signal.SIGINT, _handler)
        return previous
    except Exception:
        return previous


def _restore_sigint_handler(previous):
    try:
        import signal

        if previous is not None:
            signal.signal(signal.SIGINT, previous)
    except Exception:
        pass


def _register_one_account_cli(log_fn, stop_fn, accounts_output_file):
    email_holder = {"email": ""}
    egress = rotate_egress_proxy(log_fn)
    clash_node = egress.get("clash_node")
    try:
        result = _register_one_account_cli_body(
            log_fn,
            stop_fn,
            accounts_output_file,
            clash_node,
            email_holder=email_holder,
        )
        report_egress_result(True, egress)
        note_register_outcome(True, log_fn=log_fn)
        return result
    except Exception as exc:
        report_egress_result(False, egress)
        note_register_outcome(False, exc, log_fn=log_fn)
        try:
            import domain_health as _dh

            em = str(email_holder.get("email") or "")
            if em:
                _dh.record_fail(
                    em, reason=_dh.classify_fail_reason(exc), cfg=config
                )
        except Exception:
            pass
        raise

def _register_one_account_cli_body(
    log_fn, stop_fn, accounts_output_file, clash_node=None, email_holder=None
):
    email = ""
    dev_token = ""
    code = ""
    mail_ok = False
    max_mail_retry = 3
    if email_holder is None:
        email_holder = {"email": ""}
    for mail_try in range(1, max_mail_retry + 1):
        log_fn(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
        open_signup_page(log_callback=log_fn, cancel_callback=stop_fn)
        # Human-like pause between page open and email create
        sleep_with_cancel(random.uniform(0.8, 2.5), stop_fn)
        log_fn("[*] 2. 创建邮箱并提交")
        email, dev_token = fill_email_and_submit(
            log_callback=log_fn, cancel_callback=stop_fn
        )
        email_holder["email"] = email or ""
        log_fn(f"[*] 邮箱: {email}")
        try:
            with _io_lock:
                with open(
                    os.path.join(os.path.dirname(__file__), "mail_credentials.txt"),
                    "a", encoding="utf-8",
                ) as f:
                    f.write(f"{email}\t{dev_token}\n")
        except Exception:
            pass
        log_fn("[*] 3. 拉取验证码")
        try:
            code = fill_code_and_submit(
                email, dev_token,
                log_callback=log_fn, cancel_callback=stop_fn,
            )
            mail_ok = True
            break
        except Exception as mail_exc:
            msg = str(mail_exc)
            if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                log_fn(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                restart_browser(log_callback=log_fn)
                sleep_with_cancel(1, stop_fn)
                continue
            raise
    if not mail_ok:
        raise Exception("验证码阶段失败，已达到最大重试次数")
    log_fn(f"[*] 验证码: {code}")
    log_fn("[*] 4. 填写资料")
    profile = fill_profile_and_submit(
        log_callback=log_fn, cancel_callback=stop_fn
    )
    log_fn(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
    log_fn("[*] 5. 等待 sso cookie")
    sso = wait_for_sso_cookie(
        log_callback=log_fn, cancel_callback=stop_fn
    )
    _cpa_page = _get_page()
    cpa_result = _enqueue_cpa_mint(
        email, profile.get("password", ""), sso, log_fn, page=_cpa_page
    )
    if config.get("enable_nsfw", True):
        log_fn("[*] 6. 开启 NSFW")
        nsfw_ok, nsfw_msg = enable_nsfw_for_token(sso, log_callback=log_fn)
        if nsfw_ok:
            log_fn(f"[+] NSFW 开启成功: {nsfw_msg}")
        else:
            log_fn(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
    try:
        line = f"{email}----{profile.get('password','')}----{sso}\n"
        with _io_lock:
            with open(accounts_output_file, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as file_exc:
        log_fn(f"[Debug] 保存账号文件失败: {file_exc}")
    run_post_register_pipeline(
        sso, email, log_callback=log_fn, cpa_result=cpa_result
    )
    log_fn(f"[+] 注册成功: {email}")
    try:
        import domain_health as _dh

        _dh.record_success(email, cfg=config)
    except Exception:
        pass
    # Community proxy_pool pattern: credit the exit node on success
    try:
        from clash_proxy import report_success
        report_success(clash_node)
    except Exception:
        pass


def _cli_worker_loop(worker_id, task_queue, total_count, controller, accounts_output_file, stats):
    _set_worker_id(worker_id)
    prefix = f"[W{worker_id}]"
    log_fn = lambda msg: cli_log(f"{prefix} {msg}")
    try:
        start_browser(log_callback=log_fn)
        log_fn(f"[*] Worker-{worker_id} 浏览器已启动")
    except Exception as e:
        log_fn(f"[!] Worker-{worker_id} 浏览器启动失败: {e}")
        return
    restart_every = int(config.get("browser_restart_every", 10) or 0)
    local_success = 0
    local_attempts = 0
    max_slot_retry = 3
    try:
        while not controller.should_stop():
            if not check_daily_success_cap(log_fn):
                controller.stop()
                break
            try:
                task_queue.get_nowait()
            except Exception:
                break
            slot_done = False
            retry_count_for_slot = 0
            while not slot_done and not controller.should_stop():
                try:
                    _register_one_account_cli(log_fn, controller.should_stop, accounts_output_file)
                    with stats["lock"]:
                        stats["success"] += 1
                        local_success += 1
                    slot_done = True
                except RegistrationCancelled:
                    return
                except AccountRetryNeeded as exc:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= max_slot_retry:
                        log_fn(
                            f"[!] 账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                        )
                        restart_browser(log_callback=log_fn)
                        continue
                    with stats["lock"]:
                        stats["fail"] += 1
                    log_fn(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
                    slot_done = True
                except Exception as exc:
                    with stats["lock"]:
                        stats["fail"] += 1
                    log_fn(f"[-] 注册失败: {exc}")
                    # domain_health recorded inside _register_one_account_cli
                    slot_done = True
                finally:
                    local_attempts += 1
                    if controller.should_stop():
                        break
                    # 与稳定版/单 worker 一致：每账号完整重启，避免 SSO/TOS 会话残留落到 tos-gate
                    if _get_browser() is None:
                        start_browser(log_callback=log_fn)
                    else:
                        if restart_every > 0 and local_attempts % restart_every == 0:
                            log_fn(
                                f"[*] Worker-{worker_id} 已处理 {local_attempts} 个账号，周期重启浏览器"
                            )
                        restart_browser(log_callback=log_fn)
                    sleep_with_cancel(1, controller.should_stop)
    finally:
        stop_browser()


def run_registration_cli(count):
    controller = CliStopController()
    prev_handler = _install_cli_sigint_handler(controller)
    accounts_output_file = os.path.join(
        os.path.dirname(__file__),
        f"accounts_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
    )
    worker_count = max(1, int(config.get("concurrent_count", 1) or 1))
    stats = {"success": 0, "fail": 0, "lock": threading.Lock()}
    stop_speed = threading.Event()
    interval = float(config.get("speed_log_interval_sec", 60) or 60)

    def _cli_counts():
        with stats["lock"]:
            return stats["success"], stats["fail"]

    speed_thread, _meter = start_speed_logger(
        get_counts=_cli_counts,
        log_callback=cli_log,
        stop_event=stop_speed,
        interval_sec=interval,
    )
    cli_log(f"[*] 终端模式启动，目标数量: {count}，并发: {worker_count}")
    cli_log(f"[*] 成功账号将实时保存到: {accounts_output_file}")
    cli_log(f"[*] 日志级别: {get_log_level()} | 速度统计间隔: {int(interval)}s")
    cli_log("[*] 按 Ctrl+C 停止（连按两次强制退出）")
    try:
        if worker_count > 1:
            import queue
            task_queue = queue.Queue()
            for idx in range(count):
                task_queue.put(idx)
            threads = []
            for wid in range(worker_count):
                if controller.should_stop():
                    break
                t = threading.Thread(
                    target=_cli_worker_loop,
                    args=(wid, task_queue, count, controller, accounts_output_file, stats),
                    daemon=True,
                )
                t.start()
                threads.append(t)
                # 可中断的启动间隔
                sleep_with_cancel(2, controller.should_stop)
            _join_threads_interruptible(
                threads,
                should_stop=controller.should_stop,
                timeout=None,
                poll=0.5,
            )
            if controller.should_stop():
                cli_log("[!] 已请求停止，等待 worker 收尾...")
                _join_threads_interruptible(
                    threads,
                    should_stop=None,
                    timeout=5,
                    poll=0.5,
                )
        else:
            start_browser(log_callback=cli_log)
            cli_log("[*] 浏览器已启动")
            restart_every = int(config.get("browser_restart_every", 10) or 0)
            i = 0
            retry_count_for_slot = 0
            max_slot_retry = 3
            while i < count:
                if controller.should_stop():
                    break
                cli_log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
                try:
                    _register_one_account_cli(cli_log, controller.should_stop, accounts_output_file)
                    with stats["lock"]:
                        stats["success"] += 1
                    retry_count_for_slot = 0
                    i += 1
                    cli_log(f"[*] 当前统计: 成功 {stats['success']} | 失败 {stats['fail']}")
                    if restart_every > 0 and i > 0 and i % restart_every == 0:
                        cli_log(f"[*] 已注册 {i} 个账号，重启浏览器")
                        restart_browser(log_callback=cli_log)
                    if (
                        stats["success"] > 0
                        and stats["success"] % MEMORY_CLEANUP_INTERVAL == 0
                        and i < count
                    ):
                        cleanup_runtime_memory(
                            log_callback=cli_log,
                            reason=f"已成功 {stats['success']} 个账号，执行定期清理",
                        )
                except RegistrationCancelled:
                    cli_log("[!] 注册被停止")
                    break
                except AccountRetryNeeded as exc:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= max_slot_retry:
                        cli_log(
                            f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                        )
                    else:
                        with stats["lock"]:
                            stats["fail"] += 1
                        retry_count_for_slot = 0
                        i += 1
                        cli_log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
                except Exception as exc:
                    with stats["lock"]:
                        stats["fail"] += 1
                    retry_count_for_slot = 0
                    i += 1
                    cli_log(f"[-] 注册失败: {exc}")
                finally:
                    if controller.should_stop():
                        break
                    if _get_browser() is None:
                        start_browser(log_callback=cli_log)
                    else:
                        restart_browser(log_callback=cli_log)
                    sleep_with_cancel(1, controller.should_stop)
    except KeyboardInterrupt:
        controller.stop()
        cli_log("[!] 收到 KeyboardInterrupt，正在停止并清理")
    except Exception as exc:
        cli_log(f"[!] 任务异常: {exc}")
    finally:
        stop_speed.set()
        try:
            speed_thread.join(timeout=2)
        except Exception:
            pass
        stopping = controller.should_stop()
        controller.stop()
        _wait_cpa_async_threads(
            timeout=5 if stopping else 300,
            log_callback=cli_log,
            skip_if_stopping=(lambda: stopping),
        )
        try:
            cleanup_runtime_memory(log_callback=cli_log, reason="任务结束")
        except Exception as clean_exc:
            cli_log(f"[Debug] 结束清理异常: {clean_exc}")
        _restore_sigint_handler(prev_handler)
        with stats["lock"]:
            ok, bad = stats["success"], stats["fail"]
        total = max(ok + bad, 1)
        rate = 100.0 * ok / total
        cli_log(
            f"[*] 任务结束。成功 {ok} | 失败 {bad} | 成功率 {rate:.0f}% | 目标 {count}"
        )
        try:
            import domain_health as _dh

            cli_log(_dh.format_summary_line(config))
        except Exception:
            pass
        # fail-class histogram from domain_health last_reasons (best-effort)
        try:
            snap = __import__("domain_health").snapshot(config)
            reasons = {}
            for ent in (snap.get("domains") or {}).values():
                r = str(ent.get("last_reason") or "").strip()
                if r:
                    reasons[r] = reasons.get(r, 0) + 1
            if reasons:
                top = ", ".join(f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:6])
                cli_log(f"[*] 失败原因(末次/域名): {top}")
        except Exception:
            pass


def run_registration_cli_loop(count_per_round):
    """Continuous registration until Ctrl+C or max rounds."""
    # Anti-detection: randomize interval so registration doesn't look mechanical.
    # Default: base ±50% (e.g. 60s base -> 30-90s, configurable via auto_loop_jitter).
    round_i = 0
    controller = CliStopController()
    prev_handler = _install_cli_sigint_handler(controller)
    try:
        while not controller.should_stop():
            # Reload config each round so speed/concurrency tweaks apply without restart.
            load_config()
            if not check_daily_success_cap(cli_log):
                break
            pause_base = float(config.get("auto_loop_pause_sec", 30) or 30)
            max_rounds = int(config.get("auto_loop_max_rounds", 0) or 0)
            jitter = float(config.get("auto_loop_jitter", 0.5) or 0.5)
            count_this = int(config.get("register_count", count_per_round) or count_per_round)
            round_i += 1
            if max_rounds > 0 and round_i > max_rounds:
                cli_log(f"[*] 已达 max_rounds={max_rounds}，结束自动循环")
                break
            cli_log(
                f"[*] ===== 自动循环第 {round_i} 轮 | 本轮目标 {count_this} "
                f"| 并发 {int(config.get('concurrent_count', 1) or 1)} ====="
            )
            run_registration_cli(count_this)
            if controller.should_stop():
                break
            import random as _rnd
            pause = pause_base * (1.0 + _rnd.uniform(-jitter, jitter))
            pause = max(10.0, pause)
            cli_log(
                f"[*] 本轮结束，{pause:.0f}s 后开始下一轮（Ctrl+C 停止）"
            )
            sleep_with_cancel(pause, controller.should_stop)
    finally:
        _restore_sigint_handler(prev_handler)


def main_cli():
    load_config()
    count = int(config.get("register_count", 1) or 1)
    auto_start = False
    force_loop = False
    force_once = False
    if len(sys.argv) > 1:
        args = [a.strip().lower() for a in sys.argv[1:]]
        if "auto" in args or "--auto" in args or "loop" in args:
            auto_start = True
            force_loop = True
            config["auto_loop"] = True
        if "start" in args or "--start" in args:
            auto_start = True
            # `start` is always one-shot even if config.auto_loop is true
            if not force_loop:
                force_once = True
                config["auto_loop"] = False
    cli_log("[*] CLI 已加载配置")
    cli_log(
        f"[*] 邮箱: {config.get('email_provider', 'duckmail')} | 单轮数量: {count} "
        f"| auto_loop={bool(config.get('auto_loop'))} | remote_pool={bool(config.get('grok2api_auto_add_remote'))}"
    )
    if not auto_start:
        cli_log("[*] 输入 start 开始；auto 开启持续循环；Ctrl+C 停止")
        try:
            command = input("> ").strip().lower()
        except KeyboardInterrupt:
            cli_log("[!] 已取消")
            return
        if command in ("auto", "loop"):
            config["auto_loop"] = True
            force_loop = True
            auto_start = True
        elif command == "start":
            config["auto_loop"] = False
            force_once = True
            auto_start = True
        else:
            cli_log("[!] 未输入 start/auto，已退出")
            return
    # Prefer explicit CLI intent over stale config.auto_loop
    use_loop = bool(config.get("auto_loop")) and not force_once
    if force_loop:
        use_loop = True
    if use_loop:
        run_registration_cli_loop(count)
    else:
        run_registration_cli(count)


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() in (
        "start",
        "cli",
        "--cli",
        "auto",
        "--auto",
        "loop",
    ):
        main_cli()
        return
    root = tk.Tk()
    setup_light_theme(root)
    app = GrokRegisterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
