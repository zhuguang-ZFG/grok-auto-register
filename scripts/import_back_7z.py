#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import OAuth CPA backup (xai_oauth_*.json from 7z) into cpa_auths/.

Converts xai_oauth_<email-with-_>_<timestamp>.json → xai-<email>.json
strips per-auth proxy, keeps tokens, dedupes against existing pool.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import py7zr

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = Path(r"D:/Downloads/back.7z")
DEST = ROOT / "cpa_auths"
TMP = ROOT / "logs" / "_back_import"


def parse_email_from_name(name: str) -> str | None:
    """xai_oauth_aliceadams3ad21c_6u.gardianwaves.org_20260712T111531Z.json
    → aliceadams3ad21c@6u.gardianwaves.org
    """
    stem = Path(name).stem
    if not stem.startswith("xai_oauth_"):
        return None
    body = stem[len("xai_oauth_"):]
    # strip trailing _<timestamp>
    body = re.sub(r"_\d{8}T\d{6}Z$", "", body)
    # first underscore after the user part = @ separator
    # user part has no dots; domain has dots. Find first part containing a dot.
    parts = body.split("_")
    for i in range(1, len(parts)):
        candidate = "_".join(parts[:i]) + "@" + ".".join(parts[i:])
        if "." in "@".join(parts[i:]):
            return candidate
    return None


def import_all() -> dict:
    DEST.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    # Extract all
    with py7zr.SevenZipFile(str(ARCHIVE), mode="r") as z:
        z.extractall(path=str(TMP))

    back_dir = TMP / "back"
    stats = {"scanned": 0, "imported": 0, "skipped_dup": 0, "skipped_invalid": 0, "skipped_noemail": 0}

    for src in sorted(back_dir.glob("xai_oauth_*.json")):
        stats["scanned"] += 1
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            stats["skipped_invalid"] += 1
            continue

        email = data.get("email") or parse_email_from_name(src.name)
        if not email:
            stats["skipped_noemail"] += 1
            continue

        dest_name = f"xai-{email}.json"
        dest = DEST / dest_name

        if dest.exists():
            stats["skipped_dup"] += 1
            continue

        # Ensure email field set
        data["email"] = email
        # Remove stale per-auth proxy (dead port trap)
        for key in ("proxy", "proxy_url", "proxy-url"):
            data.pop(key, None)

        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(dest)
        stats["imported"] += 1

    # Cleanup temp
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)
    return stats


if __name__ == "__main__":
    s = import_all()
    print(json.dumps(s, indent=2))
