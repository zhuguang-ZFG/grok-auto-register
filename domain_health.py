#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-domain success/fail tracking and temporary demotion for mail LB.

Community free-mail domains can get polluted or rate-limited. This module
records registration outcomes and lets domain pickers skip demoted domains
for a cooldown window.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_STATE_PATH = ROOT / ".domain_health.json"
_lock = threading.Lock()

# Defaults (overridable via config dict keys of the same name)
DEFAULTS = {
    "domain_health_enabled": True,
    "domain_health_fail_streak_demote": 5,
    "domain_health_demote_sec": 3600,
    "domain_health_min_samples": 4,
    "domain_health_min_success_rate": 0.25,
}


def _now() -> float:
    return time.time()


def _state_path(cfg: dict[str, Any] | None = None) -> Path:
    raw = ""
    if cfg:
        raw = str(cfg.get("domain_health_path") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else ROOT / p
    return DEFAULT_STATE_PATH


def _cfg_val(cfg: dict[str, Any] | None, key: str) -> Any:
    if cfg and key in cfg and cfg[key] is not None:
        return cfg[key]
    return DEFAULTS.get(key)


def load_state(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    path = _state_path(cfg)
    if not path.is_file():
        return {"domains": {}, "updated_at": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"domains": {}, "updated_at": 0}
        domains = data.get("domains")
        if not isinstance(domains, dict):
            data["domains"] = {}
        return data
    except Exception:
        return {"domains": {}, "updated_at": 0}


def save_state(state: dict[str, Any], cfg: dict[str, Any] | None = None) -> None:
    path = _state_path(cfg)
    state = dict(state)
    state["updated_at"] = _now()
    text = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _domain_entry(state: dict[str, Any], domain: str) -> dict[str, Any]:
    domain = (domain or "").strip().lower()
    domains = state.setdefault("domains", {})
    ent = domains.get(domain)
    if not isinstance(ent, dict):
        ent = {
            "success": 0,
            "fail": 0,
            "mail_ok": 0,
            "mail_fail": 0,
            "fail_streak": 0,
            "demoted_until": 0.0,
            "last_success_at": 0.0,
            "last_fail_at": 0.0,
            "last_reason": "",
        }
        domains[domain] = ent
    return ent


def domain_from_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[1].strip()


def is_demoted(domain: str, cfg: dict[str, Any] | None = None, *, now: float | None = None) -> bool:
    if not domain:
        return False
    if not bool(_cfg_val(cfg, "domain_health_enabled")):
        return False
    now = _now() if now is None else now
    with _lock:
        state = load_state(cfg)
        ent = _domain_entry(state, domain)
        until = float(ent.get("demoted_until") or 0)
        return until > now


def filter_active_domains(
    domains: list[str],
    cfg: dict[str, Any] | None = None,
) -> list[str]:
    """Return domains not currently demoted. If all demoted, return original list."""
    if not domains:
        return []
    if not bool(_cfg_val(cfg, "domain_health_enabled")):
        return list(domains)
    now = _now()
    active = [d for d in domains if not is_demoted(d, cfg, now=now)]
    return active if active else list(domains)


def _maybe_demote(ent: dict[str, Any], cfg: dict[str, Any] | None) -> bool:
    streak_n = int(_cfg_val(cfg, "domain_health_fail_streak_demote") or 5)
    min_samples = int(_cfg_val(cfg, "domain_health_min_samples") or 4)
    min_rate = float(_cfg_val(cfg, "domain_health_min_success_rate") or 0.25)
    demote_sec = float(_cfg_val(cfg, "domain_health_demote_sec") or 3600)
    success = int(ent.get("success") or 0)
    fail = int(ent.get("fail") or 0)
    mail_fail = int(ent.get("mail_fail") or 0)
    total = success + fail
    demote = False
    if int(ent.get("fail_streak") or 0) >= max(1, streak_n):
        demote = True
    elif total >= max(1, min_samples) and (success / total) < min_rate:
        demote = True
    elif mail_fail >= max(1, streak_n) and success == 0:
        demote = True
    if demote:
        ent["demoted_until"] = _now() + max(60.0, demote_sec)
        return True
    return False


def record_event(
    domain: str,
    *,
    kind: str,
    reason: str = "",
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one outcome. kind: success | fail | mail_ok | mail_fail."""
    domain = (domain or "").strip().lower()
    if not domain:
        return {}
    if not bool(_cfg_val(cfg, "domain_health_enabled")):
        return {}
    with _lock:
        state = load_state(cfg)
        ent = _domain_entry(state, domain)
        now = _now()
        kind = (kind or "").strip().lower()
        if kind == "success":
            ent["success"] = int(ent.get("success") or 0) + 1
            ent["fail_streak"] = 0
            ent["last_success_at"] = now
            # success clears demotion early
            ent["demoted_until"] = 0.0
        elif kind == "fail":
            ent["fail"] = int(ent.get("fail") or 0) + 1
            ent["fail_streak"] = int(ent.get("fail_streak") or 0) + 1
            ent["last_fail_at"] = now
            ent["last_reason"] = (reason or "")[:200]
            _maybe_demote(ent, cfg)
        elif kind == "mail_ok":
            ent["mail_ok"] = int(ent.get("mail_ok") or 0) + 1
        elif kind == "mail_fail":
            ent["mail_fail"] = int(ent.get("mail_fail") or 0) + 1
            ent["last_fail_at"] = now
            ent["last_reason"] = (reason or "mail_fail")[:200]
            # mail-only failures also contribute to streak lightly
            ent["fail_streak"] = int(ent.get("fail_streak") or 0) + 1
            _maybe_demote(ent, cfg)
        else:
            return ent
        save_state(state, cfg)
        return dict(ent)


def record_success(email_or_domain: str, cfg: dict[str, Any] | None = None) -> None:
    d = domain_from_email(email_or_domain) if "@" in (email_or_domain or "") else email_or_domain
    record_event(d, kind="success", cfg=cfg)


def record_fail(
    email_or_domain: str,
    reason: str = "",
    cfg: dict[str, Any] | None = None,
) -> None:
    d = domain_from_email(email_or_domain) if "@" in (email_or_domain or "") else email_or_domain
    record_event(d, kind="fail", reason=reason, cfg=cfg)


def classify_fail_reason(exc: BaseException | str) -> str:
    text = str(exc or "").lower()
    rules = [
        ("turnstile", ("turnstile", "cf-turnstile", "人机")),
        ("mail_code", ("验证码", "验证码失败", "code", "邮件", "mail", "inbox")),
        ("email_input", ("邮箱输入", "注册按钮", "未找到邮箱", "email input")),
        ("cloudflare", ("cloudflare", "cf 防护", "http 403")),
        ("oauth_mint", ("cpa", "oauth", "device", "mint", "authorization")),
        ("browser", ("浏览器", "browser", "chromium", "page disconnected")),
        ("proxy", ("proxy", "clash", "连接", "timeout", "timed out")),
        ("nsfw", ("nsfw",)),
    ]
    for name, keys in rules:
        if any(k in text for k in keys):
            return name
    return "other"


def snapshot(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact stats for batch summary / pool_status."""
    with _lock:
        state = load_state(cfg)
    now = _now()
    out_domains = {}
    for name, ent in (state.get("domains") or {}).items():
        if not isinstance(ent, dict):
            continue
        success = int(ent.get("success") or 0)
        fail = int(ent.get("fail") or 0)
        total = success + fail
        rate = (success / total) if total else None
        until = float(ent.get("demoted_until") or 0)
        out_domains[name] = {
            "success": success,
            "fail": fail,
            "mail_ok": int(ent.get("mail_ok") or 0),
            "mail_fail": int(ent.get("mail_fail") or 0),
            "fail_streak": int(ent.get("fail_streak") or 0),
            "success_rate": None if rate is None else round(rate, 3),
            "demoted": until > now,
            "demoted_remain_sec": int(max(0, until - now)),
            "last_reason": ent.get("last_reason") or "",
        }
    return {"domains": out_domains, "updated_at": state.get("updated_at") or 0}


def format_summary_line(cfg: dict[str, Any] | None = None) -> str:
    snap = snapshot(cfg)
    parts = []
    demoted = []
    for name, ent in sorted((snap.get("domains") or {}).items()):
        parts.append(
            f"{name}:ok={ent['success']}/fail={ent['fail']}"
            + (f"(rate={ent['success_rate']})" if ent.get("success_rate") is not None else "")
        )
        if ent.get("demoted"):
            demoted.append(f"{name}~{ent['demoted_remain_sec']}s")
    body = ", ".join(parts) if parts else "(no domain stats yet)"
    extra = f" | demoted: {', '.join(demoted)}" if demoted else ""
    return f"[*] 域名健康: {body}{extra}"
