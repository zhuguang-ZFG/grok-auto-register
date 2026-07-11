#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自动把健康号池接到 CLIProxyAPI / 常见 auth 目录，实现全程无感。

策略（按优先级）：
1. config.cli_proxy_auth_dirs 显式列表
2. 扫描本机常见 CLIProxyAPI 数据目录
3. 始终维护项目内 cpa_auths/（规范热目录）

对每个目标目录：
- 写入/更新 junction 或同步 xai-*.json（Windows 优先 mklink /J 到 cli_live）
- 写 pool_index.json 方便外部读取
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"


def load_cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def discover_cli_dirs(cfg: dict) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            rp = p.expanduser().resolve()
        except Exception:
            rp = p
        key = str(rp).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(rp)

    # 1) explicit
    for raw in cfg.get("cli_proxy_auth_dirs") or []:
        if str(raw).strip():
            add(Path(str(raw).strip()))

    # 2) env
    for env_key in ("CLIPROXY_AUTH_DIR", "CPA_AUTH_DIR", "CLI_PROXY_AUTH_DIR"):
        v = os.environ.get(env_key, "").strip()
        if v:
            add(Path(v))

    # 3) common locations
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
    appdata = Path(os.environ.get("APPDATA", str(home / "AppData/Roaming")))
    candidates = [
        ROOT / "cpa_auths",
        home / ".cli-proxy-api" / "auth",
        home / ".cliproxyapi" / "auth",
        home / ".config" / "cli-proxy-api" / "auth",
        home / ".config" / "cliproxyapi" / "auth",
        local / "cli-proxy-api" / "auth",
        local / "CLIProxyAPI" / "auth",
        appdata / "cli-proxy-api" / "auth",
        appdata / "CLIProxyAPI" / "auth",
        Path("D:/cli-proxy-api/auth"),
        Path("D:/CLIProxyAPI/auth"),
        Path("C:/cli-proxy-api/auth"),
    ]
    # also scan shallow for config.yaml mentioning auth-dir
    for base in [home, local, appdata, Path("D:/"), Path("C:/")]:
        if not base.exists():
            continue
        try:
            for p in base.glob("**/config.y*ml"):
                if p.stat().st_size > 200_000:
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if "auth-dir" in text or "auth_dir" in text or "auths" in text:
                    # sibling auth folder
                    sib = p.parent / "auth"
                    if sib.is_dir() or "auth" in text:
                        add(sib)
                    # parse simple path after auth-dir:
                    for line in text.splitlines():
                        if "auth-dir" in line or "auth_dir" in line:
                            part = line.split(":", 1)[-1].strip().strip("\"'")
                            if part and not part.startswith("#") and ("/" in part or "\\" in part):
                                add(Path(part))
        except Exception:
            pass
        # don't walk entire D: deeply via glob above already limited by ** depth? pathlib ** is recursive - constrain
        break  # only home-level first; extra explicit below

    # project cli_live always first target for copy source, not as external sink only
    return found


def ensure_dir_linked_or_synced(target: Path, live_dir: Path) -> str:
    """让 target 内容等于 live_dir。优先 junction，失败则文件同步。"""
    target = Path(target)
    live_dir = Path(live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)

    # 如果 target 就是 live_dir，跳过
    try:
        if target.resolve() == live_dir.resolve():
            return "self"
    except Exception:
        if str(target) == str(live_dir):
            return "self"

    # Windows junction
    if os.name == "nt":
        try:
            if target.exists() or target.is_symlink() or target.is_junction():  # type: ignore[attr-defined]
                # 若已是指向 live 的 junction，OK
                try:
                    if target.resolve() == live_dir.resolve():
                        return "junction-exists"
                except Exception:
                    pass
                # 非空普通目录：做同步而不是强删
                if target.is_dir() and not target.is_symlink():
                    return _sync_files(live_dir, target)
            # 创建到 live 的 junction
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                return _sync_files(live_dir, target)
            cmd = f'cmd /c mklink /J "{target}" "{live_dir}"'
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if r.returncode == 0:
                return "junction-created"
            return _sync_files(live_dir, target) + f" (junction-fail: {r.stderr.strip()[:80]})"
        except Exception as e:
            return _sync_files(live_dir, target) + f" (err: {e})"
    else:
        # non-windows: symlink or sync
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.symlink_to(live_dir, target_is_directory=True)
                return "symlink-created"
            return _sync_files(live_dir, target)
        except Exception:
            return _sync_files(live_dir, target)


def _sync_files(src: Path, dst: Path) -> str:
    dst.mkdir(parents=True, exist_ok=True)
    # remove dest xai that are not in src
    src_names = {p.name for p in src.glob("xai-*.json")}
    for old in dst.glob("xai-*.json"):
        if old.name not in src_names:
            try:
                old.unlink()
            except Exception:
                pass
    n = 0
    for f in src.glob("xai-*.json"):
        shutil.copy2(f, dst / f.name)
        n += 1
    # copy index
    idx = src / "pool_index.json"
    if idx.is_file():
        shutil.copy2(idx, dst / "pool_index.json")
    return f"synced:{n}"


def main() -> int:
    cfg = load_cfg()
    live_dir = Path(str(cfg.get("cli_live_dir") or "./cpa_auths"))
    if not live_dir.is_absolute():
        live_dir = (ROOT / live_dir).resolve()
    live_dir.mkdir(parents=True, exist_ok=True)

    # 确保 cli_live 至少有当前健康号（若空则从 cpa_auths 拉一份）
    if not list(live_dir.glob("xai-*.json")):
        auth_dir = Path(str(cfg.get("cpa_auth_dir") or "./cpa_auths"))
        if not auth_dir.is_absolute():
            auth_dir = (ROOT / auth_dir).resolve()
        for f in auth_dir.glob("xai-*.json"):
            shutil.copy2(f, live_dir / f.name)

    targets = discover_cli_dirs(cfg)
    # always include live_dir itself for report
    report = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live_dir": str(live_dir),
        "live_count": len(list(live_dir.glob("xai-*.json"))),
        "targets": [],
    }
    print(f"[*] live_dir={live_dir} live={report['live_count']}")
    for t in targets:
        mode = ensure_dir_linked_or_synced(t, live_dir)
        print(f"[*] target {t} -> {mode}")
        report["targets"].append({"path": str(t), "mode": mode})

    out = live_dir / "auto_link_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # also write machine-wide pointer
    pointer = ROOT / "CLI_AUTH_DIR.txt"
    pointer.write_text(str(live_dir) + "\n", encoding="utf-8")
    print(f"[*] pointer -> {pointer}")
    print(f"[*] report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
