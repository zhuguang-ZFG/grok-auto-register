#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Own-domain vs buffer-domain pool policy.

Self-owned mail domains (config.defaultDomains) are primary for water-level
and local Grok CLI rotation. Third-party batch imports (e.g. lsw666.dpdns.org)
are buffer capacity for CLIProxy round-robin only, used as local-auth fallback
when own pool is empty.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


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


def domain_of_path(path: Path | str) -> str:
    name = Path(path).name
    # xai-email@domain.json
    if "@" in name:
        return name.rsplit("@", 1)[-1].removesuffix(".json").lower()
    return ""


def is_own_path(path: Path | str, cfg: dict[str, Any]) -> bool:
    own = own_domains(cfg)
    if not own:
        return True
    dom = domain_of_path(path)
    return any(d in dom or dom == d for d in own)


def is_buffer_path(path: Path | str, cfg: dict[str, Any]) -> bool:
    if not is_own_path(path, cfg):
        buf = buffer_domains(cfg)
        if not buf:
            return True  # non-own = buffer by default
        dom = domain_of_path(path)
        return any(d in dom or dom == d for d in buf)
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
    """Own first; buffer only if pool_local_use_buffer (default True) as tail."""
    own, buf = partition_paths(paths, cfg)
    use_buf = cfg.get("pool_local_use_buffer", True)
    if use_buf is False or str(use_buf).lower() in ("0", "false", "no"):
        return own
    # Prefer own only first; if prefer_own_only_strict, still append buffer as last resort
    return own + buf


def summarize_pool_files(paths: list[Path], cfg: dict[str, Any]) -> dict[str, int]:
    own, buf = partition_paths(paths, cfg)
    return {"own": len(own), "buffer": len(buf), "total": len(paths)}
