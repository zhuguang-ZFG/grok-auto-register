#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K12 import pipeline for RT-capable sources (B).

Community formats supported:
  1) sub2api bundle JSON / zip of part JSONs
  2) sub2api CSV export (credentials JSON column)
  3) CPA single-account JSON / directory of JSON
  4) chatgpt.com /api/auth/session style dumps

Goal: only keep accounts that look like real K12 *and* preferably have
refresh_token for renewal. Snapshot-only (no RT) can still import with
--allow-no-rt for short-window use.

Examples:
  # dry classify a file
  python scripts/k12_rt_import.py inspect D:/Downloads/xxx.zip

  # import only K12 with refresh_token
  python scripts/k12_rt_import.py import D:/Downloads/xxx.zip --require-rt --require-k12

  # import snapshot K12 (no RT) into gateway
  python scripts/k12_rt_import.py import D:/Downloads/xxx.zip --require-k12 --allow-no-rt

  # refresh gateway accounts that have RT (via chatgpt2api refresh API)
  python scripts/k12_rt_import.py refresh-gateway --limit 500
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

GATEWAY = "http://127.0.0.1:8124"
AUTH_KEY = "k12-pool-local"
BATCH = 300


def log(msg: str) -> None:
    print(msg, flush=True)


def http_json(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 120.0) -> tuple[int, Any]:
    data = None
    headers = {"Authorization": f"Bearer {AUTH_KEY}"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{GATEWAY.rstrip('/')}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def jwt_claims(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def jwt_plan(token: str) -> str:
    pl = jwt_claims(token)
    auth = pl.get("https://api.openai.com/auth") or pl.get("auth") or {}
    if isinstance(auth, dict):
        return str(auth.get("chatgpt_plan_type") or "")
    return ""


def jwt_exp(token: str) -> int | None:
    pl = jwt_claims(token)
    exp = pl.get("exp")
    try:
        return int(exp) if exp is not None else None
    except Exception:
        return None


def jwt_kid_alg(token: str) -> tuple[str, str]:
    try:
        parts = token.split(".")
        hdr = parts[0] + "=" * (-len(parts[0]) % 4)
        h = json.loads(base64.urlsafe_b64decode(hdr))
        return str(h.get("kid") or ""), str(h.get("alg") or "")
    except Exception:
        return "", ""


def is_synthetic(token: str, email: str = "") -> bool:
    kid, alg = jwt_kid_alg(token)
    if alg == "none" or "dummy" in kid.lower():
        return True
    if "example.invalid" in (email or "").lower():
        return True
    pl = jwt_claims(token)
    iss = str(pl.get("iss") or "")
    if "example.invalid" in iss:
        return True
    return False


def is_k12_plan(plan: str) -> bool:
    p = (plan or "").strip().lower()
    return p in {"k12", "education", "edu"}


def normalize_account(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize many community formats into gateway flat account dict."""
    if not isinstance(raw, dict):
        return None

    # session dump
    if raw.get("accessToken") and not raw.get("access_token"):
        raw = {
            "access_token": raw.get("accessToken"),
            "refresh_token": raw.get("refreshToken") or raw.get("refresh_token") or "",
            "id_token": raw.get("idToken") or raw.get("id_token") or "",
            "email": (raw.get("user") or {}).get("email") if isinstance(raw.get("user"), dict) else raw.get("email"),
        }

    creds = raw.get("credentials") if isinstance(raw.get("credentials"), dict) else {}
    access = str(raw.get("access_token") or creds.get("access_token") or "").strip()
    if not access:
        return None
    refresh = str(raw.get("refresh_token") or creds.get("refresh_token") or "").strip()
    id_token = str(raw.get("id_token") or creds.get("id_token") or "").strip()
    email = str(raw.get("email") or creds.get("email") or "").strip()
    plan = str(
        raw.get("plan_type")
        or raw.get("chatgpt_plan_type")
        or creds.get("plan_type")
        or raw.get("type")
        or ""
    ).strip()
    if not plan:
        plan = jwt_plan(access) or ""
    account_id = str(
        raw.get("account_id")
        or raw.get("chatgpt_account_id")
        or creds.get("chatgpt_account_id")
        or creds.get("account_id")
        or ""
    ).strip()
    exp = raw.get("expires_at") or creds.get("expires_at") or jwt_exp(access)

    return {
        "access_token": access,
        "refresh_token": refresh,
        "id_token": id_token,
        "email": email,
        "account_id": account_id,
        "chatgpt_account_id": account_id,
        "plan_type": plan or "unknown",
        "type": plan or "unknown",
        "source_type": str(raw.get("source_type") or "import"),
        "expires_at": exp,
        "status": "正常",
        "_has_rt": bool(refresh),
        "_is_k12": is_k12_plan(plan) or is_k12_plan(jwt_plan(access)),
        "_synthetic": is_synthetic(access, email),
    }


def iter_json_accounts(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, dict):
                yield x
        return
    if not isinstance(obj, dict):
        return
    if "accounts" in obj and isinstance(obj["accounts"], list):
        for x in obj["accounts"]:
            if isinstance(x, dict):
                yield x
        return
    # single account-like
    if obj.get("access_token") or obj.get("accessToken") or obj.get("credentials"):
        yield obj
        return
    # dict of accounts
    for v in obj.values():
        if isinstance(v, dict) and (v.get("access_token") or v.get("credentials")):
            yield v


def load_path(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"not found: {path}")

    accounts: list[dict[str, Any]] = []

    if path.is_dir():
        for f in sorted(path.rglob("*.json")):
            try:
                obj = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            accounts.extend(iter_json_accounts(obj))
        return accounts

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.endswith(".json") or name.startswith("__MACOSX"):
                    continue
                try:
                    obj = json.loads(z.read(name).decode("utf-8"))
                except Exception:
                    continue
                accounts.extend(iter_json_accounts(obj))
        return accounts

    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                creds_raw = row.get("credentials") or ""
                try:
                    creds = json.loads(creds_raw) if creds_raw else {}
                except Exception:
                    creds = {}
                item = dict(row)
                if isinstance(creds, dict):
                    item["credentials"] = creds
                accounts.append(item)
        return accounts

    # json file
    obj = json.loads(path.read_text(encoding="utf-8"))
    accounts.extend(iter_json_accounts(obj))
    return accounts


def classify(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    stats = Counter()
    sample_k12_rt = None
    sample_k12_no_rt = None
    for raw in accounts:
        a = normalize_account(raw)
        if not a:
            stats["invalid"] += 1
            continue
        stats["valid_shape"] += 1
        if a["_synthetic"]:
            stats["synthetic"] += 1
            continue
        stats["real"] += 1
        if a["_is_k12"]:
            stats["k12"] += 1
            if a["_has_rt"]:
                stats["k12_with_rt"] += 1
                sample_k12_rt = sample_k12_rt or a
            else:
                stats["k12_no_rt"] += 1
                sample_k12_no_rt = sample_k12_no_rt or a
        else:
            stats["non_k12"] += 1
            if a["_has_rt"]:
                stats["non_k12_with_rt"] += 1
    return {
        "stats": dict(stats),
        "sample_k12_rt_email": (sample_k12_rt or {}).get("email"),
        "sample_k12_no_rt_email": (sample_k12_no_rt or {}).get("email"),
    }


def filter_accounts(
    accounts: list[dict[str, Any]],
    *,
    require_k12: bool,
    require_rt: bool,
    allow_no_rt: bool,
    include_synthetic: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in accounts:
        a = normalize_account(raw)
        if not a:
            continue
        if a["_synthetic"] and not include_synthetic:
            continue
        if require_k12 and not a["_is_k12"]:
            continue
        if require_rt and not a["_has_rt"]:
            continue
        if not allow_no_rt and not a["_has_rt"]:
            # default prefer RT accounts unless explicitly allowed
            if require_k12:
                # when requiring k12 only, allow snapshot if allow_no_rt
                continue
        tok = a["access_token"]
        if tok in seen:
            continue
        seen.add(tok)
        # strip internal flags
        out.append({k: v for k, v in a.items() if not k.startswith("_")})
    return out


def import_gateway(records: list[dict[str, Any]]) -> dict[str, int]:
    added = skipped = 0
    errors = 0
    for i in range(0, len(records), BATCH):
        batch = records[i : i + BATCH]
        code, body = http_json(
            "POST",
            "/api/accounts",
            {"accounts": batch, "refresh": False, "return_items": False},
            timeout=180,
        )
        if code != 200 or not isinstance(body, dict):
            errors += 1
            log(f"  batch {i // BATCH + 1}: ERROR {code} {str(body)[:120]}")
            continue
        added += int(body.get("added") or 0)
        skipped += int(body.get("skipped") or 0)
        log(f"  batch {i // BATCH + 1}: +{body.get('added', 0)} skip={body.get('skipped', 0)}")
    return {"added": added, "skipped": skipped, "errors": errors}


def cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.path)
    raw = load_path(path)
    log(f"loaded raw accounts: {len(raw)} from {path}")
    info = classify(raw)
    log(f"classify: {json.dumps(info['stats'], ensure_ascii=False)}")
    if info.get("sample_k12_rt_email"):
        log(f"sample K12+RT: {info['sample_k12_rt_email']}")
    if info.get("sample_k12_no_rt_email"):
        log(f"sample K12 no-RT: {info['sample_k12_no_rt_email']}")
    # guidance
    st = info["stats"]
    if st.get("k12_with_rt", 0):
        log("RECOMMEND: import --require-k12 --require-rt")
    elif st.get("k12_no_rt", 0):
        log("RECOMMEND: short-window import --require-k12 --allow-no-rt")
    else:
        log("RECOMMEND: no usable K12 found; do not import free-only dumps")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.path)
    raw = load_path(path)
    log(f"loaded raw accounts: {len(raw)}")
    info = classify(raw)
    log(f"classify: {json.dumps(info['stats'], ensure_ascii=False)}")

    require_rt = bool(args.require_rt)
    allow_no_rt = bool(args.allow_no_rt)
    if not require_rt and not allow_no_rt:
        # default: if any RT exists prefer RT; else require explicit allow-no-rt for snapshot
        if info["stats"].get("k12_with_rt", 0) > 0:
            require_rt = True
        else:
            log("No K12+RT found. Refusing snapshot import without --allow-no-rt")
            return 2

    filtered = filter_accounts(
        raw,
        require_k12=bool(args.require_k12),
        require_rt=require_rt,
        allow_no_rt=allow_no_rt or require_rt,
        include_synthetic=bool(args.include_synthetic),
    )
    log(
        f"filtered={len(filtered)} require_k12={args.require_k12} "
        f"require_rt={require_rt} allow_no_rt={allow_no_rt}"
    )
    if args.dry_run:
        log("dry-run: not importing")
        return 0
    if not filtered:
        log("nothing to import")
        return 1
    result = import_gateway(filtered)
    log(f"import done: {result}")
    code, body = http_json("GET", "/api/accounts?page=1&page_size=1")
    if code == 200 and isinstance(body, dict):
        log(f"gateway total now: {body.get('total')}")
    return 0 if result.get("errors", 0) == 0 else 1


def cmd_refresh_gateway(args: argparse.Namespace) -> int:
    """Trigger chatgpt2api refresh for accounts (uses stored refresh_token when present)."""
    limit = max(1, int(args.limit))
    # gather some tokens
    code, body = http_json("GET", f"/api/accounts?page=1&page_size={min(limit, 200)}&status=normal")
    if code != 200 or not isinstance(body, dict):
        log(f"list failed: {code} {body}")
        return 1
    items = body.get("items") or []
    tokens = [str(a.get("access_token") or "") for a in items if isinstance(a, dict) and a.get("access_token")]
    tokens = [t for t in tokens if t][:limit]
    if not tokens:
        log("no tokens")
        return 1
    code, body = http_json("POST", "/api/accounts/refresh", {"access_tokens": tokens})
    log(f"refresh started: {code} {body}")
    if code != 200 or not isinstance(body, dict):
        return 1
    progress_id = body.get("progress_id")
    if not progress_id:
        return 0
    # poll a bit
    for _ in range(30):
        c2, b2 = http_json("GET", f"/api/accounts/refresh/progress/{progress_id}")
        if c2 == 200 and isinstance(b2, dict):
            log(f"progress: {b2}")
            if b2.get("done") or b2.get("finished") or b2.get("status") in {"done", "finished", "completed"}:
                break
        time.sleep(2)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K12 RT-aware importer")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_i = sub.add_parser("inspect")
    p_i.add_argument("path")

    p_im = sub.add_parser("import")
    p_im.add_argument("path")
    p_im.add_argument("--require-k12", action="store_true", default=True)
    p_im.add_argument("--no-require-k12", action="store_false", dest="require_k12")
    p_im.add_argument("--require-rt", action="store_true")
    p_im.add_argument("--allow-no-rt", action="store_true")
    p_im.add_argument("--include-synthetic", action="store_true")
    p_im.add_argument("--dry-run", action="store_true")

    p_r = sub.add_parser("refresh-gateway")
    p_r.add_argument("--limit", type=int, default=200)

    args = p.parse_args(argv)
    if args.cmd == "inspect":
        return cmd_inspect(args)
    if args.cmd == "import":
        return cmd_import(args)
    if args.cmd == "refresh-gateway":
        return cmd_refresh_gateway(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
