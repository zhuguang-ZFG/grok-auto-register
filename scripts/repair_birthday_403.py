#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repair permission-denied (chat 403) CPA accounts by setting birth date.

Community finding (linux.do t/2564817, t/2579539): xAI gates chat behind a
birthday prompt. Accounts without birthDate get 403 permission-denied on
chat/completions; setting it via grok.com clears the gate immediately.

Our register flow only called set_birth_date inside enable_nsfw_for_token(),
which is skipped when config enable_nsfw=false — so recent accounts never
got a birthday. This script:

  1. scans cpa_auths for disabled + quota_state.reason == "permission-denied"
  2. chat-probes each (some self-heal over time; skip those already OK)
  3. for still-403 accounts with a known sso token (accounts_*.txt),
     POSTs https://grok.com/rest/auth/set-birth-date then re-probes
  4. re-enables accounts whose chat probe flips to 200

Usage:
  python scripts/repair_birthday_403.py --limit 3          # small sample
  python scripts/repair_birthday_403.py --workers 4        # full run
  python scripts/repair_birthday_403.py --probe-only       # just re-probe
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_cpa_with_probe import opener_for, probe_chat  # noqa: E402

PROXY = "http://127.0.0.1:7897"
SET_BIRTH_URL = "https://grok.com/rest/auth/set-birth-date"


def load_sso_map(root: Path) -> dict[str, str]:
    """email -> sso token from accounts_*.txt (email----password----sso)."""
    out: dict[str, str] = {}
    for f in glob.glob(str(root / "accounts_*.txt")):
        try:
            for line in open(f, encoding="utf-8", errors="ignore"):
                parts = line.strip().split("----")
                if len(parts) >= 3 and parts[0] and parts[2]:
                    out.setdefault(parts[0].strip().lower(), parts[2].strip())
        except OSError:
            continue
    return out


def find_candidates(auth_dir: Path) -> list[Path]:
    out = []
    for p in sorted(auth_dir.glob("xai-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("disabled") and (d.get("quota_state") or {}).get("reason") == "permission-denied":
            out.append(p)
    return out


def set_birth_date(sso: str, proxy: str | None, log=print) -> tuple[bool, str]:
    """POST set-birth-date with sso cookie session (curl_cffi impersonate)."""
    import datetime as dt
    import random

    from curl_cffi import requests as crequests

    today = dt.date.today()
    birth = today.replace(year=today.year - random.randint(20, 40))
    birth = birth.replace(day=random.randint(1, 28))
    payload = {"birthDate": f"{birth.isoformat()}T16:00:00.000Z"}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        with crequests.Session(impersonate="chrome120", proxies=proxies) as s:
            s.headers.update({
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "cookie": f"sso={sso}; sso-rw={sso}",
            })
            res = s.post(
                SET_BIRTH_URL,
                json=payload,
                headers={
                    "content-type": "application/json",
                    "origin": "https://grok.com",
                    "referer": "https://grok.com/",
                },
                timeout=20,
            )
            body = (res.text or "")[:200]
            if 200 <= res.status_code < 300:
                return True, "ok"
            return False, f"HTTP {res.status_code}: {body}"
    except Exception as exc:
        return False, f"exc: {exc}"


def reenable(path: Path) -> None:
    d = json.loads(path.read_text(encoding="utf-8"))
    d.pop("quota_state", None)
    d["disabled"] = False
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def process(path: Path, sso_map: dict[str, str], opener, probe_only: bool,
            reprobe_delay: float) -> dict:
    email = path.stem
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        email = d.get("email") or email
    except Exception:
        d = {}
    status, _ = probe_chat(d, opener)
    rec = {"email": email, "before": status}
    if status == "chat_ok":
        reenable(path)
        rec["action"] = "reenabled(self-healed)"
        return rec
    if probe_only or status != "permission_denied":
        rec["action"] = "skip"
        return rec
    sso = sso_map.get(str(email).lower())
    if not sso:
        rec["action"] = "no-sso"
        return rec
    ok, msg = set_birth_date(sso, PROXY)
    rec["birth"] = msg if not ok else "set"
    if not ok:
        rec["action"] = "birth-fail"
        return rec
    time.sleep(reprobe_delay)
    status2, _ = probe_chat(d, opener)
    rec["after"] = status2
    if status2 == "chat_ok":
        reenable(path)
        rec["action"] = "repaired"
    else:
        rec["action"] = "still-403"
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth-dir", default=str(ROOT / "cpa_auths"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--reprobe-delay", type=float, default=4.0)
    args = ap.parse_args()

    auth_dir = Path(args.auth_dir)
    cands = find_candidates(auth_dir)
    if args.limit:
        cands = cands[: args.limit]
    sso_map = load_sso_map(ROOT)
    print(f"[repair] candidates={len(cands)} sso_map={len(sso_map)}", flush=True)
    if not cands:
        return 0

    opener = opener_for(PROXY)
    stats = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(process, p, sso_map, opener, args.probe_only,
                          args.reprobe_delay): p for p in cands}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                rec = fut.result()
            except Exception as exc:
                rec = {"email": futs[fut].name, "action": f"error: {exc}"}
            stats[rec["action"]] = stats.get(rec["action"], 0) + 1
            print(f"[{i}/{len(cands)}] {rec}", flush=True)
    print(f"[repair] done in {time.time() - t0:.0f}s stats={json.dumps(stats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
