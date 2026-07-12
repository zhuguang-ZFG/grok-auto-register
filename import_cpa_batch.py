#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import CPA xAI auth JSON from zip(s) or directories into cpa_auths/.

Skips JWT-expired access tokens (unless --keep-expired), dedupes by email/sub,
normalizes headers/disabled/base_url for CLIProxy.

Usage:
  python import_cpa_batch.py D:/Downloads/batch_0001-0500.zip D:/Downloads/batch_0501-1000.zip
  python import_cpa_batch.py --dir D:/Downloads/cpa_dump
  python import_cpa_batch.py *.zip --keep-expired
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from cpa_xai.schema import DEFAULT_CLIENT_HEADERS, OIDC_CLIENT_ID, OIDC_ISSUER
except Exception:
    DEFAULT_CLIENT_HEADERS = {
        "User-Agent": "grok-shell/0.2.93 (linux; x86_64)",
        "x-grok-client-identifier": "grok-shell",
        "x-grok-client-version": "0.2.93",
        "x-xai-token-auth": "xai-grok-cli",
        "x-authenticateresponse": "authenticate-response",
    }
    OIDC_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329"
    OIDC_ISSUER = "https://auth.x.ai"


def jwt_claims(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        seg = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def jwt_exp(token: str) -> int:
    try:
        return int(jwt_claims(token).get("exp") or 0)
    except Exception:
        return 0


def email_of(d: dict[str, Any]) -> str:
    em = str(d.get("email") or "").strip().lower()
    if em:
        return em
    claims = jwt_claims(str(d.get("access_token") or ""))
    for k in ("email", "preferred_username", "upn"):
        if claims.get(k):
            return str(claims[k]).strip().lower()
    sub = str(d.get("sub") or claims.get("sub") or "").strip()
    return f"sub-{sub[:12]}@unknown.local" if sub else ""


def normalize_payload(d: dict[str, Any], *, exp: int) -> dict[str, Any]:
    out = dict(d)
    out["type"] = out.get("type") or "xai"
    out["auth_kind"] = out.get("auth_kind") or "oauth"
    out["email"] = email_of(out)
    claims = jwt_claims(str(out.get("access_token") or ""))
    if not out.get("sub") and claims.get("sub"):
        out["sub"] = claims.get("sub")
    if "disabled" not in out:
        out["disabled"] = False
    if not isinstance(out.get("headers"), dict):
        out["headers"] = dict(DEFAULT_CLIENT_HEADERS)
    out.setdefault("oidc_issuer", OIDC_ISSUER)
    try:
        out.setdefault("oidc_client_id", OIDC_CLIENT_ID)
    except Exception:
        pass
    out.setdefault("base_url", "https://cli-chat-proxy.grok.com/v1")
    out.setdefault("token_type", "Bearer")
    if exp and not out.get("expired"):
        out["expired"] = datetime.fromtimestamp(exp, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return out


def iter_json_blobs(paths: list[Path]):
    for path in paths:
        if path.is_dir():
            for p in sorted(path.rglob("*.json")):
                if p.name.startswith("."):
                    continue
                try:
                    yield str(p), json.loads(p.read_text(encoding="utf-8"))
                except Exception as exc:
                    yield str(p), {"__error__": str(exc)}
        elif path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".json"):
                        continue
                    try:
                        yield info.filename, json.loads(z.read(info.filename))
                    except Exception as exc:
                        yield info.filename, {"__error__": str(exc)}
        elif path.suffix.lower() == ".json":
            try:
                yield str(path), json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                yield str(path), {"__error__": str(exc)}


def load_existing(auth_dir: Path) -> tuple[set[str], set[str]]:
    emails: set[str] = set()
    subs: set[str] = set()
    for p in auth_dir.glob("xai-*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        em = str(d.get("email") or "").lower()
        if em:
            emails.add(em)
        sub = str(d.get("sub") or "").strip()
        if sub:
            subs.add(sub)
    return emails, subs


def import_paths(
    paths: list[Path],
    *,
    auth_dir: Path,
    keep_expired: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    auth_dir.mkdir(parents=True, exist_ok=True)
    existing_email, existing_sub = load_existing(auth_dir)
    now = time.time()
    stats: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    imported: list[str] = []
    skipped: list[list[str]] = []

    for src, d in iter_json_blobs(paths):
        stats["seen"] += 1
        if not isinstance(d, dict) or d.get("__error__"):
            stats["parse_fail"] += 1
            skipped.append([src, f"parse:{d.get('__error__') if isinstance(d, dict) else d}"])
            continue
        at = str(d.get("access_token") or "").strip()
        rt = str(d.get("refresh_token") or "").strip()
        if not at or not rt:
            stats["missing_token"] += 1
            skipped.append([src, "missing access/refresh"])
            continue
        exp = jwt_exp(at)
        if exp and exp <= now and not keep_expired:
            stats["jwt_expired"] += 1
            skipped.append([src, f"jwt_expired exp={exp}"])
            continue
        payload = normalize_payload(d, exp=exp or 0)
        email = payload["email"]
        if not email:
            stats["no_email"] += 1
            skipped.append([src, "no email"])
            continue
        sub = str(payload.get("sub") or "").strip()
        if email in existing_email or (sub and sub in existing_sub):
            stats["dup"] += 1
            skipped.append([src, f"dup {email}"])
            continue
        dest = auth_dir / f"xai-{email.replace('/', '_')}.json"
        if dest.exists():
            stats["dup_file"] += 1
            skipped.append([src, f"file exists {dest.name}"])
            continue
        if not dry_run:
            dest.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        existing_email.add(email)
        if sub:
            existing_sub.add(sub)
        if "@" in email:
            domains[email.split("@", 1)[1]] += 1
        imported.append(dest.name)
        stats["imported"] += 1

    return {
        "stats": dict(stats),
        "imported": len(imported),
        "skipped": len(skipped),
        "domains": dict(domains.most_common()),
        "pool_total_xai": len(list(auth_dir.glob("xai-*.json"))),
        "sample_imported": imported[:15],
        "sample_skipped": skipped[:20],
        "dry_run": dry_run,
        "auth_dir": str(auth_dir),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        import stdio_utf8  # noqa: F401
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Import CPA xAI auth JSON batches")
    p.add_argument("paths", nargs="*", help="zip / json / directories")
    p.add_argument("--dir", action="append", default=[], help="extra directory")
    p.add_argument("--auth-dir", default="", help="target cpa_auths (default project)")
    p.add_argument("--keep-expired", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report", default="", help="write JSON report path")
    args = p.parse_args(argv)

    paths: list[Path] = [Path(x) for x in args.paths]
    for d in args.dir:
        paths.append(Path(d))
    paths = [x for x in paths if x.exists()]
    if not paths:
        print("[!] no input paths", file=sys.stderr)
        return 2

    auth_dir = Path(args.auth_dir) if args.auth_dir else ROOT / "cpa_auths"
    if not auth_dir.is_absolute():
        auth_dir = (ROOT / auth_dir).resolve()

    report = import_paths(
        paths,
        auth_dir=auth_dir,
        keep_expired=bool(args.keep_expired),
        dry_run=bool(args.dry_run),
    )
    rep_path = Path(args.report) if args.report else ROOT / "logs" / "_import_batch_report.json"
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:5000])
    print(f"[*] report: {rep_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
