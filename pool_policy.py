#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Own-domain vs buffer-domain pool policy.

Modes (config pool_prefer_mode):
  - own_first (default): local Grok CLI rotation prefers self-owned domains;
    buffer (e.g. lsw666.dpdns.org) is fallback only.
  - buffer_first: burn third-party buffer quota first; own domains are soft-held
    (disabled + hold_reason) so CLIProxy round-robin skips them until restored.

CLIProxy only serves auth files with disabled!=true. Holding own accounts is the
reliable way to force the proxy onto the buffer pool without a second auth-dir.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

HOLD_REASON = "prefer_buffer"


def parse_domains(raw: str | Iterable[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.replace(";", ",").replace(" ", ",").split(",")
        return [p.strip().lower() for p in parts if p.strip()]
    return [str(x).strip().lower() for x in raw if str(x).strip()]


def own_domains(cfg: dict[str, Any]) -> list[str]:
    return parse_domains(cfg.get("defaultDomains") or cfg.get("own_domains") or "")


def buffer_domains(cfg: dict[str, Any]) -> list[str]:
    """Explicit buffer list, or empty meaning 'everything not own'."""
    return parse_domains(cfg.get("pool_buffer_domains") or "")


def prefer_mode(cfg: dict[str, Any]) -> str:
    # Accept both historical keys: pool_prefer_mode and pool_prefer
    mode = str(
        cfg.get("pool_prefer_mode") or cfg.get("pool_prefer") or "own_first"
    ).strip().lower()
    if mode in ("buffer", "buffer_first", "burn_buffer", "prefer_buffer"):
        return "buffer_first"
    return "own_first"


def domain_matches(candidate: str, owned: str) -> bool:
    """Exact domain or DNS subdomain suffix match (not bare substring).

    own=ccwu.cc  →  mail.ccwu.cc OK, evil.ccwu.cc is OK as subdomain of ccwu.cc
    but evilccwu.cc / notlima.cc.cd-style substring traps are rejected.
    For apex equality: dom == owned.
    """
    cand = (candidate or "").strip().lower().lstrip(".")
    own = (owned or "").strip().lower().lstrip(".")
    if not cand or not own:
        return False
    return cand == own or cand.endswith("." + own)


def is_own_email(email: str, cfg: dict[str, Any]) -> bool:
    em = (email or "").strip().lower()
    if "@" not in em:
        return False
    dom = em.rsplit("@", 1)[-1]
    own = own_domains(cfg)
    if not own:
        # Fail-closed for hygiene: empty own list ⇒ nothing is "own"
        # (avoids labeling all shared buffer as own when config is missing).
        return False
    return any(domain_matches(dom, d) for d in own)


def tag_pool_source(payload: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stamp source=own|buffer for hygiene (CLIProxy ignores unknown fields)."""
    cfg = cfg or {}
    out = dict(payload)
    em = str(out.get("email") or "").strip().lower()
    if em and is_own_email(em, cfg):
        out["source"] = "own"
        out["pool_tier"] = "own"
    else:
        # Non-own domain or missing email → buffer (shared/import)
        out["source"] = "buffer"
        out["pool_tier"] = "buffer"
    return out


def watermark_own_only(cfg: dict[str, Any]) -> bool:
    """When true, refill / heartbeat floor counts only own-domain CPA files."""
    v = cfg.get("pool_watermark_own_only", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off")
    return bool(v)


def domain_of_path(path: Path | str) -> str:
    name = Path(path).name
    if "@" in name:
        return name.rsplit("@", 1)[-1].removesuffix(".json").lower()
    return ""


def is_own_path(path: Path | str, cfg: dict[str, Any]) -> bool:
    own = own_domains(cfg)
    if not own:
        return False
    dom = domain_of_path(path)
    if not dom:
        return False
    return any(domain_matches(dom, d) for d in own)


def is_buffer_path(path: Path | str, cfg: dict[str, Any]) -> bool:
    if not is_own_path(path, cfg):
        buf = buffer_domains(cfg)
        if not buf:
            return True
        dom = domain_of_path(path)
        if not dom:
            return True
        return any(domain_matches(dom, d) for d in buf)
    return False


def partition_paths(
    paths: list[Path], cfg: dict[str, Any]
) -> tuple[list[Path], list[Path]]:
    own: list[Path] = []
    buf: list[Path] = []
    for p in paths:
        if is_own_path(p, cfg):
            own.append(p)
        else:
            buf.append(p)
    return own, buf


def order_for_local_rotate(paths: list[Path], cfg: dict[str, Any]) -> list[Path]:
    """Order candidates for ~/.grok/auth.json rotation."""
    own, buf = partition_paths(paths, cfg)
    use_buf = cfg.get("pool_local_use_buffer", True)
    buf_ok = not (
        use_buf is False or str(use_buf).lower() in ("0", "false", "no")
    )
    mode = prefer_mode(cfg)
    if mode == "buffer_first":
        if buf_ok:
            return buf + own
        return own
    # own_first
    if buf_ok:
        return own + buf
    return own


def summarize_pool_files(paths: list[Path], cfg: dict[str, Any]) -> dict[str, int]:
    own, buf = partition_paths(paths, cfg)
    return {
        "own": len(own),
        "buffer": len(buf),
        "total": len(paths),
        "prefer_mode": prefer_mode(cfg),
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic JSON write: tmp file + os.replace. Retries 3x on OSError."""
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    for attempt in range(3):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, path)
            return
        except OSError:
            time.sleep(0.05 * (attempt + 1))
    raise OSError(f"atomic_write_json failed: {path}")


def is_prefer_buffer_hold(data: dict[str, Any]) -> bool:
    if str(data.get("hold_reason") or "") == HOLD_REASON:
        return True
    qs = data.get("quota_state") or {}
    return str(qs.get("reason") or "") == HOLD_REASON


def hold_own_for_buffer(
    auth_dir: Path,
    cfg: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Soft-disable own-domain auths so CLIProxy burns buffer first.

    Does not touch accounts already disabled for real quota exhaustion
    (quota_state.reason != prefer_buffer and recover_after set).
    """
    stats = {"scanned": 0, "held": 0, "already": 0, "skipped_quota": 0}
    if not auth_dir.is_dir():
        return stats
    for path in sorted(auth_dir.glob("xai-*.json")):
        if not is_own_path(path, cfg):
            continue
        stats["scanned"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if is_prefer_buffer_hold(data) and data.get("disabled"):
            stats["already"] += 1
            continue
        qs = data.get("quota_state") or {}
        if data.get("disabled") and qs.get("recover_after") and not is_prefer_buffer_hold(data):
            # real exhaustion mark — leave alone
            stats["skipped_quota"] += 1
            continue
        data["disabled"] = True
        data["hold_reason"] = HOLD_REASON
        data["held_at"] = time.time()
        # no recover_after → reenable_recovered leaves operator holds alone
        if not dry_run:
            _atomic_write(path, data)
        stats["held"] += 1
    return stats


def release_own_hold(
    auth_dir: Path,
    cfg: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Clear prefer_buffer holds on own-domain auths."""
    stats = {"scanned": 0, "released": 0}
    if not auth_dir.is_dir():
        return stats
    for path in sorted(auth_dir.glob("xai-*.json")):
        if not is_own_path(path, cfg):
            continue
        stats["scanned"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not is_prefer_buffer_hold(data):
            continue
        data["disabled"] = False
        data.pop("hold_reason", None)
        data.pop("held_at", None)
        qs = data.get("quota_state")
        if isinstance(qs, dict) and str(qs.get("reason") or "") == HOLD_REASON:
            data.pop("quota_state", None)
        if not dry_run:
            _atomic_write(path, data)
        stats["released"] += 1
    return stats
