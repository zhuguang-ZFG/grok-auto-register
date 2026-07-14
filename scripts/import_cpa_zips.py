#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import CPA JSON packs (zip/7z/dir) into cpa_auths with dedupe + proxy scrub.

Usage:
  python scripts/import_cpa_zips.py D:/Downloads/cpa_auths_100.zip D:/Downloads/batch_*.zip
  python scripts/import_cpa_zips.py --scan-downloads
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "cpa_auths"
DOWNLOADS = Path(r"D:/Downloads")


def _email_from_payload_or_name(data: dict, name: str) -> str | None:
    email = str(data.get("email") or "").strip()
    if email and "@" in email:
        return email.lower()
    stem = Path(name).stem
    # xai-user@domain.json
    if stem.startswith("xai-") and "@" in stem:
        return stem[4:].lower()
    # xai_oauth_user_domain_ts.json (underscore form)
    if stem.startswith("xai_oauth_"):
        body = stem[len("xai_oauth_") :]
        body = re.sub(r"_\d{8}T\d{6}Z$", "", body)
        parts = body.split("_")
        for i in range(1, len(parts)):
            domain = ".".join(parts[i:])
            if "." in domain:
                return f"{'_'.join(parts[:i])}@{domain}".lower()
    # bare user@domain.json
    if "@" in stem:
        return stem.lower()
    # CPA export often uses xai-<uuid>.json with empty email; sub is account id
    sub = str(data.get("sub") or "").strip()
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        sub,
    ):
        return f"{sub.lower()}@unknown.local"
    # filename xai-<uuid>.json without payload email
    m = re.fullmatch(
        r"xai-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        stem,
        flags=re.I,
    )
    if m:
        return f"{m.group(1).lower()}@unknown.local"
    return None


def _scrub(data: dict) -> dict:
    for key in ("proxy", "proxy_url", "proxy-url"):
        data.pop(key, None)
    if not data.get("type"):
        data["type"] = "xai"
    return data


def _iter_json_from_zip(path: Path):
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.endswith("/") or not n.lower().endswith(".json"):
                continue
            if n.lower().endswith(("report.json", "index.json", "manifest.json")):
                continue
            try:
                raw = z.read(n)
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if not (data.get("access_token") or data.get("refresh_token")):
                continue
            yield n, data


def _iter_json_from_7z(path: Path):
    try:
        import py7zr
    except ImportError as e:
        raise RuntimeError("py7zr required for .7z import") from e
    with tempfile.TemporaryDirectory() as td:
        with py7zr.SevenZipFile(str(path), mode="r") as z:
            z.extractall(path=td)
        for p in Path(td).rglob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if not (data.get("access_token") or data.get("refresh_token")):
                continue
            yield p.name, data


def import_pack(path: Path, dest: Path) -> dict:
    stats = {
        "pack": str(path),
        "scanned": 0,
        "imported": 0,
        "skipped_dup": 0,
        "skipped_invalid": 0,
    }
    if not path.exists():
        stats["error"] = "missing"
        return stats

    try:
        if path.suffix.lower() == ".zip":
            items = list(_iter_json_from_zip(path))
        elif path.suffix.lower() == ".7z":
            items = list(_iter_json_from_7z(path))
        elif path.is_dir():
            items = []
            for p in path.rglob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(data, dict) and (
                    data.get("access_token") or data.get("refresh_token")
                ):
                    items.append((p.name, data))
        else:
            stats["error"] = f"unsupported {path.suffix}"
            return stats
    except Exception as e:
        stats["error"] = f"{type(e).__name__}: {e}"
        return stats

    dest.mkdir(parents=True, exist_ok=True)
    for name, data in items:
        stats["scanned"] += 1
        email = _email_from_payload_or_name(data, name)
        if not email:
            stats["skipped_invalid"] += 1
            continue
        data = _scrub(dict(data))
        data["email"] = email
        out = dest / f"xai-{email}.json"
        if out.exists():
            stats["skipped_dup"] += 1
            continue
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(out)
        stats["imported"] += 1
    return stats


def scan_downloads() -> list[Path]:
    patterns = (
        "*cpa*.zip",
        "*cpa*.7z",
        "*auth*.zip",
        "*xai*.zip",
        "batch_*.zip",
        "新鮮*.7z",
        "grok*cpa*.zip",
        "grok*cpa*.7z",
    )
    out: list[Path] = []
    for pat in patterns:
        out.extend(DOWNLOADS.glob(pat))
    # unique preserve order
    seen = set()
    uniq = []
    for p in out:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="zip/7z/dir packs")
    ap.add_argument("--scan-downloads", action="store_true")
    ap.add_argument("--dest", default=str(DEST))
    args = ap.parse_args(argv)
    dest = Path(args.dest)
    paths = [Path(p) for p in args.paths]
    if args.scan_downloads:
        paths.extend(scan_downloads())
    if not paths:
        print("no packs given; use paths or --scan-downloads")
        return 2
    total = {"packs": 0, "scanned": 0, "imported": 0, "skipped_dup": 0, "skipped_invalid": 0}
    for p in paths:
        s = import_pack(p, dest)
        total["packs"] += 1
        for k in ("scanned", "imported", "skipped_dup", "skipped_invalid"):
            total[k] += int(s.get(k) or 0)
        print(json.dumps(s, ensure_ascii=False))
    print("TOTAL", json.dumps(total, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
