#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import CPA packs (zip/json/txt lines) with RT sample probe + failure fuse.

Community shared packs often look large but refresh_token is already revoked.
Flow:
  1) Parse candidates (do not write yet for fuse path, or write to quarantine)
  2) Sample N refresh attempts via proxy
  3) If ok_rate < min_ok_rate → abort, write nothing to live (or leave quarantine)
  4) Else import all survivors into cpa_auths/ with source=buffer tag

Supports:
  - zip of xai-*.json
  - directory of json
  - .txt lines: pure JSON or `{json}____sso`

Usage:
  python scripts/import_cpa_with_probe.py "D:/Downloads/300x-cpa.txt"
  python scripts/import_cpa_with_probe.py pack.zip --sample 30 --min-ok-rate 0.7
  python scripts/import_cpa_with_probe.py pack.zip --dry-run
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from cpa_xai.schema import CLIENT_ID as CLIENT
except Exception:  # pragma: no cover
    CLIENT = "b1a00492-073a-47ea-816f-4c329264a828"
DEFAULT_BASE = "https://cli-chat-proxy.grok.com/v1"
DEFAULT_HEADERS = {
    "x-grok-client-version": "0.2.93",
    "x-xai-token-auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-shell",
    "User-Agent": "grok-shell/0.2.93 (linux; x86_64)",
}


def load_cfg() -> dict[str, Any]:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def proxy_of(cfg: dict[str, Any]) -> str | None:
    return str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None


def classify_chat_result(status: int, body: str) -> str:
    """Classify a real chat probe for live-pool admission."""
    blob = (body or "").lower()
    if status == 200:
        return "chat_ok"
    if status == 403 and any(
        marker in blob
        for marker in (
            "permission-denied",
            "permission_denied",
            "access to the chat endpoint is denied",
            "update the permissions",
        )
    ):
        return "permission_denied"
    if status == 429 or "free-usage-exhausted" in blob or "usage-exhausted" in blob:
        return "quota_exhausted"
    if status == 401:
        return "unauthorized"
    return "http_error"


def opener_for(proxy: str | None):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    return urllib.request.build_opener()


def probe_chat(d: dict[str, Any], opener) -> tuple[str, str]:
    """Probe the actual chat surface; models-list success is insufficient."""
    token = str(d.get("access_token") or "").strip()
    if not token:
        return "unauthorized", "missing access_token"
    base = str(d.get("base_url") or DEFAULT_BASE).rstrip("/")
    headers = dict(DEFAULT_HEADERS)
    if isinstance(d.get("headers"), dict):
        headers.update({str(k): str(v) for k, v in d["headers"].items()})
    headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    payload = json.dumps(
        {
            "model": "grok-4.5",
            "messages": [{"role": "user", "content": "Reply OK."}],
            "max_tokens": 4,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with opener.open(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "ignore")
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        body = exc.read().decode("utf-8", "ignore")
    except Exception as exc:
        return "network_error", str(exc)[:300]
    return classify_chat_result(status, body), body


def admit_candidate(
    candidate: dict[str, Any],
    opener,
    *,
    chat_probe=probe_chat,
    refresher=None,
) -> tuple[str, dict[str, Any] | None]:
    """Admit only chat-capable accounts; refresh RT solely after AT 401."""
    status, _body = chat_probe(candidate, opener)
    if status == "chat_ok":
        return status, candidate
    if status != "unauthorized":
        return status, None
    refresh_fn = refresher or refresh_one
    refresh_status, refreshed = refresh_fn(candidate, opener)
    if refresh_status != "ok" or refreshed is None:
        return f"refresh_{refresh_status}", None
    status, _body = chat_probe(refreshed, opener)
    if status == "chat_ok":
        return status, refreshed
    return status, None


def parse_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    if "____" in line:
        line = line.split("____", 1)[0].strip()
    try:
        return json.loads(line)
    except Exception:
        a, b = line.find("{"), line.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(line[a : b + 1])
            except Exception:
                return None
    return None


def normalize(d: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(d)
    out["type"] = out.get("type") or "xai"
    out["auth_kind"] = out.get("auth_kind") or "oauth"
    out["disabled"] = False
    bu = str(out.get("base_url") or "")
    if (not bu) or ("api.x.ai" in bu):
        out["base_url"] = DEFAULT_BASE
    if not isinstance(out.get("headers"), dict):
        out["headers"] = dict(DEFAULT_HEADERS)
    try:
        from pool_policy import tag_pool_source

        out = tag_pool_source(out, cfg)
    except Exception:
        out["source"] = "buffer"
        out["pool_tier"] = "buffer"
    if out.get("source") != "own":
        out["source"] = "buffer"
        out["pool_tier"] = "buffer"
    out["imported_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def email_of(d: dict[str, Any]) -> str:
    return str(d.get("email") or "").strip().lower()


def load_candidates(paths: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in paths:
        if path.is_dir():
            for p in path.rglob("*.json"):
                try:
                    items.append(json.loads(p.read_text(encoding="utf-8")))
                except Exception:
                    pass
        elif path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".json"):
                        continue
                    try:
                        items.append(json.loads(z.read(info.filename)))
                    except Exception:
                        pass
        elif path.suffix.lower() in (".txt", ".jsonl", ".log"):
            for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                d = parse_line(ln)
                if d:
                    items.append(d)
        elif path.suffix.lower() == ".json":
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
    return items


def refresh_one(d: dict[str, Any], opener) -> tuple[str, dict[str, Any] | None]:
    rt = d.get("refresh_token")
    if not rt:
        return "no_rt", None
    body = (
        f"grant_type=refresh_token&refresh_token={rt}&client_id={CLIENT}"
    ).encode()
    req = urllib.request.Request(
        "https://auth.x.ai/oauth2/token",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "grok-shell/0.2.93",
        },
    )
    try:
        with opener.open(req, timeout=20) as r:
            tok = json.loads(r.read())
        if not tok.get("access_token"):
            return "empty", None
        out = dict(d)
        out["access_token"] = tok["access_token"]
        if tok.get("refresh_token"):
            out["refresh_token"] = tok["refresh_token"]
        if tok.get("expires_in"):
            exp = datetime.now(timezone.utc) + timedelta(seconds=int(tok["expires_in"]))
            out["expired"] = exp.strftime("%Y-%m-%dT%H:%M:%SZ")
            out["expires_in"] = int(tok["expires_in"])
        out["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return "ok", out
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", "ignore")
        if "revoked" in b or "invalid_grant" in b:
            return "revoked", None
        return "http", None
    except Exception:
        return "net", None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="zip/json/txt/dir")
    ap.add_argument("--auth-dir", default=str(ROOT / "cpa_auths"))
    ap.add_argument("--sample", type=int, default=25, help="RT probe sample size")
    ap.add_argument(
        "--min-ok-rate",
        type=float,
        default=0.7,
        help="abort import if sample ok rate below this (0-1)",
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="import even if fuse trips")
    ap.add_argument(
        "--refresh-all",
        action="store_true",
        default=True,
        help="after sample fuse passes, refresh every candidate; only write ok (default)",
    )
    ap.add_argument(
        "--no-refresh-all",
        action="store_false",
        dest="refresh_all",
        help="legacy: write unprobed candidates after sample fuse only",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=8,
        help="parallel refresh workers when --refresh-all (default 8)",
    )
    args = ap.parse_args(argv)

    cfg = load_cfg()
    proxy = proxy_of(cfg)
    opener = opener_for(proxy)
    paths = [Path(p) for p in args.paths]
    for p in paths:
        if not p.exists():
            print(f"[!] missing {p}", file=sys.stderr)
            return 2

    raw = load_candidates(paths)
    print(f"[*] candidates raw={len(raw)} proxy={proxy}")
    if not raw:
        print("[!] no candidates")
        return 2

    # dedupe by email/sub
    seen: set[str] = set()
    cands: list[dict[str, Any]] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        if not d.get("access_token") or not d.get("refresh_token"):
            continue
        n = normalize(d, cfg)
        em = email_of(n)
        key = em or str(n.get("sub") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        cands.append(n)
    print(f"[*] unique with tokens={len(cands)}")

    auth_dir = Path(args.auth_dir)
    if not auth_dir.is_absolute():
        auth_dir = (ROOT / auth_dir).resolve()
    existing = {p.name for p in auth_dir.glob("xai-*.json")} if auth_dir.is_dir() else set()

    sample_n = min(max(1, int(args.sample)), len(cands))
    random.seed(int(args.seed))
    sample = list(cands)
    random.shuffle(sample)
    sample = sample[:sample_n]
    stats = Counter()
    refreshed_map: dict[str, dict[str, Any]] = {}
    for d in sample:
        st, nd = admit_candidate(d, opener)
        stats[st] += 1
        if st == "chat_ok" and nd is not None:
            refreshed_map[email_of(nd) or str(nd.get("sub"))] = nd
    ok = int(stats.get("chat_ok") or 0)
    rate = ok / sample_n if sample_n else 0.0
    print(
        f"[*] sample n={sample_n} stats={dict(stats)} ok_rate={rate:.1%} "
        f"min={args.min_ok_rate:.0%}"
    )

    fuse = rate < float(args.min_ok_rate)
    if fuse and not args.force:
        report = {
            "aborted": True,
            "reason": "sample_ok_rate_below_threshold",
            "ok_rate": rate,
            "min_ok_rate": args.min_ok_rate,
            "sample_stats": dict(stats),
            "candidates": len(cands),
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        out = ROOT / "logs" / "_import_probe_report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"[!] FUSE: not importing. report={out}")
        return 3

    if args.dry_run:
        mode = "refresh-all survivors" if args.refresh_all else "all candidates (legacy)"
        print(f"[*] dry-run would import via {mode}; candidates={len(cands)}")
        return 0

    # Full admission: only accounts that pass the real chat endpoint enter live.
    # AT is tested first; RT is consumed only after a 401, then chat is rechecked.
    full_stats: Counter = Counter()
    to_write: list[dict[str, Any]] = []
    if args.refresh_all:
        import concurrent.futures

        workers = max(1, min(int(args.workers or 8), 16))
        print(f"[*] chat-admission candidates={len(cands)} workers={workers}", flush=True)

        def _job(d0: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
            key0 = email_of(d0) or str(d0.get("sub") or "")
            if key0 in refreshed_map:
                return "chat_ok", refreshed_map[key0]
            return admit_candidate(d0, opener)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = list(ex.map(_job, cands))
        for st, nd in futs:
            full_stats[st] += 1
            if st == "chat_ok" and nd is not None:
                to_write.append(normalize(nd, cfg))
        print(f"[*] admission stats={dict(full_stats)} survivors={len(to_write)}", flush=True)
    else:
        for d in cands:
            key = email_of(d) or str(d.get("sub") or "")
            if key in refreshed_map:
                st, nd = "chat_ok", refreshed_map[key]
            else:
                st, nd = admit_candidate(d, opener)
            full_stats[st] += 1
            if st == "chat_ok" and nd is not None:
                to_write.append(normalize(nd, cfg))

    auth_dir.mkdir(parents=True, exist_ok=True)
    imported = skipped_dup = 0
    for d in to_write:
        em = email_of(d) or f"unknown_{imported}"
        safe = re.sub(r"[^\w.@+-]+", "_", em)
        name = f"xai-{safe}.json"
        if name in existing or (auth_dir / name).exists():
            skipped_dup += 1
            continue
        from pool_policy import atomic_write_json
        atomic_write_json(auth_dir / name, d)
        existing.add(name)
        imported += 1

    report = {
        "aborted": False,
        "imported": imported,
        "skipped_dup": skipped_dup,
        "candidates": len(cands),
        "survivors": len(to_write),
        "ok_rate": rate,
        "sample_stats": dict(stats),
        "full_refresh_stats": dict(full_stats),
        "refresh_all": bool(args.refresh_all),
        "fuse_would_trip": fuse,
        "forced": bool(args.force and fuse),
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    out = ROOT / "logs" / "_import_probe_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[*] report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
