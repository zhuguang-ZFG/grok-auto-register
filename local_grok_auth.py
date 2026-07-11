"""Write CPA/xAI OIDC credentials into local Grok CLI auth.json.

Grok CLI stores session tokens at ~/.grok/auth.json and hot-reloads them.
Documented usage: jq -r '."https://accounts.x.ai/sign-in".key' ~/.grok/auth.json

IMPORTANT (Grok CLI 0.2.93+):
  - Entry MUST include auth_mode, create_time (RFC3339), user_id.
  - auth_mode must be \"oidc\" — \"web_login\" is treated as legacy and rejected
    (CLI may wipe the entry: \"ignoring legacy WebLogin token\").
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


LogFn = Callable[[str], None]

AUTH_ENTRY_KEY = "https://accounts.x.ai/sign-in"
# CLI 0.2.x rejects WebLogin as legacy; CPA device/OIDC tokens use oidc.
DEFAULT_AUTH_MODE = "oidc"


def default_auth_path() -> Path:
    # Prefer USERPROFILE on Windows; fall back to Path.home()
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".grok" / "auth.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".auth-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def load_auth_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _b64url_json(segment: str) -> dict[str, Any]:
    raw = segment.encode("ascii")
    pad = b"=" * (-len(raw) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + pad).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def decode_access_token_claims(access_token: str) -> dict[str, Any]:
    token = str(access_token or "").strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    return _b64url_json(parts[1])


def _rfc3339_from_unix(ts: int | float | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def _normalize_rfc3339(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith("Z") or "+" in text[10:]:
        # already timezone-ish
        if text.endswith("Z"):
            return text
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return text
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return text


def build_auth_entry(
    *,
    access_token: str,
    refresh_token: str = "",
    expires_in: int | None = None,
    expired: str = "",
    email: str = "",
    auth_mode: str = DEFAULT_AUTH_MODE,
    user_id: str = "",
    team_id: str = "",
    create_time: str = "",
) -> dict[str, Any]:
    """Build a CLI-readable auth entry (Grok 0.2.93+ schema)."""
    token = str(access_token or "").strip()
    claims = decode_access_token_claims(token)

    mode = str(auth_mode or DEFAULT_AUTH_MODE).strip() or DEFAULT_AUTH_MODE
    if mode in ("web_login", "WebLogin", "web"):
        # Legacy; CLI rejects / wipes these.
        mode = DEFAULT_AUTH_MODE

    exp_in = expires_in
    if exp_in is None and claims.get("exp") and claims.get("iat"):
        try:
            exp_in = int(claims["exp"]) - int(claims["iat"])
        except Exception:
            exp_in = None
    if exp_in is None:
        exp_in = 21600

    expires = _normalize_rfc3339(expired)
    if not expires and claims.get("exp") is not None:
        expires = _rfc3339_from_unix(claims.get("exp"))
    if not expires:
        expires = (datetime.now(timezone.utc) + timedelta(seconds=int(exp_in))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    created = _normalize_rfc3339(create_time)
    if not created and claims.get("iat") is not None:
        created = _rfc3339_from_unix(claims.get("iat"))
    if not created:
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            created = (exp_dt - timedelta(seconds=int(exp_in))).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    uid = str(user_id or "").strip() or str(
        claims.get("sub") or claims.get("principal_id") or ""
    ).strip()
    tid = str(team_id or "").strip() or str(claims.get("team_id") or "").strip()
    mail = str(email or "").strip() or str(claims.get("email") or "").strip()
    refresh = str(refresh_token or "").strip()

    entry: dict[str, Any] = {
        "type": "oauth",
        "auth_mode": mode,
        "key": token,
        "create_time": created,
        "user_id": uid or "unknown",
        "expires_in": int(exp_in),
        "expires": expires,
        "expires_at": expires,
        "expired": expires,
    }
    if refresh:
        entry["refresh"] = refresh
        entry["refresh_token"] = refresh
    if tid:
        entry["team_id"] = tid
    if mail:
        entry["email"] = mail
    return entry


def write_local_grok_auth(
    *,
    access_token: str,
    refresh_token: str = "",
    expires_in: int | None = None,
    expired: str = "",
    email: str = "",
    auth_path: str | Path | None = None,
    merge: bool = True,
    auth_mode: str = DEFAULT_AUTH_MODE,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Write/replace the xAI sign-in entry used by local `grok` CLI."""
    token = str(access_token or "").strip()
    if not token:
        return {"ok": False, "error": "empty access_token"}

    path = Path(auth_path).expanduser() if auth_path else default_auth_path()
    current = load_auth_file(path) if merge else {}

    entry = build_auth_entry(
        access_token=token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        expired=expired,
        email=email,
        auth_mode=auth_mode,
    )

    current[AUTH_ENTRY_KEY] = entry
    _atomic_write_json(path, current)
    if log:
        log(
            f"[+] 已写入本机 Grok 凭证: {path} "
            f"({entry.get('email') or 'no-email'} mode={entry.get('auth_mode')})"
        )
    return {
        "ok": True,
        "path": str(path),
        "email": entry.get("email") or email,
        "auth_mode": entry.get("auth_mode"),
        "user_id": entry.get("user_id"),
    }


def refresh_auth_entry(
    auth_path: str | Path | None = None,
    *,
    log: LogFn | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    """读 auth.json 里的 refresh_token，主动向 auth.x.ai 换新 token 并写回。

    保留原 email/user_id，只更新 access_token/refresh_token/expires。
    refresh_token 无效/过期时不抛异常，返回 {ok:False, reason:...}，让调用方
    （如 quota_watch）自行决定是否走池轮换换号。
    """
    path = Path(auth_path).expanduser() if auth_path else default_auth_path()
    data = load_auth_file(path)
    entry = data.get(AUTH_ENTRY_KEY) or {}
    old_refresh = str(entry.get("refresh_token") or "").strip()
    email = str(entry.get("email") or "")
    if not old_refresh:
        return {"ok": False, "reason": "no refresh_token in auth.json"}

    try:
        from cpa_xai.oauth_device import refresh_access_token, OAuthDeviceError
    except Exception as exc:
        return {"ok": False, "reason": f"import oauth_device failed: {exc}"}

    try:
        result = refresh_access_token(old_refresh, proxy=proxy)
    except OAuthDeviceError as exc:
        if log:
            log(f"[auth] refresh failed: {exc}")
        return {"ok": False, "reason": str(exc)}
    except Exception as exc:
        if log:
            log(f"[auth] refresh error: {exc}")
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    # 写回：保留 email/user_id，merge=True 保留其它条目
    wr = write_local_grok_auth(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        email=email,
        auth_path=path,
        merge=True,
        log=log,
    )
    wr["refreshed"] = wr.get("ok", False)
    if wr.get("ok"):
        wr["reason"] = "refreshed"
    return wr


def write_from_cpa_payload(
    payload: dict[str, Any],
    *,
    auth_path: str | Path | None = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid payload"}
    return write_local_grok_auth(
        access_token=str(payload.get("access_token") or ""),
        refresh_token=str(payload.get("refresh_token") or ""),
        expires_in=payload.get("expires_in"),
        expired=str(payload.get("expired") or payload.get("expires") or ""),
        email=str(payload.get("email") or ""),
        auth_path=auth_path,
        auth_mode=str(payload.get("auth_mode") or DEFAULT_AUTH_MODE),
        log=log,
    )


def write_from_cpa_file(
    cpa_json_path: str | Path,
    *,
    auth_path: str | Path | None = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    path = Path(cpa_json_path).expanduser()
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return write_from_cpa_payload(payload, auth_path=auth_path, log=log)


def write_from_config_and_cpa_result(
    config: dict,
    cpa_result: dict,
    *,
    log: LogFn | None = None,
) -> dict[str, Any]:
    if not config.get("local_grok_auth_auto", False):
        return {"ok": False, "skipped": True, "reason": "disabled"}
    if not cpa_result or not cpa_result.get("ok"):
        return {"ok": False, "skipped": True, "reason": "cpa_not_ok"}
    auth_path = str(config.get("local_grok_auth_path") or "").strip() or None
    # Prefer in-memory tokens if present
    for key in ("payload", "auth", "tokens"):
        blob = cpa_result.get(key)
        if isinstance(blob, dict) and blob.get("access_token"):
            return write_from_cpa_payload(blob, auth_path=auth_path, log=log)
    path = cpa_result.get("path") or cpa_result.get("cpa_path")
    if path:
        return write_from_cpa_file(path, auth_path=auth_path, log=log)
    if cpa_result.get("access_token"):
        return write_local_grok_auth(
            access_token=str(cpa_result.get("access_token")),
            refresh_token=str(cpa_result.get("refresh_token") or ""),
            expires_in=cpa_result.get("expires_in"),
            expired=str(cpa_result.get("expired") or ""),
            email=str(cpa_result.get("email") or ""),
            auth_path=auth_path,
            log=log,
        )
    return {"ok": False, "error": "no tokens in cpa_result"}
