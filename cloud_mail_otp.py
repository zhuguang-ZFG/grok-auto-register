#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud Mail (maillab/skymail fork) provider — e.g. community vip0.xyz.

Protocol (verified against vip0.xyz + upstream maillab/cloud-mail)::

    POST /api/login              {"email","password"} → data.token (JWT)
    Authorization: <jwt>         # plain token, NOT \"Bearer ...\"
    GET  /api/setting/websiteConfig → siteKey, domainList
    POST /api/account/add        {"email","token": <turnstile>} → accountId
    GET  /api/email/list         accountId, type=0 (RECEIVE), size, num optional
                                 list items include subject/text/content/code

This is a **buffer** channel only. Do not put shared domains into
``defaultDomains`` / own-pool waterline.

Config (any path works)::

    cloud_mail_base / cloud_mail_email / cloud_mail_password / cloud_mail_sitekey
    cloud_mail_credentials_file  (default: vip0_mail.local.json, gitignored)
    cloud_mail_domain            (default: first from websiteConfig or vip0.xyz)
    capsolver_api_key            (required to create sub-inboxes)
    proxy                        (optional; used for Cloud Mail HTTP)

``dev_token`` is a JSON blob so ``get_oai_code`` can poll the right accountId.
"""

from __future__ import annotations

import json
import re
import secrets
import string
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]
ResendFn = Callable[[], None]

DEFAULT_BASE = "https://vip0.xyz"
DEFAULT_SITEKEY = "0x4AAAAAAD0dY6S9OmQL32yO"
DEFAULT_CREDENTIALS_FILE = "vip0_mail.local.json"
DEFAULT_HEALTH_FILE = ".cloud_mail_domain_health.json"
# emailConst.type.RECEIVE = 0, SEND = 1 (maillab/cloud-mail)
EMAIL_TYPE_RECEIVE = 0
# CapSolver burn guard: one Turnstile is for the *site* (vip0.xyz API host),
# not per mailbox suffix — reuse token across domain failover.
DEFAULT_TURNSTILE_MAX = 2
DEFAULT_DOMAIN_COOLDOWN_SEC = 1800
DEFAULT_DOMAIN_FAIL_COOLDOWN = 3  # consecutive add fails → cool down

_CODE_RE_XAI = re.compile(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", re.I)
_CODE_RE_NUM = re.compile(
    r"(?:verification\s+code|your\s+code|confirm(?:ation)?\s+code)[:\s]+(\d{4,8})",
    re.I,
)


def _root() -> Path:
    return Path(__file__).resolve().parent


def _extract_code(text: str, subject: str = "") -> str | None:
    if subject:
        m = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.I)
        if m:
            return m.group(1)
        m = _CODE_RE_XAI.search(subject)
        if m:
            return m.group(1)
    blob = text or ""
    m = _CODE_RE_XAI.search(blob)
    if m:
        return m.group(1)
    m = _CODE_RE_NUM.search(blob)
    if m:
        return m.group(1)
    # bare 6-digit near "code"
    m = re.search(r"\b(\d{6})\b", blob)
    if m:
        return m.group(1)
    return None


def _load_credentials(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> dict[str, Any]:
    cfg = cfg or {}
    root = root or _root()
    out: dict[str, Any] = {}

    rel = str(cfg.get("cloud_mail_credentials_file") or DEFAULT_CREDENTIALS_FILE).strip()
    path = Path(rel)
    if not path.is_absolute():
        path = root / path
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out.update(data)
        except (OSError, json.JSONDecodeError):
            pass

    # config.json overrides file (except secrets often only in file)
    for k_src, k_dst in (
        ("cloud_mail_base", "base"),
        ("cloud_mail_email", "email"),
        ("cloud_mail_password", "password"),
        ("cloud_mail_sitekey", "sitekey"),
        ("cloud_mail_domain", "domain"),
        ("cloud_mail_account_id", "accountId"),
    ):
        v = cfg.get(k_src)
        if v is not None and str(v).strip() != "":
            out[k_dst if k_dst != "accountId" else "accountId"] = v

    if not out.get("base"):
        out["base"] = DEFAULT_BASE
    if not out.get("sitekey"):
        out["sitekey"] = DEFAULT_SITEKEY
    return out


def _session(proxy: str | None = None):
    try:
        from curl_cffi import requests as cf_requests
    except ImportError as e:
        raise RuntimeError("curl_cffi required for cloud_mail_otp") from e
    try:
        s = cf_requests.Session(impersonate="chrome131")
    except TypeError:
        s = cf_requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _auth_headers(jwt: str, *, json_body: bool = False) -> dict[str, str]:
    h = {
        "Authorization": jwt,  # no Bearer — vip0 rejects Bearer with 401
        "Accept": "application/json",
        "User-Agent": "grok-auto-register/cloud_mail_otp",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def login(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> tuple[str, dict[str, Any]]:
    """Login master account → (jwt, credentials dict)."""
    cfg = cfg or {}
    cred = _load_credentials(cfg, root=root)
    email = str(cred.get("email") or "").strip()
    password = str(cred.get("password") or "").strip()
    base = str(cred.get("base") or DEFAULT_BASE).rstrip("/")
    if not email or not password:
        raise RuntimeError(
            "cloud_mail credentials missing — set vip0_mail.local.json "
            "or cloud_mail_email/cloud_mail_password"
        )
    proxy = str(cfg.get("cloud_mail_proxy") or cfg.get("proxy") or "").strip() or None
    s = _session(proxy)
    r = s.post(
        f"{base}/api/login",
        json={"email": email, "password": password},
        headers={"Content-Type": "application/json", "User-Agent": "grok-auto-register/cloud_mail_otp"},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"cloud_mail login HTTP {r.status_code}: {(r.text or '')[:200]}")
    data = r.json() if r.text else {}
    if not isinstance(data, dict) or data.get("code") not in (200, "200", None):
        # some forks omit code
        pass
    token = None
    payload = data.get("data") if isinstance(data, dict) else None
    if isinstance(payload, dict):
        token = payload.get("token")
    if not token and isinstance(data, dict):
        token = data.get("token")
    if not token:
        raise RuntimeError(f"cloud_mail login failed: {(r.text or '')[:300]}")
    cred = dict(cred)
    cred["base"] = base
    return str(token), cred


def _solve_turnstile(cfg: dict[str, Any], website_url: str, sitekey: str) -> str:
    """Prefer project CapSolver helper; fallback inline."""
    try:
        import grok_register_ttk as reg

        token = reg.solve_turnstile_capsolver(
            website_url=website_url,
            sitekey=sitekey,
            log_callback=None,
            cancel_callback=None,
        )
        if token and len(token) >= 80:
            return token
    except Exception:
        pass

    api_key = str(cfg.get("capsolver_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("capsolver_api_key required for cloud_mail account/add")

    from curl_cffi import requests as cf_requests

    create = cf_requests.post(
        "https://api.capsolver.com/createTask",
        json={
            "clientKey": api_key,
            "task": {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": website_url,
                "websiteKey": sitekey,
            },
        },
        timeout=60,
    ).json()
    if create.get("errorId", 0) not in (0, None):
        raise RuntimeError(f"CapSolver createTask: {create.get('errorDescription') or create}")
    task_id = create.get("taskId")
    if not task_id:
        raise RuntimeError(f"CapSolver no taskId: {create}")
    for _ in range(40):
        time.sleep(2)
        st = cf_requests.post(
            "https://api.capsolver.com/getTaskResult",
            json={"clientKey": api_key, "taskId": task_id},
            timeout=60,
        ).json()
        if st.get("status") == "ready":
            tok = (st.get("solution") or {}).get("token") or ""
            if len(tok) >= 80:
                return tok
            raise RuntimeError("CapSolver ready but empty token")
        if st.get("status") == "failed" or st.get("errorId") not in (0, None):
            if st.get("status") == "processing":
                continue
            raise RuntimeError(f"CapSolver failed: {st.get('errorDescription') or st}")
    raise RuntimeError("CapSolver turnstile timeout")


def _normalize_domain_list(raw: Any) -> list[str]:
    """Accept list / comma-string / single string → clean hostnames without @."""
    items: list[str] = []
    if raw is None:
        return items
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
        items = parts
    elif isinstance(raw, (list, tuple)):
        for x in raw:
            if isinstance(x, str) and x.strip():
                items.append(x.strip())
            elif isinstance(x, dict):
                d = str(x.get("domain") or x.get("name") or "").strip()
                if d:
                    items.append(d)
    out: list[str] = []
    seen: set[str] = set()
    for d in items:
        d = d.lstrip("@").strip().lower()
        if not d or d in seen:
            continue
        seen.add(d)
        out.append(d)
    return out


def list_available_domains(
    cfg: dict[str, Any] | None = None,
    *,
    jwt: str = "",
    cred: dict[str, Any] | None = None,
) -> list[str]:
    """Return domains usable for account/add, in preference order.

    Sources (later does not override earlier unique list order for *configured* picks)::

        1. cloud_mail_domains / credentials domains (explicit allow-list)
        2. websiteConfig.domainList (server-side)
        3. fallback vip0.xyz / base hostname

    Domains that fail DNS/host checks can still be listed; create_inbox will
    try next domain on account/add failure when multi-domain is enabled.
    """
    cfg = cfg or {}
    cred = cred or {}
    configured = _normalize_domain_list(
        cfg.get("cloud_mail_domains")
        or cred.get("domains")
        or cfg.get("cloud_mail_domain")
        or cred.get("domain")
    )
    server: list[str] = []
    base = str(cred.get("base") or cfg.get("cloud_mail_base") or DEFAULT_BASE).rstrip("/")
    if jwt:
        proxy = str(cfg.get("cloud_mail_proxy") or cfg.get("proxy") or "").strip() or None
        s = _session(proxy)
        try:
            r = s.get(
                f"{base}/api/setting/websiteConfig",
                headers=_auth_headers(jwt),
                timeout=20,
            )
            data = (r.json() or {}).get("data") or {}
            server = _normalize_domain_list(data.get("domainList") or [])
            sk = data.get("siteKey") or data.get("sitekey")
            if sk and isinstance(cred, dict) and not cred.get("sitekey"):
                cred["sitekey"] = sk
        except Exception:
            pass

    # If user configured a list, intersect with server when server known (stay valid).
    if configured and server:
        allow = set(server)
        picked = [d for d in configured if d in allow]
        # keep configured-only if server list incomplete/stale
        return picked or configured
    if configured:
        return configured
    if server:
        # Prefer vip0 first among server list (others often 403 / parked).
        preferred = [d for d in server if d == "vip0.xyz" or d.endswith(".vip0.xyz")]
        rest = [d for d in server if d not in preferred]
        return preferred + rest
    host = urlparse(base).hostname or "vip0.xyz"
    return [host]


_domain_rr_index = 0
_health_lock = threading.Lock()


def _health_path(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> Path:
    cfg = cfg or {}
    root = root or _root()
    rel = str(cfg.get("cloud_mail_health_file") or DEFAULT_HEALTH_FILE).strip()
    path = Path(rel)
    if not path.is_absolute():
        path = root / path
    return path


def _load_health(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> dict[str, Any]:
    path = _health_path(cfg, root=root)
    if not path.is_file():
        return {"domains": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("domains"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"domains": {}}


def _save_health(state: dict[str, Any], cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> None:
    path = _health_path(cfg, root=root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _domain_row(state: dict[str, Any], domain: str) -> dict[str, Any]:
    doms = state.setdefault("domains", {})
    row = doms.get(domain)
    if not isinstance(row, dict):
        row = {"ok": 0, "fail": 0, "streak_fail": 0, "cooldown_until": 0.0, "last_ok": 0.0, "last_fail": 0.0}
        doms[domain] = row
    return row


def record_domain_ok(domain: str, cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> None:
    domain = (domain or "").strip().lower().lstrip("@")
    if not domain:
        return
    with _health_lock:
        state = _load_health(cfg, root=root)
        row = _domain_row(state, domain)
        row["ok"] = int(row.get("ok") or 0) + 1
        row["streak_fail"] = 0
        row["cooldown_until"] = 0.0
        row["last_ok"] = time.time()
        _save_health(state, cfg, root=root)


def record_domain_fail(
    domain: str,
    cfg: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
    reason: str = "",
) -> None:
    """Penalize mailbox suffix after account/add failure (not CapSolver failure)."""
    domain = (domain or "").strip().lower().lstrip("@")
    if not domain:
        return
    cfg = cfg or {}
    threshold = int(cfg.get("cloud_mail_domain_fail_cooldown") or DEFAULT_DOMAIN_FAIL_COOLDOWN)
    cool_sec = float(cfg.get("cloud_mail_domain_cooldown_sec") or DEFAULT_DOMAIN_COOLDOWN_SEC)
    with _health_lock:
        state = _load_health(cfg, root=root)
        row = _domain_row(state, domain)
        row["fail"] = int(row.get("fail") or 0) + 1
        row["streak_fail"] = int(row.get("streak_fail") or 0) + 1
        row["last_fail"] = time.time()
        row["last_reason"] = (reason or "")[:200]
        if int(row["streak_fail"]) >= max(1, threshold):
            row["cooldown_until"] = time.time() + max(60.0, cool_sec)
        _save_health(state, cfg, root=root)


def _domain_weight(domain: str, state: dict[str, Any], now: float) -> float:
    row = (state.get("domains") or {}).get(domain) or {}
    if not isinstance(row, dict):
        return 1.0
    until = float(row.get("cooldown_until") or 0)
    if until > now:
        return 0.0  # cooled down — skip unless all dead
    ok = float(row.get("ok") or 0)
    fail = float(row.get("fail") or 0)
    # Laplace smoothing; success lifts weight
    return max(0.05, (ok + 1.0) / (fail + 1.0))


def _active_domains(
    domains: list[str],
    cfg: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
) -> list[str]:
    """Filter cooled-down domains; if all cooled, return original list."""
    if not domains:
        return domains
    state = _load_health(cfg, root=root)
    now = time.time()
    live = [d for d in domains if _domain_weight(d, state, now) > 0]
    return live or list(domains)


def _weighted_choice(domains: list[str], state: dict[str, Any], now: float) -> str:
    weights = [_domain_weight(d, state, now) for d in domains]
    total = sum(weights)
    if total <= 0:
        return secrets.choice(domains)
    r = secrets.SystemRandom().random() * total
    acc = 0.0
    for d, w in zip(domains, weights):
        acc += w
        if r <= acc:
            return d
    return domains[-1]


def _pick_domain(
    cfg: dict[str, Any],
    cred: dict[str, Any],
    jwt: str,
    *,
    root: Path | None = None,
) -> str:
    """Pick one domain: random | weighted | round_robin | first."""
    domains = _active_domains(list_available_domains(cfg, jwt=jwt, cred=cred), cfg, root=root)
    if not domains:
        return "vip0.xyz"
    mode = str(cfg.get("cloud_mail_domain_mode") or cred.get("domain_mode") or "weighted").strip().lower()
    if mode in ("first", "fixed", "primary"):
        return domains[0]
    if mode in ("rr", "round_robin", "round-robin", "sequential"):
        global _domain_rr_index
        idx = _domain_rr_index % len(domains)
        _domain_rr_index += 1
        return domains[idx]
    if mode in ("random", "rand", "uniform"):
        return secrets.choice(domains)
    # default weighted by health (anti CapSolver burn on bad suffixes)
    state = _load_health(cfg, root=root)
    return _weighted_choice(domains, state, time.time())


def _pick_domain_candidates(
    cfg: dict[str, Any],
    cred: dict[str, Any],
    jwt: str,
    *,
    root: Path | None = None,
) -> list[str]:
    """Ordered try-list: healthiest first; cooled domains last (last resort)."""
    raw = list_available_domains(cfg, jwt=jwt, cred=cred) or ["vip0.xyz"]
    state = _load_health(cfg, root=root)
    now = time.time()
    live = [d for d in raw if _domain_weight(d, state, now) > 0]
    cold = [d for d in raw if d not in live]
    primary = _pick_domain(cfg, cred, jwt, root=root)
    # sort live by weight desc for failover order (reuse same Turnstile)
    live_sorted = sorted(live, key=lambda d: _domain_weight(d, state, now), reverse=True)
    if primary in live_sorted:
        live_sorted = [primary] + [d for d in live_sorted if d != primary]
    return live_sorted + cold


def _turnstile_rejected(body: Any, text: str) -> bool:
    blob = (text or "").lower()
    if "人机" in (text or "") or "turnstile" in blob or "captcha" in blob:
        return True
    if isinstance(body, dict):
        msg = str(body.get("message") or body.get("msg") or "")
        if "人机" in msg or "验证" in msg and "失败" in msg:
            return True
    return False


def _account_limit_reached(body: Any, text: str) -> bool:
    """True when master Cloud Mail hit sub-address quota (not domain-specific).

    Server returns the same 403 for every suffix — retrying other domains only
    burns CapSolver. Short-circuit the whole create_inbox.
    """
    msg = ""
    if isinstance(body, dict):
        msg = str(body.get("message") or body.get("msg") or "")
    blob = f"{msg}\n{text or ''}".lower()
    needles = (
        "email address limit",
        "address limit reached",
        "limit reached",
        "too many email",
        "邮箱数量",
        "邮箱上限",
        "地址数量",
        "数量上限",
        "已达上限",
        "超过限制",
    )
    return any(n in blob or n in (msg or "") for n in needles)


def create_inbox(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> tuple[str, str]:
    """Create a sub-address under the master Cloud Mail account.

    CapSolver-saving design::

        * Turnstile is site-scoped (API host), **not** per mailbox suffix.
        * Solve once, try all candidate domains with the **same** token.
        * Only re-solve when server rejects the token (max ``cloud_mail_turnstile_max``).
        * Domain health cools down repeatedly-failing suffixes so mix rarely picks them.

    Returns (email, dev_token_json).
    """
    cfg = cfg or {}
    root = root or _root()
    jwt, cred = login(cfg, root=root)
    base = str(cred.get("base") or DEFAULT_BASE).rstrip("/")
    sitekey = str(cred.get("sitekey") or DEFAULT_SITEKEY).strip()
    candidates = _pick_domain_candidates(cfg, cred, jwt, root=root)
    prefix_len = int(cfg.get("cloud_mail_prefix_len") or 10)
    proxy = str(cfg.get("cloud_mail_proxy") or cfg.get("proxy") or "").strip() or None
    s = _session(proxy)
    max_ts = int(cfg.get("cloud_mail_turnstile_max") or DEFAULT_TURNSTILE_MAX)
    max_ts = max(1, min(5, max_ts))

    last_err: Exception | None = None
    turnstile = ""
    ts_solves = 0

    def ensure_turnstile(force: bool = False) -> str:
        nonlocal turnstile, ts_solves, last_err
        if turnstile and not force:
            return turnstile
        if ts_solves >= max_ts:
            raise RuntimeError(
                f"cloud_mail CapSolver budget exhausted ({ts_solves}/{max_ts}): {last_err}"
            )
        turnstile = _solve_turnstile(cfg, base + "/", sitekey)
        ts_solves += 1
        return turnstile

    for domain in candidates:
        prefix = "tmp" + "".join(
            secrets.choice(string.ascii_lowercase + string.digits)
            for _ in range(max(6, prefix_len - 3))
        )
        email = f"{prefix}@{domain}"
        try:
            tok = ensure_turnstile(force=False)
        except Exception as exc:
            last_err = exc
            break  # no more CapSolver budget / solve hard-fail
        try:
            r = s.post(
                f"{base}/api/account/add",
                json={"email": email, "token": tok},
                headers=_auth_headers(jwt, json_body=True),
                timeout=30,
            )
        except Exception as exc:
            last_err = exc
            record_domain_fail(domain, cfg, root=root, reason=str(exc)[:120])
            continue

        text = r.text or ""
        body: Any = {}
        try:
            body = r.json() if text else {}
        except Exception:
            body = {}

        if r.status_code >= 400 or (
            isinstance(body, dict) and body.get("code") not in (200, "200", None, 0)
            and not isinstance(body.get("data"), dict)
        ):
            last_err = RuntimeError(
                f"cloud_mail account/add HTTP {r.status_code} @{domain}: {text[:200]}"
            )
            # Account-wide quota: do NOT walk other domains (same 403, wastes CapSolver)
            if _account_limit_reached(body, text):
                raise RuntimeError(
                    f"cloud_mail account address limit reached (stop multi-domain): {text[:160]}"
                )
            if _turnstile_rejected(body, text):
                turnstile = ""  # force re-solve next loop
                try:
                    ensure_turnstile(force=True)
                except Exception as exc:
                    last_err = exc
                    break
                # retry same domain once with fresh token
                try:
                    r = s.post(
                        f"{base}/api/account/add",
                        json={"email": email, "token": turnstile},
                        headers=_auth_headers(jwt, json_body=True),
                        timeout=30,
                    )
                    text = r.text or ""
                    body = r.json() if text else {}
                except Exception as exc:
                    last_err = exc
                    record_domain_fail(domain, cfg, root=root, reason=str(exc)[:120])
                    continue
                if _account_limit_reached(body, text):
                    raise RuntimeError(
                        f"cloud_mail account address limit reached: {text[:160]}"
                    )
            else:
                record_domain_fail(domain, cfg, root=root, reason=text[:120])
                continue

        data = body.get("data") if isinstance(body, dict) else None
        code_ok = not isinstance(body, dict) or body.get("code") in (200, "200", None, 0)
        if not code_ok and not isinstance(data, dict):
            last_err = RuntimeError(f"cloud_mail account/add failed @{domain}: {text[:240]}")
            if _account_limit_reached(body, text):
                raise RuntimeError(
                    f"cloud_mail account address limit reached: {text[:160]}"
                )
            if _turnstile_rejected(body, text):
                turnstile = ""
            else:
                record_domain_fail(domain, cfg, root=root, reason=text[:120])
            continue
        if not isinstance(data, dict):
            last_err = RuntimeError(f"cloud_mail account/add bad body @{domain}: {text[:240]}")
            record_domain_fail(domain, cfg, root=root, reason=text[:120])
            continue
        account_id = data.get("accountId") or data.get("id")
        if account_id is None:
            last_err = RuntimeError(f"cloud_mail no accountId @{domain}: {data}")
            record_domain_fail(domain, cfg, root=root, reason="no accountId")
            continue
        out_email = str(data.get("email") or email).strip()
        record_domain_ok(domain, cfg, root=root)
        blob = {
            "provider": "cloud_mail",
            "base": base,
            "jwt": jwt,
            "accountId": int(account_id),
            "email": out_email,
            "domain": domain,
            "master": str(cred.get("email") or ""),
            "sitekey": sitekey,
            "turnstile_solves": ts_solves,
        }
        return out_email, json.dumps(blob, ensure_ascii=False, separators=(",", ":"))

    raise RuntimeError(
        f"cloud_mail create_inbox failed domains={candidates} ts_solves={ts_solves}: {last_err}"
    )


def _parse_dev_token(dev_token: str) -> dict[str, Any]:
    raw = (dev_token or "").strip()
    if not raw.startswith("{"):
        raise RuntimeError("cloud_mail dev_token must be JSON session blob")
    data = json.loads(raw)
    if not isinstance(data, dict) or not data.get("jwt") or data.get("accountId") is None:
        raise RuntimeError(f"cloud_mail dev_token incomplete: keys={list(data) if isinstance(data, dict) else type(data)}")
    return data


def list_messages(
    dev_token: str,
    *,
    cfg: dict[str, Any] | None = None,
    size: int = 20,
) -> list[dict[str, Any]]:
    """Return receive-box messages (full rows: subject/text/content/code)."""
    cfg = cfg or {}
    sess = _parse_dev_token(dev_token)
    base = str(sess.get("base") or DEFAULT_BASE).rstrip("/")
    jwt = str(sess["jwt"])
    account_id = int(sess["accountId"])
    proxy = str(cfg.get("cloud_mail_proxy") or cfg.get("proxy") or "").strip() or None
    s = _session(proxy)
    r = s.get(
        f"{base}/api/email/list",
        params={
            "accountId": account_id,
            "allReceive": 0,
            "size": max(1, min(50, int(size))),
            "type": EMAIL_TYPE_RECEIVE,
            # cursor: omit emailId → service uses max id for desc scan
        },
        headers=_auth_headers(jwt),
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"cloud_mail email/list HTTP {r.status_code}: {(r.text or '')[:200]}")
    body = r.json() if r.text else {}
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        lst = data.get("list") or []
    elif isinstance(data, list):
        lst = data
    else:
        lst = []
    return [m for m in lst if isinstance(m, dict)]


def delete_account(
    dev_token: str,
    *,
    cfg: dict[str, Any] | None = None,
    log: LogFn | None = None,
) -> bool:
    """Delete the sub-inbox used for this registration (never the master login email).

    Cloud Mail soft-deletes (``isDel``); freed slots count toward account limit again.
    """
    cfg = cfg or {}
    log = log or (lambda _m: None)
    try:
        sess = _parse_dev_token(dev_token)
    except Exception as exc:
        log(f"[cloud_mail] delete skip bad token: {exc}")
        return False
    account_id = int(sess["accountId"])
    email = str(sess.get("email") or "").strip().lower()
    master = str(sess.get("master") or "").strip().lower()
    if master and email and email == master:
        log("[cloud_mail] refuse delete master inbox")
        return False
    base = str(sess.get("base") or DEFAULT_BASE).rstrip("/")
    jwt = str(sess["jwt"])
    proxy = str(cfg.get("cloud_mail_proxy") or cfg.get("proxy") or "").strip() or None
    s = _session(proxy)
    try:
        r = s.delete(
            f"{base}/api/account/delete",
            params={"accountId": account_id},
            headers=_auth_headers(jwt),
            timeout=30,
        )
    except Exception as exc:
        log(f"[cloud_mail] delete err accountId={account_id}: {exc}")
        return False
    text = r.text or ""
    try:
        body = r.json() if text else {}
    except Exception:
        body = {}
    ok = r.status_code < 400 and (
        not isinstance(body, dict) or body.get("code") in (200, "200", None, 0)
    )
    if ok:
        log(f"[cloud_mail] deleted sub-inbox accountId={account_id} {email}")
        return True
    log(f"[cloud_mail] delete failed accountId={account_id}: {text[:160]}")
    return False


def _delete_after_code_enabled(cfg: dict[str, Any] | None) -> bool:
    cfg = cfg or {}
    v = cfg.get("cloud_mail_delete_after_code", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def wait_code(
    dev_token: str,
    email: str = "",
    *,
    cfg: dict[str, Any] | None = None,
    timeout: float = 180,
    poll_interval: float = 3.0,
    log: LogFn | None = None,
    cancel: CancelFn | None = None,
    resend: ResendFn | None = None,
) -> str:
    """Poll receive box until code appears; optionally delete sub-inbox after success."""
    cfg = cfg or {}
    log = log or (lambda _m: None)
    deadline = time.time() + max(15.0, float(timeout))
    interval = max(1.0, float(poll_interval or 3.0))
    next_resend = time.time() + 40
    seen: set[str] = set()
    label = (email or "").strip() or "cloud_mail"

    while time.time() < deadline:
        if cancel and cancel():
            raise TimeoutError(f"cloud_mail wait cancelled ({label})")
        if resend and time.time() >= next_resend:
            try:
                resend()
            except Exception:
                pass
            next_resend = time.time() + 40
        try:
            messages = list_messages(dev_token, cfg=cfg)
        except Exception as exc:
            log(f"[cloud_mail] poll err: {exc}")
            time.sleep(interval)
            continue

        for msg in messages:
            eid = str(msg.get("emailId") or "")
            subject = str(msg.get("subject") or "")
            key = f"{eid}|{subject}|{msg.get('createTime') or ''}"
            if key in seen:
                continue
            seen.add(key)
            parts: list[str] = []
            for field in ("code", "text", "content", "message", "toEmail"):
                v = msg.get(field)
                if isinstance(v, str) and v.strip():
                    parts.append(v)
            blob = "\n".join(parts)
            blob = re.sub(r"<[^>]+>", " ", blob)
            code = _extract_code(blob, subject)
            if not code and isinstance(msg.get("code"), str) and msg.get("code"):
                c = str(msg["code"]).strip()
                if re.fullmatch(r"[A-Za-z0-9-]{4,16}", c):
                    code = c
            if code:
                log(f"[cloud_mail] code found subject={subject[:60]!r}")
                # Use-once: free account quota so next register can mint again
                if _delete_after_code_enabled(cfg):
                    try:
                        delete_account(dev_token, cfg=cfg, log=log)
                    except Exception as exc:
                        log(f"[cloud_mail] post-code delete err: {exc}")
                return code
        time.sleep(interval)

    raise TimeoutError(f"cloud_mail no code within {timeout}s for {label}")


def list_accounts(
    cfg: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
    jwt: str = "",
    base: str = "",
) -> list[dict[str, Any]]:
    """Paginate GET /api/account/list (max size 30 per page)."""
    cfg = cfg or {}
    if not jwt:
        jwt, cred = login(cfg, root=root)
        base = str(cred.get("base") or DEFAULT_BASE).rstrip("/")
    else:
        base = (base or DEFAULT_BASE).rstrip("/")
        cred = _load_credentials(cfg, root=root)
    proxy = str(cfg.get("cloud_mail_proxy") or cfg.get("proxy") or "").strip() or None
    s = _session(proxy)
    out: list[dict[str, Any]] = []
    account_id = 0
    last_sort = 9999999999
    for _ in range(40):
        r = s.get(
            f"{base}/api/account/list",
            params={"accountId": account_id, "size": 30, "lastSort": last_sort},
            headers=_auth_headers(jwt),
            timeout=30,
        )
        if r.status_code >= 400:
            break
        body = r.json() if r.text else {}
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not data:
            break
        for row in data:
            if isinstance(row, dict):
                out.append(row)
        last = data[-1]
        account_id = int(last.get("accountId") or 0)
        last_sort = int(last.get("sort") or 0)
        if len(data) < 30:
            break
    return out


def cleanup_tmp_accounts(
    cfg: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
    prefix: str = "tmp",
    dry_run: bool = False,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Delete non-master sub-inboxes whose local-part starts with ``prefix`` (default tmp)."""
    cfg = cfg or {}
    log = log or (lambda _m: None)
    jwt, cred = login(cfg, root=root)
    base = str(cred.get("base") or DEFAULT_BASE).rstrip("/")
    master = str(cred.get("email") or "").strip().lower()
    rows = list_accounts(cfg, root=root, jwt=jwt, base=base)
    deleted: list[str] = []
    skipped: list[str] = []
    proxy = str(cfg.get("cloud_mail_proxy") or cfg.get("proxy") or "").strip() or None
    s = _session(proxy)
    pref = (prefix or "tmp").lower()
    for row in rows:
        email = str(row.get("email") or "").strip()
        em_l = email.lower()
        aid = row.get("accountId")
        if not email or aid is None:
            continue
        if em_l == master:
            skipped.append(email)
            continue
        local = em_l.split("@", 1)[0]
        if pref and not local.startswith(pref):
            skipped.append(email)
            continue
        if dry_run:
            deleted.append(f"dry:{email}")
            continue
        try:
            r = s.delete(
                f"{base}/api/account/delete",
                params={"accountId": int(aid)},
                headers=_auth_headers(jwt),
                timeout=30,
            )
            body = r.json() if r.text else {}
            ok = r.status_code < 400 and (
                not isinstance(body, dict) or body.get("code") in (200, "200", None, 0)
            )
            if ok:
                deleted.append(email)
                log(f"[cloud_mail] cleanup deleted {email}")
            else:
                skipped.append(f"fail:{email}:{(r.text or '')[:80]}")
        except Exception as exc:
            skipped.append(f"err:{email}:{exc}")
    return {"total": len(rows), "deleted": deleted, "skipped": skipped, "master": master}


def is_cloud_mail_token(dev_token: str) -> bool:
    raw = (dev_token or "").strip()
    if not raw.startswith("{"):
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and data.get("provider") == "cloud_mail"


def pick_inbox(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> tuple[str, str]:
    """Alias for create_inbox — provider entry used by get_email_and_token."""
    return create_inbox(cfg, root=root)
