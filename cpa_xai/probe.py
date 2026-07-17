"""Probe free Grok 4.5 via cli-chat-proxy with a CPA access_token.

Community absorb (archive.zip + acpa_watchdog):
  - JWT ``bot_flag_source`` / risk claims (advisory; chat 200 still healthy)
  - chat error_kind classification (permission-denied / anti-bot / rate-limit)
  - combined ``probe_account_health`` for mint / pool tools
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .proxyutil import resolve_proxy
from .schema import DEFAULT_BASE_URL, DEFAULT_CLIENT_HEADERS, jwt_payload


def _opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    p = resolve_proxy(proxy)
    handlers: list[Any] = []
    if p:
        handlers.append(urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()


def decode_token_risk(access_token: str) -> dict[str, Any]:
    """Extract bot/risk claims from OIDC access_token JWT.

    xAI currently embeds ``bot_flag_source`` on free-build tokens.
    ``0`` / missing -> clean; non-zero -> flagged at mint time.
    Chat 200 still overrides hard-fail in probe_account_health.
    """
    out: dict[str, Any] = {
        "bot_flag_source": None,
        "bot_flagged": False,
        "risk_claims": {},
        "sub": "",
        "principal_type": "",
        "scope": "",
    }
    if not access_token or access_token.count(".") < 2:
        return out
    try:
        pl = jwt_payload(access_token)
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
        return out
    bfs = pl.get("bot_flag_source")
    out["bot_flag_source"] = bfs
    try:
        out["bot_flagged"] = bfs is not None and int(bfs) != 0
    except (TypeError, ValueError):
        out["bot_flagged"] = bool(bfs)
    out["sub"] = str(pl.get("sub") or pl.get("principal_id") or "")
    out["principal_type"] = str(pl.get("principal_type") or "")
    out["scope"] = str(pl.get("scope") or "")
    risk: dict[str, Any] = {}
    for k, v in pl.items():
        kl = str(k).lower()
        if any(x in kl for x in ("bot", "risk", "flag", "ban", "restrict")):
            risk[k] = v
    out["risk_claims"] = risk
    return out


def _chat_error_kind(status: int | None, error_text: str) -> str:
    t = (error_text or "").lower()
    if status == 403 and (
        "permission" in t
        or "chat endpoint is denied" in t
        or "access to the chat" in t
    ):
        return "permission-denied"
    if "anti-bot" in t or "anti_bot" in t or "bot-flag" in t or "bot_flag" in t:
        return "anti-bot"
    if ("rate" in t and "limit" in t) or "too many requests" in t:
        return "rate-limit"
    if status == 429 or "usage-exhausted" in t or "free-usage-exhausted" in t:
        return "quota-exhausted"
    if status == 401:
        return "auth"
    if status and status >= 500:
        return "server"
    if status == 403:
        return "forbidden"
    return "error" if error_text or status else "unknown"


def probe_models(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 30.0,
    proxy: str | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    url = f"{base}/models"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        **DEFAULT_CLIENT_HEADERS,
    }
    risk = decode_token_risk(access_token)
    opener = _opener(proxy)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            ids = [x.get("id") for x in body.get("data") or [] if isinstance(x, dict)]
            return {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "model_ids": ids,
                "has_grok_45": any(i == "grok-4.5" for i in ids),
                "bot_flag_source": risk.get("bot_flag_source"),
                "bot_flagged": bool(risk.get("bot_flagged")),
                "risk_claims": risk.get("risk_claims") or {},
            }
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "status": e.code,
            "error": e.read().decode("utf-8", errors="replace")[:500],
            "model_ids": [],
            "has_grok_45": False,
            "bot_flag_source": risk.get("bot_flag_source"),
            "bot_flagged": bool(risk.get("bot_flagged")),
            "risk_claims": risk.get("risk_claims") or {},
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "error": str(e),
            "model_ids": [],
            "has_grok_45": False,
            "bot_flag_source": risk.get("bot_flag_source"),
            "bot_flagged": bool(risk.get("bot_flagged")),
            "risk_claims": risk.get("risk_claims") or {},
        }


def probe_mini_response(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 60.0,
    proxy: str | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    url = f"{base}/responses"
    payload = {
        "model": "grok-4.5",
        "stream": False,
        "input": "Reply with exactly MINT_OK",
        "reasoning": {"effort": "low"},
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        **DEFAULT_CLIENT_HEADERS,
    }
    risk = decode_token_risk(access_token)
    opener = _opener(proxy)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            texts: list[str] = []
            for item in body.get("output") or []:
                if item.get("type") == "message":
                    for c in item.get("content") or []:
                        if c.get("type") == "output_text":
                            texts.append(c.get("text") or "")
            return {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "model": body.get("model"),
                "text": "\n".join(texts),
                "usage": body.get("usage"),
                "chat_ok": True,
                "error_kind": "",
                "bot_flag_source": risk.get("bot_flag_source"),
                "bot_flagged": bool(risk.get("bot_flagged")),
            }
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:800]
        kind = _chat_error_kind(e.code, err)
        return {
            "ok": False,
            "status": e.code,
            "error": err,
            "chat_ok": False,
            "error_kind": kind,
            "bot_flag_source": risk.get("bot_flag_source"),
            "bot_flagged": bool(risk.get("bot_flagged"))
            or kind in ("permission-denied", "anti-bot"),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "error": str(e),
            "chat_ok": False,
            "error_kind": "network",
            "bot_flag_source": risk.get("bot_flag_source"),
            "bot_flagged": bool(risk.get("bot_flagged")),
        }


def probe_account_health(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    proxy: str | None = None,
    probe_chat: bool = True,
    models_timeout: float = 30.0,
    chat_timeout: float = 60.0,
) -> dict[str, Any]:
    """Combined health: JWT bot flag + /models + optional /responses.

    Chat success is the hard gate. JWT bot_flag_source is advisory scoring only
    (community archive credential_pool behaviour).
    """
    risk = decode_token_risk(access_token)
    pr = probe_models(
        access_token, base_url=base_url, timeout=models_timeout, proxy=proxy
    )
    out: dict[str, Any] = {
        "ok": False,
        "has_grok_45": bool(pr.get("has_grok_45")),
        "chat_ok": None,
        "bot_flagged": bool(risk.get("bot_flagged") or pr.get("bot_flagged")),
        "bot_flag_source": risk.get("bot_flag_source"),
        "risk_claims": risk.get("risk_claims") or {},
        "probe_models": pr,
        "probe_chat": None,
        "error": "",
        "tags": [],
    }
    tags: list[str] = []
    if out["bot_flagged"]:
        tags.append("bot-flag")
    if not pr.get("ok"):
        out["error"] = str(pr.get("error") or f"models status {pr.get('status')}")
        tags.append("models-fail")
        out["tags"] = tags
        return out
    if not pr.get("has_grok_45"):
        out["error"] = "no grok-4.5 in /models"
        tags.append("no-4.5")
        out["tags"] = tags
        return out

    if not probe_chat:
        out["ok"] = bool(pr.get("has_grok_45"))
        out["tags"] = tags
        if out["bot_flagged"]:
            out["error"] = f"bot_flag_source={risk.get('bot_flag_source')}"
        return out

    ch = probe_mini_response(
        access_token, base_url=base_url, timeout=chat_timeout, proxy=proxy
    )
    out["probe_chat"] = ch
    out["chat_ok"] = bool(ch.get("ok"))
    if ch.get("bot_flagged") and not ch.get("ok"):
        out["bot_flagged"] = True
        if "bot-flag" not in tags:
            tags.append("bot-flag")
    kind = str(ch.get("error_kind") or "")
    if kind:
        tags.append(kind)
    if not ch.get("ok"):
        out["error"] = str(ch.get("error") or kind or f"chat status {ch.get('status')}")
        if kind in ("permission-denied", "anti-bot", "forbidden"):
            out["bot_flagged"] = True
            if "bot-flag" not in tags:
                tags.append("bot-flag")
        out["tags"] = tags
        out["ok"] = False
        return out

    out["ok"] = True
    out["tags"] = tags
    if out["bot_flagged"]:
        out["error"] = f"bot_flag_source={risk.get('bot_flag_source')} (chat still ok)"
    return out
