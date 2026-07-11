"""Watch local Grok CLI quota / auth failures and auto-refill credentials.

Flow:
  detect exhausted (log keywords and/or CPA probe)
    -> prefer rotate unused file from cpa_auths/ into ~/.grok/auth.json
    -> else run one registration round (CPA + local_grok_auth_auto)
    -> cooldown

This targets the **official** auth.json / cli-chat-proxy path only.
Third-party NewAPI noise (new_api_error, free-az 503, etc.) is ignored by default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


LogFn = Callable[[str], None]

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"
AUTH_ENTRY_KEY = "https://accounts.x.ai/sign-in"

DEFAULT_QUOTA_PATTERNS = [
    r"\b429\b",
    r"rate[\s_-]?limit",
    r"quota[\s_-]?(exceeded|exhausted|limit)?",
    r"usage[\s_-]?limit",
    r"resource_exhausted",
    r"too many requests",
    r"limit exceeded",
    r"you've reached",
    r"you have reached",
    r"monthly limit",
    r"free tier",
    r"insufficient.?quota",
    r"auth 401 attribution",
    r"sampler 401",
    r"unauthorized",
    r"token.?expired",
    r"invalid.?token",
    r"refresh.?failed",
    r"agent response failed",
]

# Drop lines that look like third-party NewAPI / community mid-layer noise.
DEFAULT_EXCLUDE_PATTERNS = [
    r"new_api_error",
    r"system cpu ove",
    r"free-az",
    r"rainflow",
    r"voya\.eu",
    r"muapi",
    r"gptper",
    r"20\.196\.139\.201",
    r"cpu overload",
]

DEFAULT_CFG: dict[str, Any] = {
    "quota_watch_enabled": True,
    "quota_watch_poll_sec": 20,
    "quota_watch_cooldown_sec": 1800,
    "quota_watch_rotate_cooldown_sec": 30,
    "quota_watch_max_triggers_per_day": 20,
    "quota_watch_log_path": "",
    "quota_watch_state_path": "",
    "quota_watch_patterns": DEFAULT_QUOTA_PATTERNS,
    "quota_watch_exclude_patterns": DEFAULT_EXCLUDE_PATTERNS,
    "quota_watch_min_hits": 2,
    "quota_watch_hit_window_sec": 120,
    "quota_watch_probe_enabled": True,
    "quota_watch_probe_interval_sec": 300,
    "quota_watch_probe_on_start": True,
    "quota_watch_probe_kind": "models",
    "quota_watch_prefer_pool": True,
    "quota_watch_register_on_miss": True,
    "quota_watch_min_pool": 15,
    "quota_watch_target_pool": 50,
    "quota_watch_pool_topup_cooldown_sec": 1200,
    "quota_watch_pool_topup_max_per_day": 40,
    "quota_watch_refresh_enabled": True,
    "quota_watch_refresh_interval_sec": 600,
    "quota_watch_refresh_margin_sec": 1800,
    "quota_watch_register_timeout_sec": 900,
    "quota_watch_python": "",
    "quota_watch_register_args": ["start"],
    "cpa_auth_dir": "cpa_auths",
    "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
    "cpa_proxy": "",
    "local_grok_auth_path": "",
    "preferred_model": "grok-4.5",
}


def _log(log: LogFn | None, msg: str) -> None:
    if log:
        log(msg)
    else:
        print(msg, flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def default_auth_path() -> Path:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".grok" / "auth.json"


def default_log_path() -> Path:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".grok" / "logs" / "unified.jsonl"


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def merge_config(path: Path | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CFG)
    cfg_path = path or DEFAULT_CONFIG
    loaded = load_json(cfg_path)
    if loaded:
        cfg.update(loaded)
    return cfg


def resolve_path(raw: str | Path | None, fallback: Path) -> Path:
    if raw is None or str(raw).strip() == "":
        return fallback
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p


def compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    out: list[re.Pattern[str]] = []
    for p in patterns or []:
        try:
            out.append(re.compile(p, re.I))
        except re.error:
            continue
    return out


def line_matches(
    text: str,
    include: list[re.Pattern[str]],
    exclude: list[re.Pattern[str]],
) -> bool:
    if not text:
        return False
    for ex in exclude:
        if ex.search(text):
            return False
    return any(inc.search(text) for inc in include)


def flatten_log_line(raw: str) -> str:
    """Turn jsonl log objects into a searchable blob (msg + ctx + reason)."""
    raw = raw.strip()
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(obj, dict):
        return raw
    parts: list[str] = []
    for k in ("msg", "message", "error", "reason", "lvl"):
        v = obj.get(k)
        if v is not None:
            parts.append(str(v))
    ctx = obj.get("ctx")
    if isinstance(ctx, dict):
        for k in ("reason", "error", "status", "message", "detail"):
            if ctx.get(k) is not None:
                parts.append(str(ctx.get(k)))
        # include whole ctx string for keyword hits
        try:
            parts.append(json.dumps(ctx, ensure_ascii=False))
        except Exception:
            parts.append(str(ctx))
    elif ctx is not None:
        parts.append(str(ctx))
    return " | ".join(parts)


def read_auth_entry(auth_path: Path) -> dict[str, Any]:
    data = load_json(auth_path)
    entry = data.get(AUTH_ENTRY_KEY)
    return entry if isinstance(entry, dict) else {}


def _parse_rfc3339_to_epoch(s: str) -> float:
    """Parse RFC3339 like '2026-07-11T18:47:54Z' to epoch seconds. 0 on failure."""
    if not s:
        return 0.0
    s = str(s).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def current_auth_email(auth_path: Path) -> str:
    entry = read_auth_entry(auth_path)
    return str(entry.get("email") or "").strip()


def current_access_token(auth_path: Path) -> str:
    entry = read_auth_entry(auth_path)
    return str(entry.get("key") or entry.get("access_token") or "").strip()


@dataclass
class WatchState:
    path: Path
    log_offset: int = 0
    last_trigger_at: float = 0.0
    last_trigger_reason: str = ""
    last_probe_at: float = 0.0
    last_probe_ok: bool | None = None
    triggers_today: int = 0
    triggers_day: str = ""
    used_cpa_files: list[str] = field(default_factory=list)
    last_email: str = ""
    last_action: str = ""
    last_error: str = ""
    last_pool_topup_at: float = 0.0
    last_refresh_at: float = 0.0

    @classmethod
    def load(cls, path: Path) -> "WatchState":
        raw = load_json(path)
        st = cls(path=path)
        st.log_offset = int(raw.get("log_offset") or 0)
        st.last_trigger_at = float(raw.get("last_trigger_at") or 0)
        st.last_trigger_reason = str(raw.get("last_trigger_reason") or "")
        st.last_probe_at = float(raw.get("last_probe_at") or 0)
        ok = raw.get("last_probe_ok")
        st.last_probe_ok = None if ok is None else bool(ok)
        st.triggers_today = int(raw.get("triggers_today") or 0)
        st.triggers_day = str(raw.get("triggers_day") or "")
        used = raw.get("used_cpa_files") or []
        st.used_cpa_files = [str(x) for x in used] if isinstance(used, list) else []
        st.last_email = str(raw.get("last_email") or "")
        st.last_action = str(raw.get("last_action") or "")
        st.last_error = str(raw.get("last_error") or "")
        st.last_pool_topup_at = float(raw.get("last_pool_topup_at") or 0)
        st.last_refresh_at = float(raw.get("last_refresh_at") or 0)
        return st

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {
                "log_offset": self.log_offset,
                "last_trigger_at": self.last_trigger_at,
                "last_trigger_reason": self.last_trigger_reason,
                "last_probe_at": self.last_probe_at,
                "last_probe_ok": self.last_probe_ok,
                "triggers_today": self.triggers_today,
                "triggers_day": self.triggers_day,
                "used_cpa_files": self.used_cpa_files[-200:],
                "last_email": self.last_email,
                "last_action": self.last_action,
                "last_error": self.last_error,
                "last_pool_topup_at": self.last_pool_topup_at,
                "last_refresh_at": self.last_refresh_at,
                "updated_at": _now_iso(),
            },
        )

    def roll_day(self) -> None:
        day = _utc_day()
        if self.triggers_day != day:
            self.triggers_day = day
            self.triggers_today = 0


def probe_token(cfg: dict[str, Any], access_token: str) -> dict[str, Any]:
    """Active health check against official cli-chat-proxy.

    By default probes ``/v1/models`` (token validity), which works for Free-tier
    accounts. The ``/v1/responses`` chat endpoint denies Free-tier accounts with
    HTTP 403 ``permission-denied`` regardless of quota, so it is a poor health
    signal — set ``quota_watch_probe_kind = "responses"`` to use it anyway.
    """
    if not access_token:
        return {"ok": False, "status": 0, "error": "empty token", "quota_like": True}
    try:
        from cpa_xai.probe import probe_mini_response, probe_models
    except Exception as exc:
        return {"ok": False, "status": 0, "error": f"import probe failed: {exc}", "quota_like": False}

    base = str(cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1")
    proxy = str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None
    kind = str(cfg.get("quota_watch_probe_kind") or "models").strip().lower()
    if kind == "responses":
        result = probe_mini_response(access_token, base_url=base, timeout=60.0, proxy=proxy)
    else:
        result = probe_models(access_token, base_url=base, timeout=30.0, proxy=proxy)
        # Normalize so downstream fields are consistent.
        if result.get("ok") and not result.get("has_grok_45") and result.get("model_ids"):
            # Token valid but catalog changed — still healthy, just note it.
            pass
    err = str(result.get("error") or "")
    status = int(result.get("status") or 0)
    blob = f"{status} {err}".lower()
    # 403 "permission-denied / Access to the chat endpoint is denied" is an
    # endpoint-access denial for Free-tier accounts on cli-chat-proxy's
    # /responses endpoint — NOT credential exhaustion. The same token works in
    # the real Grok CLI. Treat it as a soft-fail so we don't burn the pool.
    endpoint_denial = status == 403 and any(
        k in blob
        for k in (
            "permission-denied",
            "permission_denied",
            "access to the chat endpoint is denied",
            "access to the chat endpoint",
            "update the permissions",
        )
    )
    quota_like = False
    if status in (401, 429):
        quota_like = True
    if any(
        k in blob
        for k in (
            "quota",
            "rate limit",
            "rate_limit",
            "too many",
            "usage limit",
            "resource_exhausted",
            "unauthorized",
            "invalid_token",
            "expired",
            "limit exceeded",
        )
    ):
        quota_like = True
    if endpoint_denial:
        # Don't let a bare 403 endpoint denial masquerade as exhaustion.
        quota_like = False
    result["quota_like"] = quota_like and not result.get("ok")
    return result


def pool_token_is_expired(payload: dict[str, Any]) -> bool:
    """读 CPA 文件 access_token 的 JWT exp，过期返回 True。

    无 token / 解析失败返回 False（交给 probe 判断，不误杀）。
    """
    if not isinstance(payload, dict):
        return False
    token = str(payload.get("access_token") or "").strip()
    if not token:
        return False
    try:
        from cpa_xai.schema import jwt_payload
        claims = jwt_payload(token)
        exp = int(claims.get("exp") or 0)
        if exp <= 0:
            return False
        return time.time() >= exp
    except Exception:
        return False


def count_valid_pool(cfg: dict[str, Any], *, probe: bool = False) -> int:
    """数 cpa_auths/ 里未过期(JWT exp)的文件数。

    probe=True 时再调 probe_token 验证一遍（较慢，默认关）。
    """
    valid = 0
    for p in list_cpa_pool(cfg):
        payload = load_json(p)
        if pool_token_is_expired(payload):
            continue
        if probe:
            token = str(payload.get("access_token") or "").strip()
            if not token:
                continue
            r = probe_token(cfg, token)
            if not r.get("ok"):
                continue
        valid += 1
    return valid


def list_cpa_pool(cfg: dict[str, Any], *, drop_expired: bool = False) -> list[Path]:
    raw = str(cfg.get("cpa_auth_dir") or "cpa_auths")
    d = resolve_path(raw, ROOT / "cpa_auths")
    if not d.is_dir():
        return []
    files = sorted(d.glob("xai-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not drop_expired:
        return files
    out = []
    for p in files:
        if not pool_token_is_expired(load_json(p)):
            out.append(p)
    return out


def try_rotate_from_pool(
    cfg: dict[str, Any],
    state: WatchState,
    *,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Pick an unused CPA file, probe it, write auth.json if OK.

    The used-blacklist is reset each call — we only skip the CURRENT exhausted
    credential (the one in auth.json right now). Other pool files may have
    recovered quota since last check, so we re-evaluate them every time.
    """
    auth_path = resolve_path(cfg.get("local_grok_auth_path"), default_auth_path())
    current = current_auth_email(auth_path)
    # Fresh blacklist: only exclude the currently-exhausted credential
    used: set[str] = set()
    if current:
        # mark current email's file as used so we don't re-pick the same exhausted acct
        for p in list_cpa_pool(cfg):
            if current in p.name:
                used.add(str(p.resolve()))

    try:
        from local_grok_auth import write_from_cpa_file
    except Exception as exc:
        return {"ok": False, "error": f"import local_grok_auth failed: {exc}"}

    candidates = list_cpa_pool(cfg)
    if not candidates:
        return {"ok": False, "skipped": True, "reason": "empty_pool"}

    for path in candidates:
        key = str(path.resolve())
        if key in used:
            continue
        payload = load_json(path)
        email = str(payload.get("email") or "")
        if current and email and email == current:
            used.add(key)
            continue
        token = str(payload.get("access_token") or "").strip()
        if not token:
            continue
        # 跳过 JWT 已过期的死号（不加入 used，让它们自然被新文件挤掉）
        if pool_token_is_expired(payload):
            _log(log, f"[quota] pool skip expired: {path.name}")
            continue
        # 跳过 24h 额度尚未恢复的号（rolling window）
        try:
            from cpa_xai.usage import is_account_recovered, recover_in_sec
            if not is_account_recovered(path):
                remain_h = recover_in_sec(path) / 3600.0
                _log(log, f"[quota] pool skip exhausted (recovers in {remain_h:.1f}h): {path.name}")
                continue
        except Exception:
            pass  # usage module not available, skip check
        _log(log, f"[quota] pool candidate: {path.name} ({email or 'no-email'})")
        probe = probe_token(cfg, token)
        if not probe.get("ok"):
            _log(
                log,
                f"[quota] pool probe fail {path.name}: status={probe.get('status')} err={str(probe.get('error') or '')[:160]}",
            )
            # still mark as tried so we don't spin on dead tokens forever this day
            # (403 endpoint-denial is NOT counted — those tokens are still good)
            if probe.get("quota_like") or int(probe.get("status") or 0) in (401, 429):
                used.add(key)
            continue
        result = write_from_cpa_file(path, auth_path=auth_path, log=log)
        if result.get("ok"):
            used.add(key)
            state.used_cpa_files = list(used)
            state.last_email = email
            state.last_action = f"pool_rotate:{path.name}"
            state.last_error = ""
            # Clear any stale exhausted mark — this account is healthy now
            try:
                from cpa_xai.usage import clear_exhausted_mark
                clear_exhausted_mark(path)
            except Exception:
                pass
            return {
                "ok": True,
                "action": "pool_rotate",
                "path": str(path),
                "email": email,
                "auth_path": str(auth_path),
                "probe": probe,
            }
        return {"ok": False, "error": result.get("error") or "write failed", "path": str(path)}

    state.used_cpa_files = list(used)
    return {"ok": False, "skipped": True, "reason": "no_healthy_pool_entry"}


def run_one_registration(cfg: dict[str, Any], *, log: LogFn | None = None) -> dict[str, Any]:
    """Spawn one register round with local_grok_auth_auto forced on."""
    if not cfg.get("quota_watch_register_on_miss", True):
        return {"ok": False, "skipped": True, "reason": "register_disabled"}

    py = str(cfg.get("quota_watch_python") or "").strip() or sys.executable
    script = ROOT / "grok_register_ttk.py"
    if not script.is_file():
        return {"ok": False, "error": f"missing {script}"}

    args = list(cfg.get("quota_watch_register_args") or ["start"])
    # Prefer non-loop one-shot
    clean_args = []
    for a in args:
        al = str(a).lower()
        if al in ("auto", "--auto", "loop"):
            continue
        clean_args.append(str(a))
    if "start" not in [x.lower() for x in clean_args] and "--start" not in [x.lower() for x in clean_args]:
        clean_args = ["start"]

    env = os.environ.copy()
    # Child process reloads config.json; ensure local write is on there (caller should set).
    env["GROK_QUOTA_WATCH_TRIGGER"] = "1"
    cmd = [py, str(script), *clean_args]
    timeout = float(cfg.get("quota_watch_register_timeout_sec") or 900)
    _log(log, f"[quota] spawning register: {' '.join(cmd)} (timeout={timeout:.0f}s)")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": f"register timeout: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    out = (proc.stdout or "")[-4000:]
    err = (proc.stderr or "")[-2000:]
    if out:
        for line in out.splitlines()[-30:]:
            _log(log, f"[register] {line}")
    if err:
        for line in err.splitlines()[-15:]:
            _log(log, f"[register:err] {line}")

    auth_path = resolve_path(cfg.get("local_grok_auth_path"), default_auth_path())
    email = current_auth_email(auth_path)
    # Success heuristic: process exit 0 and auth has a key
    token = current_access_token(auth_path)
    ok = proc.returncode == 0 and bool(token)
    return {
        "ok": ok,
        "action": "register",
        "returncode": proc.returncode,
        "email": email,
        "auth_path": str(auth_path),
        "stdout_tail": out[-500:],
        "stderr_tail": err[-300:],
        "error": "" if ok else f"register failed rc={proc.returncode}",
    }


def ensure_local_auth_flag(cfg_path: Path) -> None:
    """Best-effort: turn on local_grok_auth_auto in config.json if missing/false."""
    data = load_json(cfg_path)
    if not data:
        return
    changed = False
    if not data.get("local_grok_auth_auto"):
        data["local_grok_auth_auto"] = True
        changed = True
    if not data.get("cpa_export_enabled", True):
        data["cpa_export_enabled"] = True
        changed = True
    # One account per refill is enough
    if int(data.get("register_count") or 1) != 1:
        data["register_count"] = 1
        changed = True
    if data.get("auto_loop"):
        # register start should not inherit loop from file if user left it on
        # do not force-write false permanently if user wants loop for manual auto;
        # only ensure register_count=1. Child `start` does not enable auto_loop.
        pass
    if changed:
        atomic_write_json(cfg_path, data)


def topup_pool(
    cfg: dict[str, Any],
    state: WatchState,
    *,
    log: LogFn | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Register one account to replenish the CPA pool WITHOUT touching auth.json.

    This keeps the in-use grok CLI credential untouched. The new account lands
    in cpa_auths/ + grok2api pools (local/remote if enabled). The current
    auth.json is snapshotted and restored after the register subprocess, so the
    grok CLI session keeps running on its existing valid credential.
    """
    min_pool = int(cfg.get("quota_watch_min_pool") or 3)
    target_pool = int(cfg.get("quota_watch_target_pool") or max(min_pool, 20))
    # 用「有效数」(未过期) 而非文件总数判断水位
    pool = list_cpa_pool(cfg)
    valid_n = sum(1 for p in pool if not pool_token_is_expired(load_json(p)))
    if valid_n >= target_pool:
        return {"ok": True, "skipped": True, "reason": f"pool_at_target(valid={valid_n}>={target_pool})"}
    if valid_n >= min_pool:
        # Above floor but below target — top up slowly toward target.
        # The cooldown (topup_cooldown_sec) gates how fast we approach target.
        _log(log, f"[quota] pool topping toward target: valid={valid_n} (min={min_pool}, target={target_pool})")
    elif pool:
        expired_n = len(pool) - valid_n
        _log(log, f"[quota] pool low: valid={valid_n}<{min_pool} (total={len(pool)}, expired={expired_n}) — topping up")
    else:
        _log(log, f"[quota] pool low: valid={valid_n}<{min_pool} (empty) — topping up")

    state.roll_day()
    max_day = int(cfg.get("quota_watch_pool_topup_max_per_day") or 30)
    if state.triggers_today >= max_day:
        return {"ok": False, "skipped": True, "reason": f"daily topup cap ({max_day})"}

    cooldown = float(cfg.get("quota_watch_pool_topup_cooldown_sec") or 600)
    now = time.time()
    if state.last_pool_topup_at and (now - state.last_pool_topup_at) < cooldown:
        remain = int(cooldown - (now - state.last_pool_topup_at))
        return {"ok": False, "skipped": True, "reason": f"topup cooldown {remain}s"}

    _log(log, f"[quota] pool low ({len(pool)}<{min_pool}) — topping up")

    if dry_run:
        return {"ok": True, "dry_run": True, "reason": "pool_topup"}

    # Snapshot the current auth.json so we can restore it after register.
    auth_path = resolve_path(cfg.get("local_grok_auth_path"), default_auth_path())
    auth_snapshot = None
    if auth_path.is_file():
        try:
            auth_snapshot = auth_path.read_bytes()
        except Exception:
            auth_snapshot = None

    # Temporarily disable local_grok_auth_auto in config.json so the child
    # register process does NOT overwrite auth.json. CPA export + grok2api pool
    # writes still happen normally.
    cfg_path = DEFAULT_CONFIG
    cfg_backup = load_json(cfg_path)
    cfg_patched = dict(cfg_backup)
    cfg_patched["local_grok_auth_auto"] = False
    atomic_write_json(cfg_path, cfg_patched)

    try:
        result = run_one_registration(cfg_patched, log=log)
    finally:
        # Restore config.json + auth.json regardless of register outcome.
        atomic_write_json(cfg_path, cfg_backup)
        if auth_snapshot is not None:
            try:
                auth_path.write_bytes(auth_snapshot)
            except Exception:
                pass

    state.last_pool_topup_at = now
    if result.get("ok"):
        after_valid = sum(1 for p in list_cpa_pool(cfg) if not pool_token_is_expired(load_json(p)))
        _log(log, f"[quota] pool topped up: valid {valid_n} -> {after_valid} ({result.get('email')})")
    else:
        _log(log, f"[quota] pool topup register failed: {result.get('error')}")
    state.save()
    return result


def refill(
    cfg: dict[str, Any],
    state: WatchState,
    *,
    reason: str,
    log: LogFn | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    state.roll_day()
    max_day = int(cfg.get("quota_watch_max_triggers_per_day") or 20)
    if state.triggers_today >= max_day:
        msg = f"daily trigger cap reached ({max_day})"
        _log(log, f"[quota] skip refill: {msg}")
        return {"ok": False, "skipped": True, "reason": msg}

    rotate_cooldown = float(cfg.get("quota_watch_rotate_cooldown_sec") or 30)
    now = time.time()
    # Pool rotation has a much shorter cooldown — swapping tokens is cheap and
    # Free-tier quota exhausts fast, so we must be able to rotate again within
    # minutes. Registration keeps the full cooldown (prevent signup spam).
    if state.last_trigger_at and (now - state.last_trigger_at) < rotate_cooldown:
        remain = int(rotate_cooldown - (now - state.last_trigger_at))
        msg = f"rotate cooldown {remain}s remaining"
        _log(log, f"[quota] skip refill: {msg}")
        return {"ok": False, "skipped": True, "reason": msg}

    _log(log, f"[quota] refill triggered: {reason}")
    if dry_run:
        return {"ok": True, "dry_run": True, "reason": reason}

    result: dict[str, Any] = {"ok": False, "reason": reason}
    if cfg.get("quota_watch_prefer_pool", True):
        result = try_rotate_from_pool(cfg, state, log=log)
        if result.get("ok"):
            state.last_trigger_at = now
            state.last_trigger_reason = reason
            state.triggers_today += 1
            state.last_action = result.get("action") or "pool_rotate"
            state.save()
            _log(log, f"[quota] OK pool rotate -> {result.get('email')}")
            return result
        _log(log, f"[quota] pool miss: {result.get('reason') or result.get('error')}")

    # Registration has its own (long) cooldown to prevent x.ai signup spam.
    reg_cooldown = float(cfg.get("quota_watch_cooldown_sec") or 1800)
    if state.last_trigger_at and (now - state.last_trigger_at) < reg_cooldown:
        remain = int(reg_cooldown - (now - state.last_trigger_at))
        _log(log, f"[quota] registration skipped: cooldown {remain}s (pool also empty)")
        return {"ok": False, "skipped": True, "reason": f"pool_empty + reg cooldown {remain}s"}

    ensure_local_auth_flag(DEFAULT_CONFIG)
    result = run_one_registration(cfg, log=log)
    state.last_trigger_at = now
    state.last_trigger_reason = reason
    state.triggers_today += 1
    state.last_action = result.get("action") or "register"
    state.last_email = str(result.get("email") or "")
    state.last_error = str(result.get("error") or "")
    state.save()
    if result.get("ok"):
        _log(log, f"[quota] OK register -> {result.get('email')}")
    else:
        _log(log, f"[quota] register failed: {result.get('error')}")
    return result


@dataclass
class HitWindow:
    times: list[float] = field(default_factory=list)

    def add(self, ts: float, window: float, min_hits: int) -> bool:
        self.times.append(ts)
        cutoff = ts - window
        self.times = [t for t in self.times if t >= cutoff]
        return len(self.times) >= max(1, min_hits)


def scan_log_new_lines(
    log_path: Path,
    state: WatchState,
    include: list[re.Pattern[str]],
    exclude: list[re.Pattern[str]],
    *,
    max_bytes: int = 2_000_000,
) -> list[str]:
    """Return matching hit snippets; advance state.log_offset."""
    if not log_path.is_file():
        return []
    size = log_path.stat().st_size
    offset = state.log_offset
    if offset > size:
        # rotated / truncated
        offset = 0
    # first run: start at end (do not replay whole history)
    if offset == 0 and size > 0 and state.last_trigger_at == 0 and state.last_probe_at == 0:
        # If state file is brand new, skip backlog unless empty file
        if not state.path.is_file() or state.log_offset == 0:
            # distinguish first-ever: if triggers_day empty and no last_action
            if not state.last_action and state.triggers_today == 0 and not state.used_cpa_files:
                state.log_offset = size
                state.save()
                return []

    hits: list[str] = []
    with open(log_path, "rb") as f:
        f.seek(offset)
        chunk = f.read(max_bytes)
        new_offset = f.tell()
    # if partial last line, keep it for next time by rewinding to last newline
    if chunk and not chunk.endswith(b"\n"):
        last_nl = chunk.rfind(b"\n")
        if last_nl >= 0:
            chunk = chunk[: last_nl + 1]
            new_offset = offset + last_nl + 1
        else:
            # wait for more data
            return []

    text = chunk.decode("utf-8", errors="replace")
    for line in text.splitlines():
        flat = flatten_log_line(line)
        if line_matches(flat, include, exclude):
            hits.append(flat[:300])
    state.log_offset = new_offset
    return hits


def once(
    cfg: dict[str, Any] | None = None,
    *,
    log: LogFn | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Single watch iteration. Returns status dict."""
    cfg = cfg or merge_config()
    if not cfg.get("quota_watch_enabled", True) and not force:
        return {"ok": False, "skipped": True, "reason": "disabled"}

    log_path = resolve_path(cfg.get("quota_watch_log_path"), default_log_path())
    state_path = resolve_path(
        cfg.get("quota_watch_state_path"),
        ROOT / ".quota_watch_state.json",
    )
    auth_path = resolve_path(cfg.get("local_grok_auth_path"), default_auth_path())
    state = WatchState.load(state_path)
    state.roll_day()

    include = compile_patterns(list(cfg.get("quota_watch_patterns") or DEFAULT_QUOTA_PATTERNS))
    exclude = compile_patterns(
        list(cfg.get("quota_watch_exclude_patterns") or DEFAULT_EXCLUDE_PATTERNS)
    )
    min_hits = int(cfg.get("quota_watch_min_hits") or 2)
    window = float(cfg.get("quota_watch_hit_window_sec") or 120)

    report: dict[str, Any] = {
        "ok": True,
        "action": "none",
        "auth_email": current_auth_email(auth_path),
        "log_path": str(log_path),
        "state_path": str(state_path),
    }

    # --- log scan ---
    hits = scan_log_new_lines(log_path, state, include, exclude)
    if hits:
        hw = HitWindow()
        # synthetic: treat all new hits in this poll as clustered at now
        now = time.time()
        for _ in hits:
            if hw.add(now, window, min_hits):
                break
        report["log_hits"] = len(hits)
        report["log_samples"] = hits[:5]
        if len(hits) >= min_hits or force:
            # Mark the current credential as exhausted (24h rolling window)
            current = current_auth_email(auth_path)
            if current and not dry_run:
                for p in list_cpa_pool(cfg):
                    if current in p.name:
                        try:
                            from cpa_xai.usage import mark_account_exhausted
                            # Try to extract tokens_used from the 429 message
                            tu = None
                            for h in hits:
                                if "tokens" in h and "/" in h:
                                    try:
                                        import re
                                        m = re.search(r"(\d+)/\d+", h)
                                        if m:
                                            tu = int(m.group(1))
                                    except Exception:
                                        pass
                            mark_account_exhausted(p, tokens_used=tu, log=log)
                        except Exception:
                            pass
                        break
            result = refill(
                cfg,
                state,
                reason=f"log_hits={len(hits)} sample={hits[0][:120]}",
                log=log,
                dry_run=dry_run,
            )
            report["action"] = result.get("action") or ("dry_run" if dry_run else "refill")
            report["refill"] = result
            state.save()
            return report
        state.save()

    # --- active probe ---
    if cfg.get("quota_watch_probe_enabled", True):
        interval = float(cfg.get("quota_watch_probe_interval_sec") or 300)
        now = time.time()
        should_probe = force or (now - state.last_probe_at) >= interval
        if state.last_probe_at == 0 and cfg.get("quota_watch_probe_on_start", True):
            should_probe = True
        if should_probe:
            token = current_access_token(auth_path)
            probe = probe_token(cfg, token)
            state.last_probe_at = now
            state.last_probe_ok = bool(probe.get("ok"))
            report["probe"] = {
                "ok": probe.get("ok"),
                "status": probe.get("status"),
                "quota_like": probe.get("quota_like"),
                "error": str(probe.get("error") or "")[:200],
                "text": str(probe.get("text") or "")[:80],
            }
            state.save()
            if probe.get("quota_like") or (
                not probe.get("ok") and int(probe.get("status") or 0) in (401, 429)
            ):
                # Mark current credential as exhausted before rotating
                current = current_auth_email(auth_path)
                if current and not dry_run:
                    for p in list_cpa_pool(cfg):
                        if current in p.name:
                            try:
                                from cpa_xai.usage import mark_account_exhausted
                                mark_account_exhausted(p, log=log)
                            except Exception:
                                pass
                            break
                result = refill(
                    cfg,
                    state,
                    reason=f"probe status={probe.get('status')} err={str(probe.get('error') or '')[:100]}",
                    log=log,
                    dry_run=dry_run,
                )
                report["action"] = result.get("action") or ("dry_run" if dry_run else "refill")
                report["refill"] = result
                return report
            if not probe.get("ok"):
                # soft failure (network, or 403 endpoint-denial) — do not refill
                if int(probe.get("status") or 0) == 403:
                    _log(
                        log,
                        "[quota] probe 403 endpoint-denial (not exhaustion) — keep current credential",
                    )
                else:
                    _log(
                        log,
                        f"[quota] probe soft-fail status={probe.get('status')} err={str(probe.get('error') or '')[:160]}",
                    )

    # --- proactive refresh: renew auth.json before token expires ---
    if cfg.get("quota_watch_refresh_enabled", True) and not dry_run:
        interval = float(cfg.get("quota_watch_refresh_interval_sec") or 600)
        margin = float(cfg.get("quota_watch_refresh_margin_sec") or 1800)
        now = time.time()
        if force or (now - state.last_refresh_at) >= interval:
            entry = read_auth_entry(auth_path)
            exp_str = str(entry.get("expires") or entry.get("expired") or "")
            exp_epoch = _parse_rfc3339_to_epoch(exp_str)
            should_refresh = False
            if exp_epoch > 0 and (now + margin) >= exp_epoch:
                should_refresh = True
            elif not exp_str:
                # no expiry info — try once per interval anyway (best-effort)
                should_refresh = True
            # Mark checked regardless, so we don't re-parse JWT every poll cycle
            state.last_refresh_at = now
            if should_refresh:
                try:
                    from local_grok_auth import refresh_auth_entry
                    r = refresh_auth_entry(
                        auth_path, log=log, proxy=cfg.get("cpa_proxy")
                    )
                    state.last_refresh_at = now
                    if r.get("ok"):
                        _log(log, f"[quota] auth.json refreshed proactively ({r.get('email')})")
                    else:
                        _log(log, f"[quota] proactive refresh failed: {r.get('reason') or r.get('error')} — will rotate on next exhaust")
                except Exception as exc:
                    state.last_refresh_at = now
                    _log(log, f"[quota] proactive refresh error: {exc}")

    # --- pool water-level maintenance: top up cpa_auths/ without touching auth.json ---
    min_pool = int(cfg.get("quota_watch_min_pool") or 0)
    if min_pool > 0 and not dry_run:
        pool_n = sum(1 for p in list_cpa_pool(cfg) if not pool_token_is_expired(load_json(p)))
        if pool_n < min_pool:
            topup = topup_pool(cfg, state, log=log)
            if topup.get("ok") and not topup.get("skipped"):
                report["pool_topup"] = {
                    "before_valid": pool_n,
                    "email": topup.get("email"),
                }

    state.save()
    return report


def run_loop(
    cfg: dict[str, Any] | None = None,
    *,
    log: LogFn | None = None,
    dry_run: bool = False,
    max_iters: int = 0,
) -> None:
    cfg = cfg or merge_config()
    poll = float(cfg.get("quota_watch_poll_sec") or 20)
    _log(log, f"[quota] watch started poll={poll}s dry_run={dry_run}")
    _log(
        log,
        f"[quota] log={resolve_path(cfg.get('quota_watch_log_path'), default_log_path())} "
        f"cooldown={cfg.get('quota_watch_cooldown_sec')}s "
        f"prefer_pool={cfg.get('quota_watch_prefer_pool')}",
    )
    n = 0
    try:
        while True:
            n += 1
            try:
                report = once(cfg, log=log, dry_run=dry_run)
                action = report.get("action") or "none"
                if action != "none":
                    _log(log, f"[quota] iter={n} action={action} email={report.get('auth_email')}")
                elif n == 1 or n % 15 == 0:
                    probe = report.get("probe") or {}
                    _log(
                        log,
                        f"[quota] heartbeat iter={n} email={report.get('auth_email')} "
                        f"probe_ok={probe.get('ok', 'n/a')} log_hits={report.get('log_hits', 0)}",
                    )
            except Exception as exc:
                _log(log, f"[quota] iter error: {exc}")
            if max_iters > 0 and n >= max_iters:
                break
            time.sleep(max(poll, 5.0))
    except KeyboardInterrupt:
        _log(log, "[quota] stopped by user")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Grok CLI quota watch + auto credential refill")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to config.json")
    p.add_argument("--once", action="store_true", help="single iteration then exit")
    p.add_argument("--dry-run", action="store_true", help="detect only; do not rotate/register")
    p.add_argument("--force", action="store_true", help="ignore enabled flag / force probe+refill path")
    p.add_argument("--force-refill", action="store_true", help="skip detect; run pool/register now")
    p.add_argument("--status", action="store_true", help="print state + auth email and exit")
    p.add_argument("--max-iters", type=int, default=0, help="loop max iterations (0=forever)")
    return p


def print_status(cfg: dict[str, Any]) -> None:
    auth_path = resolve_path(cfg.get("local_grok_auth_path"), default_auth_path())
    state_path = resolve_path(
        cfg.get("quota_watch_state_path"),
        ROOT / ".quota_watch_state.json",
    )
    state = WatchState.load(state_path)
    entry = read_auth_entry(auth_path)
    pool = list_cpa_pool(cfg)
    print(
        json.dumps(
            {
                "auth_path": str(auth_path),
                "email": entry.get("email"),
                "expires": entry.get("expires") or entry.get("expired"),
                "has_key": bool(entry.get("key")),
                "pool_files": len(pool),
                "state_path": str(state_path),
                "last_trigger_at": state.last_trigger_at,
                "last_trigger_reason": state.last_trigger_reason,
                "last_action": state.last_action,
                "last_email": state.last_email,
                "triggers_today": state.triggers_today,
                "last_probe_ok": state.last_probe_ok,
                "last_probe_at": state.last_probe_at,
                "used_cpa_files": len(state.used_cpa_files),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cfg = merge_config(Path(args.config))
    if args.status:
        print_status(cfg)
        return 0
    if args.force_refill:
        state_path = resolve_path(
            cfg.get("quota_watch_state_path"),
            ROOT / ".quota_watch_state.json",
        )
        state = WatchState.load(state_path)
        # Manual force always bypasses cooldown.
        state.last_trigger_at = 0
        result = refill(
            cfg,
            state,
            reason="manual --force-refill",
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0 if result.get("ok") or result.get("dry_run") or result.get("skipped") else 1
    if args.once:
        report = once(cfg, dry_run=args.dry_run, force=args.force)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0
    run_loop(cfg, dry_run=args.dry_run, max_iters=args.max_iters)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
