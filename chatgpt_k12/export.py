# -*- coding: utf-8 -*-
"""Export registered + verified accounts to chatgpt2api-compatible formats.

Writes:
  1. Individual JSON to chatgpt_auths/<email>.json (like cpa_auths/ pattern)
  2. Bundle JSON to data/chatgpt_k12_bundle.json (for bulk import)
  3. Optionally imports directly into a running chatgpt2api instance
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_AUTH_DIR = ROOT / "chatgpt_auths"
DEFAULT_BUNDLE = ROOT / "data" / "chatgpt_k12_bundle.json"


def _jwt_exp(token: str) -> int | None:
    """Extract exp claim from JWT without verification."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def _jwt_user_id(token: str) -> str:
    """Extract chatgpt_user_id from id_token or access_token."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return ""
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        auth = payload.get("auth", {})
        return auth.get("chatgpt_user_id", "") or payload.get("sub", "")
    except Exception:
        return ""


def build_account_record(
    reg_result: dict[str, Any],
    check_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single account record in chatgpt2api CPA format."""
    access_token = reg_result.get("access_token", "")
    id_token = reg_result.get("id_token", "")
    refresh_token = reg_result.get("refresh_token", "")

    exp = _jwt_exp(access_token) or (int(time.time()) + 864000)
    user_id = ""
    if id_token:
        user_id = _jwt_user_id(id_token)
    elif access_token:
        user_id = _jwt_user_id(access_token)

    account_id = ""
    plan_type = "free"
    if check_result:
        account_id = check_result.get("account_id", "")
        plan_type = check_result.get("plan_type", "free")

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "email": reg_result.get("email", ""),
        "account_id": account_id,
        "chatgpt_account_id": account_id,
        "chatgpt_user_id": user_id,
        "plan_type": plan_type,
        "type": plan_type,
        "source_type": "registration",
        "status": "正常",
        "expires_at": exp,
        "created_at": reg_result.get("created_at", int(time.time())),
    }


def save_account(record: dict[str, Any], auth_dir: Path | None = None) -> Path:
    """Save a single account JSON to chatgpt_auths/<email>.json."""
    d = auth_dir or DEFAULT_AUTH_DIR
    d.mkdir(parents=True, exist_ok=True)

    email = record.get("email", "unknown")
    filename = f"chatgpt-{email}.json"
    # Sanitize filename
    filename = filename.replace("/", "_").replace("\\", "_")
    path = d / filename

    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    return path


def export_bundle(
    records: list[dict[str, Any]],
    output_file: Path | None = None,
) -> Path:
    """Write all records to a bundle JSON for bulk import."""
    path = output_file or DEFAULT_BUNDLE
    path.parent.mkdir(parents=True, exist_ok=True)

    bundle = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(records),
        "accounts": records,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)

    return path


def import_to_gateway(
    records: list[dict[str, Any]],
    *,
    base_url: str = "http://127.0.0.1:8124",
    auth_key: str = "",
    batch_size: int = 500,
    log: Any = None,
) -> dict[str, int]:
    """Batch-import records into a running chatgpt2api instance.

    POST /api/accounts with {accounts: [...], refresh: false, return_items: false}
    """
    log = log or print
    total_added = 0
    total_skipped = 0
    errors: list[str] = []

    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        payload = json.dumps({
            "accounts": batch,
            "refresh": False,
            "return_items": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/accounts",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
            added = result.get("added", 0)
            skipped = result.get("skipped", 0)
            total_added += added
            total_skipped += skipped
            log(f"  Batch {i // batch_size + 1}: +{added} added, {skipped} skipped")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:200]
            msg = f"Batch {i // batch_size + 1}: HTTP {exc.code}: {body}"
            errors.append(msg)
            log(f"  {msg}")
        except Exception as exc:
            msg = f"Batch {i // batch_size + 1}: {exc}"
            errors.append(msg)
            log(f"  {msg}")

    return {"added": total_added, "skipped": total_skipped, "errors": errors}
