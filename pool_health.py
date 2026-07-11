#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""号池健康检查：刷新即将过期 token、标记失效、同步 CLI 可用目录。

用法:
  python pool_health.py
  python pool_health.py --refresh-all
  python pool_health.py --probe
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_PATH = ROOT / "config.json"


def load_cfg() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_proxy(cfg: dict) -> str | None:
    for key in ("mint_proxy", "cpa_push_proxy", "browser_proxy", "proxy"):
        v = str(cfg.get(key) or "").strip()
        if v:
            return v
    return None


def parse_expired(value: str) -> datetime | None:
    s = (value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def probe_access(access_token: str, base_url: str, proxy: str | None, timeout: float = 20.0) -> tuple[bool, str]:
    """轻量探测：对 cli-chat-proxy 发一条极短 models/list 或 chat 预检。

    优先 GET {base}/models；失败再记原因。不在这里打真实对话，避免耗额度。
    """
    base = (base_url or "https://cli-chat-proxy.grok.com/v1").rstrip("/")
    url = base + "/models"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "x-grok-client-version": "0.2.93",
    }
    handlers: list[Any] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 200) or 200)
            body = resp.read(300).decode("utf-8", errors="replace")
            if 200 <= code < 300:
                return True, f"HTTP {code}"
            return False, f"HTTP {code}: {body[:120]}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        # 401/403 = 死号；429 = 活着但限流
        if e.code == 429:
            return True, f"HTTP 429 rate-limited (alive): {body[:80]}"
        return False, f"HTTP {e.code}: {body[:160]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def refresh_auth_file(path: Path, proxy: str | None) -> tuple[bool, str, dict | None]:
    # 正式项目用 cpa_xai；试验目录遗留 oidc_mint/cpa 仅作兜底
    try:
        from cpa_xai.oauth_device import refresh_access_token
        from cpa_xai import schema as cpa_schema
    except Exception:
        try:
            from oidc_mint.oauth_device import refresh_access_token  # type: ignore
            import cpa as cpa_schema  # type: ignore
        except Exception as e:
            return False, f"import refresh backend: {e}", None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"read: {e}", None
    refresh = str(data.get("refresh_token") or "").strip()
    email = str(data.get("email") or "")
    if not refresh:
        return False, "no refresh_token", data
    try:
        tokens = refresh_access_token(refresh, proxy=proxy)
        build = getattr(cpa_schema, "build_cpa_xai_auth", None) or getattr(
            cpa_schema, "build_auth", None
        )
        if build is None:
            # 最小写回
            payload = dict(data)
            payload["access_token"] = tokens.access_token
            payload["refresh_token"] = tokens.refresh_token
            payload["expires_in"] = getattr(tokens, "expires_in", data.get("expires_in", 21600))
            if getattr(tokens, "id_token", None):
                payload["id_token"] = tokens.id_token
        else:
            base_url = str(
                data.get("base_url")
                or getattr(cpa_schema, "CLI_BASE_URL", "https://cli-chat-proxy.grok.com/v1")
            )
            try:
                payload = build(
                    email=email,
                    access_token=tokens.access_token,
                    refresh_token=tokens.refresh_token,
                    id_token=getattr(tokens, "id_token", None),
                    expires_in=getattr(tokens, "expires_in", 21600),
                    base_url=base_url,
                )
            except TypeError:
                payload = build(
                    email=email,
                    access_token=tokens.access_token,
                    refresh_token=tokens.refresh_token,
                    expires_in=getattr(tokens, "expires_in", 21600),
                )
        for k in ("type", "auth_kind"):
            if k in data:
                payload.setdefault(k, data[k])
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True, "refreshed", payload
    except Exception as e:
        return False, f"refresh: {e}", data


def sync_cli_live_dir(live_items: list[Path], live_dir: Path, auth_dir: Path | None = None) -> None:
    """把健康 auth 同步到 CLI 热目录，实现无感切换。

    若 live_dir 与 auth_dir 是同一目录（本项目默认），只写 index，禁止删号。
    """
    live_dir.mkdir(parents=True, exist_ok=True)
    same = False
    try:
        if auth_dir is not None and live_dir.resolve() == Path(auth_dir).resolve():
            same = True
    except Exception:
        same = str(live_dir) == str(auth_dir)

    if not same:
        # 独立 hotload 目录：只保留 live 列表
        keep = {p.name for p in live_items}
        for old in live_dir.glob("xai-*.json"):
            if old.name not in keep:
                try:
                    old.unlink()
                except Exception:
                    pass
        for src in live_items:
            try:
                shutil.copy2(src, live_dir / src.name)
            except Exception:
                pass
    # 写状态清单，给 CLI/面板读
    index = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(live_items),
        "files": [p.name for p in live_items],
        "same_as_auth_dir": same,
    }
    (live_dir / "pool_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def quarantine(path: Path, dead_dir: Path, reason: str) -> None:
    dead_dir.mkdir(parents=True, exist_ok=True)
    dest = dead_dir / path.name
    try:
        shutil.move(str(path), str(dest))
    except Exception:
        try:
            shutil.copy2(path, dest)
            path.unlink()
        except Exception:
            pass
    meta = dead_dir / (path.stem + ".reason.txt")
    try:
        meta.write_text(
            f"{datetime.now(timezone.utc).isoformat()} {reason}\n", encoding="utf-8"
        )
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="号池健康检查 / 刷新 / CLI 同步")
    parser.add_argument("--refresh-all", action="store_true", help="不管是否临期都刷新")
    parser.add_argument("--probe", action="store_true", help="对 access_token 做 /models 探测")
    parser.add_argument(
        "--refresh-within-hours",
        type=float,
        default=None,
        help="access 在 N 小时内过期则刷新（默认读 config 或 2）",
    )
    parser.add_argument("--no-sync-cli", action="store_true", help="不同步到 cli_live 目录")
    parser.add_argument("--min-live", type=int, default=None, help="健康号低于此值返回退出码 2")
    args = parser.parse_args()

    cfg = load_cfg()
    auth_dir = Path(str(cfg.get("cpa_auth_dir") or "./cpa_auths"))
    if not auth_dir.is_absolute():
        auth_dir = (ROOT / auth_dir).resolve()
    # 正式项目死号目录
    dead_dir = ROOT / "cpa_auths_dead"
    if not dead_dir.is_dir():
        dead_dir = auth_dir / "dead"
    # hotload 即 cpa_auths（CLIProxy auth-dir 同此）
    live_dir = Path(
        str(
            cfg.get("cli_live_dir")
            or cfg.get("cpa_hotload_dir")
            or cfg.get("cpa_auth_dir")
            or "./cpa_auths"
        )
    )
    if not live_dir.is_absolute():
        live_dir = (ROOT / live_dir).resolve()

    refresh_hours = args.refresh_within_hours
    if refresh_hours is None:
        refresh_hours = float(cfg.get("pool_refresh_within_hours", 2) or 2)
    min_live = args.min_live
    if min_live is None:
        min_live = int(cfg.get("pool_min_live", 5) or 5)
    do_probe = args.probe or bool(cfg.get("pool_probe_on_health", True))

    proxy = resolve_proxy(cfg)
    now = datetime.now(timezone.utc)
    files = sorted(auth_dir.glob("xai-*.json"))
    print(f"[*] auth_dir={auth_dir} files={len(files)} proxy={proxy or 'direct'}")
    print(f"[*] refresh_within={refresh_hours}h probe={do_probe} live_dir={live_dir}")

    live: list[Path] = []
    stats = {
        "total": len(files),
        "refreshed": 0,
        "refresh_fail": 0,
        "probe_ok": 0,
        "probe_fail": 0,
        "quarantined": 0,
        "skipped": 0,
    }

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[!] 坏文件 quarantine: {path.name} {e}")
            quarantine(path, dead_dir, f"bad_json: {e}")
            stats["quarantined"] += 1
            continue

        email = str(data.get("email") or path.name)
        exp = parse_expired(str(data.get("expired") or ""))
        need_refresh = args.refresh_all
        if exp is None:
            need_refresh = True
        elif exp <= now:
            need_refresh = True
        elif exp <= now + timedelta(hours=refresh_hours):
            need_refresh = True

        # 大批量号池：先 probe，活号且未临期则跳过 refresh；probe 失败再 refresh
        access = str(data.get("access_token") or "")
        base_url = str(data.get("base_url") or "https://cli-chat-proxy.grok.com/v1")
        probe_ok = False
        probe_reason = ""
        if do_probe and access and not args.refresh_all:
            probe_ok, probe_reason = probe_access(access, base_url, proxy)
            if probe_ok and not need_refresh:
                stats["probe_ok"] += 1
                stats["skipped"] += 1
                print(f"[+] probe OK    {email}: {probe_reason}")
                live.append(path)
                continue
            if probe_ok and need_refresh:
                # 临期但仍可用：尝试 refresh，失败仍可先留用
                pass
            elif not probe_ok:
                stats["probe_fail"] += 1
                need_refresh = True

        if need_refresh:
            ok, msg, new_data = refresh_auth_file(path, proxy)
            if ok:
                stats["refreshed"] += 1
                print(f"[+] refresh OK  {email}: {msg}")
                data = new_data or data
                access = str(data.get("access_token") or access)
                time.sleep(random.uniform(0.05, 0.25))
                if do_probe:
                    probe_ok, probe_reason = probe_access(access, base_url, proxy)
                    if probe_ok:
                        stats["probe_ok"] += 1
                        print(f"[+] probe OK    {email}: {probe_reason}")
                        live.append(path)
                    else:
                        stats["probe_fail"] += 1
                        print(f"[!] probe FAIL  {email}: {probe_reason}")
                        low = probe_reason.lower()
                        if "401" in low or "403" in low or "invalid" in low or "expired" in low:
                            quarantine(path, dead_dir, probe_reason)
                            stats["quarantined"] += 1
                        else:
                            live.append(path)
                else:
                    live.append(path)
            else:
                stats["refresh_fail"] += 1
                print(f"[!] refresh FAIL {email}: {msg}")
                quarantine(path, dead_dir, msg)
                stats["quarantined"] += 1
                continue
        else:
            stats["skipped"] += 1
            if do_probe and not probe_ok and access:
                probe_ok, probe_reason = probe_access(access, base_url, proxy)
                if probe_ok:
                    stats["probe_ok"] += 1
                    live.append(path)
                else:
                    stats["probe_fail"] += 1
                    low = probe_reason.lower()
                    if "401" in low or "403" in low or "invalid" in low or "expired" in low:
                        quarantine(path, dead_dir, probe_reason)
                        stats["quarantined"] += 1
                    else:
                        live.append(path)
            else:
                live.append(path)

    if not args.no_sync_cli:
        sync_cli_live_dir(live, live_dir, auth_dir=auth_dir)
        print(f"[*] CLI live synced: {len(live)} -> {live_dir}")

    # 写健康报告
    report = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": stats,
        "live_count": len(live),
        "live_files": [p.name for p in live],
        "min_live": min_live,
        "need_refill": len(live) < min_live,
    }
    (auth_dir / "pool_health_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"[*] done total={stats['total']} live={len(live)} refreshed={stats['refreshed']} "
        f"refresh_fail={stats['refresh_fail']} quarantined={stats['quarantined']} "
        f"need_refill={report['need_refill']}"
    )
    if len(live) < min_live:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
