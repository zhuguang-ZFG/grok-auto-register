# -*- coding: utf-8 -*-
"""Token verification — check plan_type via ChatGPT backend API.

Confirms that an account actually has K12 entitlement after joining the
workspace. The JWT itself does not carry workspace claims; this check
endpoint is the source of truth.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check_account(
    access_token: str,
    *,
    chatgpt_api: str = "https://chatgpt.com/backend-api",
    proxy_url: str = "",
) -> dict[str, Any]:
    """Query the check endpoint for plan_type and account info.

    GET /accounts/check/v4-2023-04-27

    Returns:
        {
            "plan_type": "k12" | "free" | "plus" | "pro" | ...,
            "account_id": str,          # chatgpt_account_id
            "account_user_role": str,   # e.g. "account-owner"
            "raw": dict,                # full response
        }
    Raises RuntimeError on HTTP failure.
    """
    from curl_cffi import requests as cffi_requests

    url = f"{chatgpt_api}/accounts/check/v4-2023-04-27"
    session = cffi_requests.Session(impersonate="chrome")
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    resp = session.get(url, headers=headers, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"check failed: {resp.status_code} {resp.text[:300]}"
        )

    data = resp.json()

    # Response shape: {"accounts": {"<account_id>": {"account": {...}, "entitlement": {...}}}}
    # or {"account": {...}} depending on version.
    accounts = data.get("accounts", {})
    if isinstance(accounts, dict) and accounts:
        first_key = next(iter(accounts))
        acct_info = accounts[first_key]
        account = acct_info.get("account", acct_info)
        plan_type = (
            account.get("plan_type")
            or acct_info.get("plan_type")
            or "unknown"
        )
        account_id = account.get("account_id") or first_key
        role = account.get("account_user_role", "")
        return {
            "plan_type": plan_type,
            "account_id": account_id,
            "account_user_role": role,
            "raw": data,
        }

    # Fallback: flat response
    return {
        "plan_type": data.get("plan_type", "unknown"),
        "account_id": data.get("account_id", ""),
        "account_user_role": data.get("account_user_role", ""),
        "raw": data,
    }


def is_k12(check_result: dict[str, Any]) -> bool:
    """Check if the account result indicates K12 plan."""
    plan = str(check_result.get("plan_type", "")).lower()
    return plan in ("k12", "education", "edu")
