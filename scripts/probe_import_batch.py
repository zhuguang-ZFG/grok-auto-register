#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AT-only probe for newly imported CPA files; soft-disable bad ones.

Rules (AGENTS.md):
  - Probe AT only (non-destructive). Do NOT refresh RT.
  - permission-denied (403) → soft-disable + recover_after (default 24h)
  - 401 unauthorized → soft-disable (token dead / revoked)
  - free-usage-exhausted / 429 → soft-disable quota window
  - network 0 → leave enabled (transient)
  - Never move to dead/ for this pass.

Usage:
  python scripts/probe_import_batch.py
  python scripts/probe_import_batch.py --source community-grok2api-zip-20260717
  python scripts/probe_import_batch.py --hours 6 --workers 20 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cpa_xai.probe import probe_models  # noqa: E402
from cpa_xai.usage import (  # noqa: E402
    mark_account_exhausted,
    mark_account_permission_denied,
)
from pool_health import soft_disable  # noqa: E402

AUTH_DIR = ROOT / "cpa_auths"
REPORT = ROOT / "logs" / "probe_import_batch.json"
DEFAULT_BASE = "https://cli-chat-proxy.grok.com/v1"


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


def classify(status: int, body: str) -> str:
    b = (body or "").lower()
    if status == 200:
        return "ok"
    if status == 403 and any(
        m in b
        for m in (
            "permission-denied",
            "permission_denied",
            "access to the chat endpoint is denied",
            "update the permissions",
        )
    ):
        return "permission_denied"
    if status == 403:
        return "forbidden"
    if status == 401:
        return "unauthorized"
    if status == 429 or "free-usage-exhausted" in b or "usage-exhausted" in b:
        return "quota"
    if status == 0:
        return "network"
    return f"http_{status}"


def select_files(
    *,
    source: str | None,
    hours: float,
    include_disabled: bool,
    enabled_only: bool,
) -> list[Path]:
    cutoff = time.time() - hours * 3600.0
    out: list[Path] = []
    for p in AUTH_DIR.glob("xai-*.json"):
        try:
            st = p.stat()
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not include_disabled and data.get("disabled"):
            continue
        if not data.get("access_token"):
            continue
        if enabled_only:
            # Full live-pool pass: every non-disabled credential CLIProxy may use.
            out.append(p)
            continue
        if source:
            if str(data.get("source") or "") != source:
                continue
        else:
            # Default: tagged import batch OR recently written files
            tagged = str(data.get("source") or "").startswith("community-grok2api")
            recent = st.st_mtime >= cutoff
            if not (tagged or recent):
                continue
        out.append(p)
    return out


def probe_one(path: Path, proxy: str | None) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"path": path.name, "class": "bad_json", "error": str(e)[:120]}
    at = data.get("access_token") or ""
    base = data.get("base_url") or DEFAULT_BASE
    r = probe_models(at, base_url=base, timeout=25.0, proxy=proxy)
    status = int(r.get("status") or 0)
    err = str(r.get("error") or "")
    cls = classify(status, err)
    return {
        "path": path.name,
        "email": data.get("email") or "",
        "class": cls,
        "status": status,
        "has_grok_45": bool(r.get("has_grok_45")),
        "error": err[:200],
        "source": data.get("source") or "",
    }


def apply_action(path: Path, cls: str) -> str:
    """Soft-disable according to class. Returns action taken."""
    p = AUTH_DIR / path if not path.is_absolute() else path
    if not p.is_file():
        # path may be name only
        p = AUTH_DIR / Path(path).name
    if cls == "ok":
        return "none"
    if cls == "permission_denied" or cls == "forbidden":
        mark_account_permission_denied(p, error=cls)
        return "soft_perm_denied"
    if cls == "unauthorized":
        soft_disable(p, f"at_probe_{cls}", hours=24.0)
        return "soft_unauthorized"
    if cls == "quota":
        mark_account_exhausted(p, disable_for_proxy=True)
        return "soft_quota"
    if cls.startswith("http_"):
        soft_disable(p, f"at_probe_{cls}", hours=6.0)
        return "soft_http"
    # network / bad_json: leave alone
    return "skip"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="", help="Only files with this source tag")
    ap.add_argument(
        "--hours",
        type=float,
        default=12.0,
        help="Also include files mtime within N hours (default 12)",
    )
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="Cap files to probe (0=all)")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Actually soft-disable bad accounts (default dry-run)",
    )
    ap.add_argument("--include-disabled", action="store_true")
    ap.add_argument(
        "--enabled-only",
        action="store_true",
        help="Probe all non-disabled live-pool accounts (full AT health pass)",
    )
    args = ap.parse_args()

    cfg = load_cfg()
    proxy = proxy_of(cfg)
    files = select_files(
        source=args.source or None,
        hours=args.hours,
        include_disabled=args.include_disabled,
        enabled_only=args.enabled_only,
    )
    if args.limit and args.limit > 0:
        files = files[: args.limit]

    print(
        f"selected={len(files)} apply={args.apply} workers={args.workers} "
        f"proxy={'yes' if proxy else 'no'} source={args.source or '(tag|recent)'}"
    )
    if not files:
        print("nothing to probe")
        return 0

    results: list[dict[str, Any]] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(probe_one, p, proxy): p for p in files}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                results.append(fut.result())
            except Exception as e:
                results.append(
                    {"path": futs[fut].name, "class": "error", "error": str(e)[:120]}
                )
            if done % 100 == 0 or done == len(files):
                print(f"  progress {done}/{len(files)}", flush=True)

    counts = Counter(r["class"] for r in results)
    actions = Counter()
    if args.apply:
        for r in results:
            act = apply_action(Path(r["path"]), r["class"])
            r["action"] = act
            actions[act] += 1

    elapsed = time.time() - t0
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "selected": len(files),
        "elapsed_sec": round(elapsed, 1),
        "apply": args.apply,
        "counts": dict(counts),
        "actions": dict(actions),
        "ok_rate": round(counts.get("ok", 0) / max(1, len(results)), 4),
        "sample_bad": [
            {k: r[k] for k in ("path", "email", "class", "status", "error") if k in r}
            for r in results
            if r.get("class") not in ("ok",)
        ][:30],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("--- summary ---")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:20s} {v}")
    print(f"ok_rate={report['ok_rate']:.1%} elapsed={elapsed:.0f}s")
    if args.apply:
        print("actions:", dict(actions))
    print(f"report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
