#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号哈希出口绑定：按 email 哈希将 CPA 账号固定到 per-auth listener 端口。

用法：
  python scripts/cpa_egress_bind.py              # dry-run，预览分配
  python scripts/cpa_egress_bind.py --apply       # 实际写入 CPA 文件
  python scripts/cpa_egress_bind.py --emit-listeners   # 打印 mihomo listeners: YAML
  python scripts/cpa_egress_bind.py --verify      # 验证 4 个出口 IP 是否互异
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"

DEFAULT_PORTS = [7911, 7912, 7913, 7914]
CLASH_API_BASE = "http://127.0.0.1:9097"
VERIFY_URL = "https://ifconfig.me"
FETCH_TIMEOUT = 10


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[egress-bind] error loading {CONFIG}: {e}")
        return {}


def _clash_api_get(path: str, secret: str) -> dict | None:
    """调用 Clash API GET 端点。"""
    url = f"{CLASH_API_BASE}{path}"
    req = Request(url, headers={"Authorization": f"Bearer {secret}"})
    try:
        with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[egress-bind] Clash API error {path}: {e}")
        return None


def _email_hash_port(email: str, num_ports: int) -> int:
    """sha1(email) % num_ports，稳定映射。"""
    h = int(hashlib.sha1(email.encode("utf-8")).hexdigest(), 16)
    return h % max(num_ports, 1)


def _load_cpa_files(auth_dir: Path) -> list[dict]:
    """读取所有 cpa_auths/xai-*.json，返回 [{path, email, data}]。"""
    results = []
    if not auth_dir.is_dir():
        return results
    for f in sorted(auth_dir.glob("xai-*.json")):
        try:
            raw = f.read_text(encoding="utf-8")
            data = json.loads(raw)
            email = (data.get("email") or f.stem.replace("xai-", "") or "").strip()
            if not email:
                email = f.stem.replace("xai-", "")
            results.append({"path": f, "email": email, "data": data})
        except Exception:
            pass
    return results


def _assign_ports(
    entries: list[dict],
    ports: list[int],
) -> tuple[dict[str, list[str]], list[dict]]:
    """为每个 email 分配端口，返回 (bind_counts, updates)。

    updates 中每项: {path, email, port, old_proxy, new_proxy, action}
    """
    n = len(ports)
    counts: dict[str, list[str]] = {str(p): [] for p in ports}
    updates: list[dict] = []

    for entry in entries:
        email = entry["email"]
        data = entry["data"]
        path = entry["path"]
        idx = _email_hash_port(email, n)
        assigned_port = ports[idx]
        new_proxy = f"http://127.0.0.1:{assigned_port}"

        old_proxy = str(data.get("proxy") or "").strip()

        if old_proxy == new_proxy:
            counts[str(assigned_port)].append(email)
            continue

        action = "skip"
        if not old_proxy:
            action = "add"
        elif "7897" in old_proxy:
            action = "change-from-default"
        else:
            action = "change-from-other"

        updates.append({
            "path": path,
            "email": email,
            "port": assigned_port,
            "old_proxy": old_proxy,
            "new_proxy": new_proxy,
            "action": action,
        })
        counts[str(assigned_port)].append(email)

    return counts, updates


def _pick_cdn_nodes(secret: str, ports: list[int]) -> list[dict]:
    """从 Clash API 获取节点列表，选 4 个不同 cdn 节点供 listener 使用。"""
    proxies_data = _clash_api_get("/proxies", secret)
    if not proxies_data or "proxies" not in proxies_data:
        print("[egress-bind] cannot fetch proxies from Clash API")
        return []

    # 筛选 cdn 节点
    cdn_nodes = []
    for name, info in proxies_data["proxies"].items():
        if isinstance(info, dict):
            name_lower = name.lower()
            if "cdn" in name_lower or "zhuguang" in name_lower:
                node_type = info.get("type", "")
                if node_type in ("Vless", "VMess", "Shadowsocks", "Trojan", "Hysteria2", "Vmess"):
                    cdn_nodes.append(name)

    cdn_nodes = sorted(set(cdn_nodes))
    if not cdn_nodes:
        # 降级：取所有 proxy 类型节点
        for name, info in proxies_data["proxies"].items():
            if isinstance(info, dict):
                node_type = info.get("type", "")
                if node_type in ("Vless", "VMess", "Shadowsocks", "Trojan", "Hysteria2", "Vmess", "Direct"):
                    cdn_nodes.append(name)
        cdn_nodes = sorted(set(cdn_nodes))

    # 尽量分散到不同 cdn-X 后缀
    result = []
    used_names = set()
    n_ports = len(ports)

    for i in range(1, n_ports + 1):
        candidates = [n for n in cdn_nodes if f"cdn-{i}" in n.lower() and n not in used_names]
        if candidates:
            chosen = candidates[0]
            result.append({"port": ports[i - 1], "node": chosen})
            used_names.add(chosen)
        else:
            remaining = [n for n in cdn_nodes if n not in used_names]
            if remaining:
                chosen = remaining[0]
                result.append({"port": ports[i - 1], "node": chosen})
                used_names.add(chosen)

    for p in ports:
        if len(result) >= n_ports:
            break
        if not any(r["port"] == p for r in result):
            remaining = [n for n in cdn_nodes if n not in used_names]
            if remaining:
                chosen = remaining[0]
                result.append({"port": p, "node": chosen})
                used_names.add(chosen)
            else:
                result.append({"port": p, "node": cdn_nodes[0] if cdn_nodes else "(none)"})

    return result


def _emit_listeners_yaml(node_assignments: list[dict]) -> str:
    """生成 mihomo listeners: YAML 片段。"""
    NL = chr(10)
    lines = ["# 以下 listeners: 片段用于 Clash Verge 配置，请手动粘贴到对应位置", "listeners:"]
    for item in node_assignments:
        port = item["port"]
        node = item["node"]
        lines.append(f"  - name: \"cpa-egress-{port}\"")
        lines.append("    type: http")
        lines.append('    listen: "127.0.0.1"')
        lines.append(f"    port: {port}")
        lines.append(f'    proxy: "{node}"')
    return NL.join(lines)


def _verify_egress(ports: list[int]) -> int:
    """验证每个端口出口 IP 是否可通且互异。"""
    results = {}
    all_ok = True
    for port in ports:
        proxy_url = f"http://127.0.0.1:{port}"
        try:
            req = Request(VERIFY_URL, headers={"User-Agent": "curl/8.0"})
            import urllib.request as ureq
            handler = ureq.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = ureq.build_opener(handler)
            with opener.open(req, timeout=FETCH_TIMEOUT) as resp:
                ip = (resp.read().decode("utf-8") or "").strip()
                results[str(port)] = {"ip": ip, "ok": True}
                print(f"[egress-bind] verify port={port} -> {ip}")
        except Exception as e:
            results[str(port)] = {"ip": None, "ok": False, "error": str(e)[:80]}
            print(f"[egress-bind] verify port={port} -> FAIL: {e}")
            all_ok = False

    ips = [r["ip"] for r in results.values() if r.get("ok") and r.get("ip")]
    if len(ips) != len(set(ips)):
        print(f"[egress-bind] WARN: some ports share the same IP ({len(set(ips))}/{len(ports)} unique)")
        all_ok = False
    elif len(ips) == len(ports):
        print(f"[egress-bind] OK: all {len(ports)} ports have distinct exit IPs")
    else:
        print(f"[egress-bind] {len(ips)}/{len(ports)} ports reachable")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="账号哈希出口绑定：按 email 哈希固定 CPA 账号出站端口",
    )
    parser.add_argument(
        "--ports",
        default=",".join(str(p) for p in DEFAULT_PORTS),
        help=f"绑定端口列表，逗号分隔（默认 {','.join(str(p) for p in DEFAULT_PORTS)}）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入 CPA 文件（默认 dry-run）",
    )
    parser.add_argument(
        "--emit-listeners",
        action="store_true",
        help="打印 mihomo listeners YAML 片段到 stdout",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="验证各端口出口 IP 是否通且互异",
    )
    args = parser.parse_args(argv)

    ports = [int(p.strip()) for p in args.ports.split(",") if p.strip().isdigit()]
    if not ports:
        print("[egress-bind] error: no valid ports specified")
        return 1

    cfg = load_config()
    if not cfg:
        return 1

    secret = str(cfg.get("clash_secret") or "").strip()
    auth_dir_str = str(cfg.get("cpa_auth_dir") or "cpa_auths")
    auth_dir = ROOT / auth_dir_str

    entries = _load_cpa_files(auth_dir)
    if not entries:
        print(f"[egress-bind] no CPA files found in {auth_dir}")
        return 0

    # -- dry-run / apply：分配端口 ---------------------------------
    if not args.emit_listeners and not args.verify:
        counts, updates = _assign_ports(entries, ports)

        total = len(entries)
        changed = len(updates)
        skipped = total - changed
        print(f"[egress-bind] CPA files: {total}, changed: {changed}, skipped: {skipped}")
        print(f"[egress-bind] port distribution:")
        for p in ports:
            emails = counts.get(str(p), [])
            print(f"  port {p}: {len(emails)} accounts")

        if args.apply:
            written = 0
            for upd in updates:
                fpath = upd["path"]
                try:
                    data = json.loads(fpath.read_text(encoding="utf-8"))
                    data["proxy"] = upd["new_proxy"]
                    tmp = fpath.with_suffix(fpath.suffix + ".tmp")
                    tmp.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False) + chr(10),
                        encoding="utf-8",
                    )
                    tmp.replace(fpath)
                    written += 1
                except Exception as e:
                    print(f"[egress-bind] error writing {fpath.name}: {e}")
            print(f"[egress-bind] applied: {written} files updated")
        else:
            print("[egress-bind] dry-run mode (use --apply to write)")
            for upd in updates[:10]:
                action_label = {
                    "add": "new bind",
                    "change-from-default": "replace 7897",
                    "change-from-other": "replace dead port",
                }.get(upd["action"], upd["action"])
                print(f"  {upd['email'][:30]:30s} -> :{upd['port']} ({action_label})")

        return 0

    # --emit-listeners -------------------------------------------
    if args.emit_listeners:
        node_assignments = _pick_cdn_nodes(secret, ports)
        if not node_assignments:
            print("[egress-bind] cannot assign nodes; check Clash API connectivity")
            return 1
        yaml = _emit_listeners_yaml(node_assignments)
        print(yaml)
        return 0

    # --verify ---------------------------------------------------
    if args.verify:
        return _verify_egress(ports)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())