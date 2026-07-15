#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""x-grok-client-version 漂移监控：对比上游常量与当前基线。

首次运行自动写基线 logs/_cpa_client_version_baseline.json。
每次运行从 GitHub 获取上游 xaiClientVersionValue + release tag 做对比。
发现漂移 → exit 1，网络错误 → exit 2，一致 → exit 0。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
BASELINE = LOG_DIR / "_cpa_client_version_baseline.json"

CLIPROXY_EXE = "D:/cli-proxy-api/cli-proxy-api.exe"
RAW_URL = (
    "https://raw.githubusercontent.com/router-for-me/CLIProxyAPI/"
    "{branch}/internal/runtime/executor/xai_executor.go"
)
RELEASES_API = "https://api.github.com/repos/router-for-me/CLIProxyAPI/releases/latest"
FETCH_TIMEOUT = 15


def _get_cliproxy_version() -> dict:
    """运行 cli-proxy-api.exe --version，返回 {cliproy_version, commit} 或 {error}。"""
    try:
        r = subprocess.run(
            [CLIPROXY_EXE, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = (r.stderr or "") + (r.stdout or "")
    except Exception as e:
        return {"error": str(e)}

    m = re.search(
        r"CLIProxyAPI Version:\s*(\S+),\s*Commit:\s*(\S+)",
        combined,
    )
    if not m:
        return {"error": f"cannot parse version from: {combined[:200]}"}
    return {"cliproxy_version": m.group(1), "commit": m.group(2)}


def _fetch_url(url: str) -> str | None:
    """HTTP GET 直连（不走 Clash），超时 15s。返回 body 或 None。"""
    req = Request(url, headers={"User-Agent": "cpa-watch/1.0"})
    try:
        with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except URLError as e:
        if getattr(e, "code", None) == 404 and "main" in url:
            fallback = url.replace("/main/", "/master/")
            try:
                with urlopen(
                    Request(fallback, headers={"User-Agent": "cpa-watch/1.0"}),
                    timeout=FETCH_TIMEOUT,
                ) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except Exception:
                return None
        return None
    except Exception:
        return None


def _extract_client_version(body: str) -> str | None:
    """从 xai_executor.go 源码提取 xaiClientVersionValue 常量。"""
    m = re.search(r'xaiClientVersionValue\s*=\s*"([^"]+)"', body)
    return m.group(1) if m else None


def _fetch_latest_release_tag() -> str | None:
    """GET /releases/latest -> tag_name。"""
    body = _fetch_url(RELEASES_API)
    if not body:
        return None
    try:
        data = json.loads(body)
        return data.get("tag_name") or None
    except Exception:
        return None


def _read_baseline() -> dict:
    if BASELINE.is_file():
        try:
            return json.loads(BASELINE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_baseline(data: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    data["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    BASELINE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="x-grok-client-version 漂移监控",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="把当前实测版本 + 上游常量写进基线（升级后人工确认用）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="仅 exit code，不打印",
    )
    args = parser.parse_args(argv)

    # -- 1) 获取本地 CLIProxy 版本 --------------------------------
    local = _get_cliproxy_version()
    if "error" in local:
        if not args.quiet:
            print(f"[cpa-ver] local version error: {local['error']}")
        return 2

    # -- 2) --update-baseline：强制重写基线 ------------------------
    if args.update_baseline:
        body = _fetch_url(RAW_URL.format(branch="main"))
        if body is None:
            if not args.quiet:
                print("[cpa-ver] --update-baseline: cannot fetch upstream source")
            return 2
        upstream_ver = _extract_client_version(body)
        if not upstream_ver:
            if not args.quiet:
                print("[cpa-ver] --update-baseline: cannot extract client_version")
            return 2
        _write_baseline({
            "cliproxy_version": local["cliproxy_version"],
            "commit": local["commit"],
            "client_version": upstream_ver,
        })
        if not args.quiet:
            print(f"[cpa-ver] baseline updated: cliproxy={local['cliproxy_version']} "
                  f"commit={local['commit']} client_version={upstream_ver}")
        return 0

    # -- 3) 读取已有基线 -----------------------------------------
    baseline = _read_baseline()

    # 首次运行：自动创建基线
    if not baseline:
        body = _fetch_url(RAW_URL.format(branch="main"))
        if body is None:
            if not args.quiet:
                print("[cpa-ver] cannot fetch upstream for initial baseline")
            return 2
        upstream_ver = _extract_client_version(body)
        if not upstream_ver:
            if not args.quiet:
                print("[cpa-ver] cannot extract client_version from upstream")
            return 2
        _write_baseline({
            "cliproxy_version": local["cliproxy_version"],
            "commit": local["commit"],
            "client_version": upstream_ver,
        })
        if not args.quiet:
            print(f"[cpa-ver] initial baseline written: cliproxy={local['cliproxy_version']} "
                  f"commit={local['commit']} client_version={upstream_ver}")
        return 0

    # -- 4) 获取上游最新信息 --------------------------------------
    body = _fetch_url(RAW_URL.format(branch="main"))
    if body is None:
        if not args.quiet:
            print("[cpa-ver] network error: cannot fetch upstream source")
        return 2

    upstream_client_ver = _extract_client_version(body)
    if not upstream_client_ver:
        if not args.quiet:
            print("[cpa-ver] cannot extract client_version from upstream source")
        return 2

    latest_tag = _fetch_latest_release_tag()
    if not latest_tag:
        if not args.quiet:
            print("[cpa-ver] network error: cannot fetch latest release tag")
        return 2

    # -- 5) 对比判定 ---------------------------------------------
    drift = False
    if upstream_client_ver != baseline.get("client_version"):
        if not args.quiet:
            print(f"[cpa-ver] ALERT: client_version drift "
                  f"baseline={baseline.get('client_version')} "
                  f"upstream={upstream_client_ver}")
        drift = True
    if latest_tag != baseline.get("cliproxy_version"):
        if not args.quiet:
            print(f"[cpa-ver] ALERT: release tag drift "
                  f"baseline.cliproxy_version={baseline.get('cliproxy_version')} "
                  f"latest_release={latest_tag}")
        drift = True

    if drift:
        if not args.quiet:
            print("[cpa-ver] drift detected -- upgrade binary then --update-baseline")
        return 1

    if not args.quiet:
        print(f"[cpa-ver] OK: client_version={upstream_client_ver} release={latest_tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
