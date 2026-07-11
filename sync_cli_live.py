#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仅把当前健康 cpa_auths 同步到 cli_live（给 Grok CLI / CLIProxyAPI 热加载）。

CLIProxyAPI 把 auth-dir 指到 cli_live 即可无感切换：
  - 死号被 pool_health 移走后，下次 sync 不再出现
  - 新号补进 cpa_auths 后，health 通过即进入 cli_live
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pool_health import load_cfg, sync_cli_live_dir  # noqa: E402


def main() -> int:
    cfg = load_cfg()
    auth_dir = Path(str(cfg.get("cpa_auth_dir") or "./cpa_auths"))
    if not auth_dir.is_absolute():
        auth_dir = (ROOT / auth_dir).resolve()
    live_dir = Path(str(cfg.get("cli_live_dir") or "./cpa_auths"))
    if not live_dir.is_absolute():
        live_dir = (ROOT / live_dir).resolve()
    dead = {p.name for p in (auth_dir / "dead").glob("xai-*.json")} if (auth_dir / "dead").is_dir() else set()
    files = [p for p in sorted(auth_dir.glob("xai-*.json")) if p.name not in dead]
    sync_cli_live_dir(files, live_dir)
    print(f"[*] synced {len(files)} auth files -> {live_dir}")
    idx = live_dir / "pool_index.json"
    if idx.is_file():
        print(idx.read_text(encoding="utf-8")[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
