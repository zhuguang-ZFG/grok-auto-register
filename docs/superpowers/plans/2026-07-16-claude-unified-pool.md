# Claude Code 本地+远端统一池 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Claude Code / cc-switch 只认一个入口；多家真 Claude 反代在 CLIProxy 内自动 hop，不中断、不手动切 provider；GLM Anthropic 兼容仅作显式降级。

**Architecture:** 复用社区 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 的 **`claude-api-key`** 多凭据池（失败换 key / 换 base-url），与本仓已上线的 Codex `:8327` / Grok `:8317` **同构但分端口**。客户端固定 `ANTHROPIC_BASE_URL=http://127.0.0.1:8337` + 本地 token。cc-switch 只保留/切换 `claude-unified`。默认池 **只挂真 Claude 反代**；智谱 GLM 用独立模型名或最后一跳，避免默默降智。

**Tech Stack:** CLIProxyAPI（`D:/cli-proxy-api`）、cc-switch SQLite（`app_type=claude`）、`scripts/claude_code_start.ps1` 模式、Python 探活、Clash `:7897`。

## Global Constraints

- 客户端日常 **只** 使用一个 `ANTHROPIC_BASE_URL` + 一个 `ANTHROPIC_AUTH_TOKEN`；禁止用 cc-switch 多 Claude 卡片假装 failover。
- **三池分端口（硬）**：Grok `8317` · Codex `8327` · Claude **`8337`**。禁止把 Claude 凭据写进 `config.yaml` / `config-codex.yaml`。
- 默认统一池 **只放真 Claude 反代**（100xlabs / AnyRouter / 胜钱帮等）；**GLM 不进默认 opus alias**（可单独 `glm-5.2` 或文档标明 last-resort）。
- 密钥只落在 `D:/cli-proxy-api/config-claude.yaml`，**勿 commit** 到 grok-auto-register。
- 探活失败的站不进池（`disabled` 或根本不写）。
- Claude Code 走 **Anthropic Messages** 协议；用 CLIProxy `claude-api-key`，不要假设 OpenAI `/chat/completions` 能直接喂 CC。
- 改完 `.py` 跑 `python -m py_compile`；git 提交须用户明确同意。
- 不恢复 PostToolUse 写后验证 hooks；不碰 xAI 判死逻辑。

## 社区对照

| 社区实践 | 本计划 |
|----------|--------|
| CLIProxy `claude-api-key` 多 base-url + 失败换凭据 | `config-claude.yaml` 多条目 |
| 客户端一个 endpoint | cc-switch `claude-unified` → `:8337` |
| Claude Code 伪装 / cloak | 对 CC 客户端 `cloak.mode: never` 或默认 auto（CC 不 cloak） |
| 空流/未识别错误可能不 hop | 探活用真实 `/v1/messages`；监控首包失败站 disabled |
| 与 Codex 统一池同构 | 文档/脚本镜像 `CODEX_UNIFIED_POOL` |

## 现状（实现前快照）

| Provider（cc-switch claude） | BASE | 备注 |
|------------------------------|------|------|
| 100xlabs / Sub2API（多条） | `https://sub.100xlabs.space` | 真 Claude 反代（Kiro） |
| 胜钱帮 | `https://k40.shengqainbang.cn` | 真 Claude 反代 |
| AnyRouter | `https://a-ocnfniawgw.cn-shanghai.fcapp.run` | 真 Claude 反代 |
| **当前 current** GLM5.2 团队 | `https://open.bigmodel.cn/api/anthropic` | **GLM 降级，不是 Opus 真身** |
| cc-switch `proxy_config` claude `auto_failover` | **0（关）** | 不依赖 GUI failover 作主路径 |

## File Map

| Path | Role |
|------|------|
| `scripts/probe_claude_upstreams.py` | 探活各 ANTHROPIC_BASE_URL + token（`/v1/messages`） |
| `logs/claude_upstream_probe.json` | 探活产物 |
| `D:/cli-proxy-api/config-claude.yaml` | Claude 专用 CLIProxy（密钥，仓外） |
| `D:/cli-proxy-api/start-claude.bat` | 拉起 `:8337` |
| `scripts/cc_upsert_claude_unified.py` | 写入 cc-switch `claude-unified` |
| `scripts/cc_switch_claude_provider.py` | 切换 claude provider（镜像 codex 脚本；schema v13） |
| `scripts/claude_unified.ps1` | 清 env + switch + 启动 claude |
| `docs/CLAUDE_UNIFIED_POOL.md` | 运维说明 |
| `docs/CODEX_CLAUDE_OPS.md` | 追加 Claude 统一入口 |
| `docs/COMMUNITY_THICKEN.md` | 一行对照 |

---

### Task 1: 探活矩阵（真 Claude 反代 + 可选 GLM）

**Files:**
- Create: `scripts/probe_claude_upstreams.py`
- Create: `logs/claude_upstream_probe.json`
- Test: `python scripts/probe_claude_upstreams.py --dry-print`

**Interfaces:**
- Consumes: cc-switch `providers` where `app_type='claude'` 的 `settings_config.env`（`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`）
- Produces: `[{name, base_url, messages_ok, model_used, error, recommend, is_glm}]`

- [ ] **Step 1: 写探活脚本**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe cc-switch Claude providers via Anthropic /v1/messages."""
from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
OUT = Path(r"D:/Users/grok-auto-register/logs/claude_upstream_probe.json")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Prefer real Claude ids first; GLM last
MODEL_CANDIDATES = [
    "claude-opus-4-8",
    "claude-opus-4-8[1M]",
    "claude-opus-4-8[1m]",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-5",
    "glm-5.2",
    "glm-5.1",
]


def load_providers() -> list[dict]:
    c = sqlite3.connect(str(DB))
    rows = c.execute(
        "SELECT id, name, settings_config FROM providers WHERE app_type='claude'"
    ).fetchall()
    c.close()
    out: list[dict] = []
    for pid, name, sc in rows:
        try:
            j = json.loads(sc or "{}")
        except Exception:
            continue
        env = j.get("env") or {}
        if not isinstance(env, dict):
            continue
        base = str(env.get("ANTHROPIC_BASE_URL") or "").strip().rstrip("/")
        key = str(
            env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or ""
        ).strip()
        model = str(env.get("ANTHROPIC_MODEL") or "").strip()
        if not base or not key:
            continue
        out.append(
            {
                "id": str(pid),
                "name": str(name),
                "base_url": base,
                "api_key": key,
                "preferred_model": model,
            }
        )
    return out


def messages_url(base: str) -> str:
    b = base.rstrip("/")
    if b.endswith("/v1"):
        return f"{b}/messages"
    if b.endswith("/messages"):
        return b
    # bigmodel anthropic path already ends with /api/anthropic
    return f"{b}/v1/messages"


def post_messages(base: str, key: str, model: str, timeout: float = 45.0):
    url = messages_url(base)
    body = {
        "model": model,
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
    }
    data = json.dumps(body).encode("utf-8")
    headers = {
        "x-api-key": key,
        "Authorization": f"Bearer {key}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "User-Agent": UA,
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw[:300]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]
    except Exception as e:
        return 0, str(e)


def extract_text(body) -> str:
    if not isinstance(body, dict):
        return str(body)[:120]
    content = body.get("content")
    if isinstance(content, list) and content:
        block = content[0]
        if isinstance(block, dict):
            return str(block.get("text") or "")[:80]
    return str(body)[:120]


def probe_one(entry: dict) -> dict:
    base = entry["base_url"]
    key = entry["api_key"]
    is_glm = "bigmodel.cn" in base or "glm" in (entry.get("preferred_model") or "").lower()
    models: list[str] = []
    if entry.get("preferred_model"):
        models.append(entry["preferred_model"])
    for m in MODEL_CANDIDATES:
        if m not in models:
            models.append(m)
    last_code, last_body, used = 0, None, None
    ok = False
    for m in models:
        code, body = post_messages(base, key, m)
        last_code, last_body, used = code, body, m
        if code == 200 and "PONG" in extract_text(body).upper() or (
            code == 200 and isinstance(body, dict) and body.get("content")
        ):
            ok = True
            break
        # auth hard fail — stop trying models
        if code in (401, 403):
            break
    return {
        "name": entry["name"],
        "id": entry["id"],
        "base_url": base,
        "is_glm": is_glm,
        "messages_http": last_code,
        "messages_ok": ok,
        "model_used": used if ok else None,
        "recommend": ok and not is_glm,  # default pool: real Claude only
        "recommend_glm_fallback": ok and is_glm,
        "error_snippet": None if ok else str(last_body)[:180],
        "api_key": key if ok else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-print", action="store_true")
    ap.add_argument("--include-keys", action="store_true")
    args = ap.parse_args()
    entries = load_providers()
    # de-dup by base_url+key prefix
    seen: set[str] = set()
    uniq: list[dict] = []
    for e in entries:
        k = e["base_url"] + "|" + e["api_key"][:16]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    if args.dry_print:
        print(
            json.dumps(
                [{k: v for k, v in e.items() if k != "api_key"} for e in uniq],
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    results = [probe_one(e) for e in uniq]
    out = []
    for r in results:
        row = dict(r)
        if not args.include_keys:
            row.pop("api_key", None)
        out.append(row)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        kind = "GLM" if r["is_glm"] else "CLD"
        mark = "OK" if r["messages_ok"] else "NO"
        pool = "POOL" if r["recommend"] else ("GLM" if r["recommend_glm_fallback"] else "—")
        print(
            f"[{mark}/{pool}][{kind}] {r['name']} http={r['messages_http']} "
            f"model={r.get('model_used')} {r['base_url']}"
        )
        if r.get("error_snippet"):
            print(f"      {r['error_snippet']}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 编译并探活**

```bash
python -m py_compile scripts/probe_claude_upstreams.py
python scripts/probe_claude_upstreams.py --include-keys
```

Expected: 打印 `[OK/POOL][CLD]` / `[OK/GLM][GLM]` / `[NO/—]`；写出 `logs/claude_upstream_probe.json`。

- [ ] **Step 3: 定入池名单**

- `recommend=true` → 写入 `claude-api-key` 默认池  
- `recommend_glm_fallback=true` → **可选** 单独条目 + 文档 last-resort，默认 `disabled: true`  
- `messages_ok=false` → 不写  

记录每个 OK 站的 **实际上游 model 名**（`model_used`），供 Task 2 alias 映射。

---

### Task 2: CLIProxy Claude 配置 `:8337`

**Files:**
- Create: `D:/cli-proxy-api/config-claude.yaml`（仓外）
- Create: `D:/cli-proxy-api/start-claude.bat`
- Create: `docs/CLAUDE_UNIFIED_POOL.md`（无密钥）

**Interfaces:**
- Consumes: Task 1 OK 列表（base_url, api_key, model_used）
- Produces: `http://127.0.0.1:8337` 对外 Anthropic 兼容；客户端模型 `claude-opus-4-8` 等

- [ ] **Step 1: 写 config-claude.yaml 骨架**

实现时用 Task1 真实 key 替换；下列结构为模板（**先只启用探活 OK 的真 Claude 站**）：

```yaml
# Claude unified pool — DO NOT mix with Grok :8317 or Codex :8327
host: "127.0.0.1"
port: 8337

api-keys:
  - "sk-local-claude-unified-2026"

remote-management:
  allow-remote: false

debug: false
logging-to-file: true
error-logs-max-files: 5
commercial-mode: true
usage-statistics-enabled: false

request-retry: 1
max-retry-credentials: 8
max-retry-interval: 5
disable-cooling: false
transient-error-cooldown-seconds: 15

# Claude Code talks Anthropic protocol; cloak auto skips real CC clients
disable-claude-cloak-mode: false

proxy-url: "http://127.0.0.1:7897"

claude-api-key:
  # Example — only include Task1 recommend=true entries
  # - api-key: "<from probe>"
  #   base-url: "https://sub.100xlabs.space"
  #   proxy-url: "http://127.0.0.1:7897"
  #   headers:
  #     User-Agent: "Mozilla/5.0 ..."
  #   models:
  #     - name: "claude-opus-4-8"          # upstream actual id from probe
  #       alias: "claude-opus-4-8"
  #     - name: "claude-opus-4-8[1M]"
  #       alias: "claude-opus-4-8"
  #     - name: "claude-opus-4-7"
  #       alias: "claude-opus-4-7"
  #     - name: "claude-opus-4-6"
  #       alias: "claude-opus-4-6"
  #   cloak:
  #     mode: "auto"

  # GLM last-resort (default disabled)
  # - api-key: "<glm key>"
  #   base-url: "https://open.bigmodel.cn/api/anthropic"
  #   disabled: true   # if CLIProxy supports; else omit entire block until needed
  #   models:
  #     - name: "glm-5.2"
  #       alias: "glm-5.2"

routing:
  strategy: round-robin
  session-affinity: true
  session-affinity-ttl: "2h"
```

注意：CLIProxy 文档写明 **oauth-model-alias 不作用于 claude-api-key**；模型映射用条目内 `models.name/alias`。

- [ ] **Step 2: start-claude.bat**

```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [*] starting CLIProxyAPI Claude unified on 127.0.0.1:8337
cli-proxy-api.exe -config "%~dp0config-claude.yaml"
```

- [ ] **Step 3: 启动并冒烟**

```bash
# 后台: D:\cli-proxy-api\start-claude.bat
curl -s http://127.0.0.1:8337/v1/models \
  -H "Authorization: Bearer sk-local-claude-unified-2026" \
  -H "x-api-key: sk-local-claude-unified-2026"

# Anthropic messages
curl -s http://127.0.0.1:8337/v1/messages \
  -H "Authorization: Bearer sk-local-claude-unified-2026" \
  -H "x-api-key: sk-local-claude-unified-2026" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d "{\"model\":\"claude-opus-4-8\",\"max_tokens\":32,\"messages\":[{\"role\":\"user\",\"content\":\"Reply: PONG\"}]}"
```

Expected: messages 200 且 content 含 PONG（或等价成功 JSON）。

- [ ] **Step 4: 回归 Grok/Codex 端口**

```bash
curl -s http://127.0.0.1:8317/v1/models -H "Authorization: Bearer sk-local-grok-pool-2026" | head -c 100
curl -s http://127.0.0.1:8327/v1/models -H "Authorization: Bearer sk-local-codex-unified-2026" | head -c 100
```

Expected: 两路仍可用。

- [ ] **Step 5: 写 `docs/CLAUDE_UNIFIED_POOL.md`**

镜像 `docs/CODEX_UNIFIED_POOL.md`：拓扑、端口、探活、入池规则、GLM 不进默认、回滚命令。

---

### Task 3: cc-switch `claude-unified` + 切换脚本

**Files:**
- Create: `scripts/cc_switch_claude_provider.py`（list/current/switch，镜像 codex 版）
- Create: `scripts/cc_upsert_claude_unified.py`
- Modify: `C:/Users/zhugu/.cc-switch/cc-switch.db` + `settings.json`（脚本 backup）
- Test: `python scripts/cc_switch_claude_provider.py current`

**Interfaces:**
- Consumes: `:8337` + `sk-local-claude-unified-2026`
- Produces: provider id `claude-unified`，`app_type=claude`，`is_current=1`，env 注入 Claude Code

- [ ] **Step 1: 切换脚本**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Switch Claude provider in cc-switch.db (schema-agnostic; mirrors codex switcher)."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
SETTINGS = Path(r"C:/Users/zhugu/.cc-switch/settings.json")
# Claude Code reads env from provider; optional write to ~/.claude/settings if needed later


def connect() -> sqlite3.Connection:
    if not DB.is_file():
        raise SystemExit(f"missing {DB}")
    return sqlite3.connect(str(DB))


def list_providers() -> list[tuple]:
    c = connect()
    try:
        return c.execute(
            "SELECT id, name, is_current FROM providers WHERE app_type='claude' "
            "ORDER BY is_current DESC, name"
        ).fetchall()
    finally:
        c.close()


def cmd_list(_: argparse.Namespace) -> int:
    for pid, name, cur in list_providers():
        print(f"{'*' if cur else ' '} {pid}  {name}")
    return 0


def cmd_current(_: argparse.Namespace) -> int:
    c = connect()
    try:
        row = c.execute(
            "SELECT id, name, settings_config FROM providers "
            "WHERE app_type='claude' AND is_current=1"
        ).fetchone()
    finally:
        c.close()
    if not row:
        print("no current claude provider")
        return 1
    pid, name, sc = row
    print(f"id:   {pid}")
    print(f"name: {name}")
    try:
        env = (json.loads(sc or "{}").get("env") or {})
        for k in sorted(env):
            if "TOKEN" in k or "KEY" in k:
                v = str(env[k])
                print(f"env:  {k}=len:{len(v)}")
            else:
                print(f"env:  {k}={env[k]}")
    except Exception as e:
        print(f"parse err: {e}")
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    target = str(args.id).strip()
    c = connect()
    try:
        row = c.execute(
            "SELECT id, name, settings_config FROM providers WHERE id=? AND app_type='claude'",
            (target,),
        ).fetchone()
        if not row:
            print(f"provider not found: {target}")
            for pid, name, cur in list_providers():
                print(f"  {pid}  {name}")
            return 1
        pid, name, sc = row
        bak = DB.with_name(f"cc-switch.db.bak-claude-switch-{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(DB, bak)
        print(f"backup {bak}")
        c.execute("UPDATE providers SET is_current=0 WHERE app_type='claude'")
        c.execute(
            "UPDATE providers SET is_current=1 WHERE id=? AND app_type='claude'", (pid,)
        )
        c.commit()
    finally:
        c.close()
    if SETTINGS.is_file():
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
        s["currentProviderClaude"] = pid
        SETTINGS.write_text(
            json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"settings currentProviderClaude={pid}")
    print(f"switched claude -> {pid} ({name})")
    print("restart Claude Code / use claude_unified.ps1 to apply env")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("current")
    p_sw = sub.add_parser("switch")
    p_sw.add_argument("id")
    args = p.parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "current":
        return cmd_current(args)
    if args.cmd == "switch":
        return cmd_switch(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: upsert claude-unified**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Upsert cc-switch claude provider pointing at CLIProxy :8337."""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
PROVIDER_ID = "claude-unified"
NAME = "Claude Unified (multi-relay)"
API_KEY = "sk-local-claude-unified-2026"
BASE = "http://127.0.0.1:8337"

# Claude Code reads these env vars from provider settings_config.env
ENV = {
    "ANTHROPIC_BASE_URL": BASE,
    "ANTHROPIC_AUTH_TOKEN": API_KEY,
    "ANTHROPIC_API_KEY": API_KEY,
    "ANTHROPIC_MODEL": "claude-opus-4-8",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "claude-opus-4-8",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-opus-4-7",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "claude-opus-4-7",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-opus-4-6",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": "claude-opus-4-6",
}
SETTINGS_CONFIG = json.dumps({"env": ENV}, ensure_ascii=False)


def main() -> None:
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = DB.with_name(f"cc-switch.db.bak-claude-unified-{ts}")
    shutil.copy2(DB, bak)
    print("backup", bak)
    c = sqlite3.connect(str(DB))
    cols = [r[1] for r in c.execute("PRAGMA table_info(providers)").fetchall()]
    now = int(time.time() * 1000)
    row = c.execute(
        "SELECT id FROM providers WHERE id=? AND app_type='claude'", (PROVIDER_ID,)
    ).fetchone()
    if row:
        c.execute(
            "UPDATE providers SET name=?, settings_config=? WHERE id=? AND app_type='claude'",
            (NAME, SETTINGS_CONFIG, PROVIDER_ID),
        )
        print("updated", PROVIDER_ID)
    else:
        base = {
            "id": PROVIDER_ID,
            "name": NAME,
            "app_type": "claude",
            "settings_config": SETTINGS_CONFIG,
            "is_current": 0,
            "created_at": now,
            "notes": "",
            "icon": "",
        }
        use = [k for k in base if k in cols]
        c.execute(
            f"INSERT INTO providers ({','.join(use)}) VALUES ({','.join('?' for _ in use)})",
            tuple(base[k] for k in use),
        )
        print("inserted", PROVIDER_ID)
    c.commit()
    c.close()
    print("next: python scripts/cc_switch_claude_provider.py switch claude-unified")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 执行 upsert + switch**

```bash
python -m py_compile scripts/cc_switch_claude_provider.py scripts/cc_upsert_claude_unified.py
python scripts/cc_upsert_claude_unified.py
python scripts/cc_switch_claude_provider.py switch claude-unified
python scripts/cc_switch_claude_provider.py current
```

Expected: `id: claude-unified`，`ANTHROPIC_BASE_URL=http://127.0.0.1:8337`。

- [ ] **Step 4: `scripts/claude_unified.ps1`**

```powershell
# Launch Claude Code via unified CLIProxy :8337
$ErrorActionPreference = "Stop"
Remove-Item Env:ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:ANTHROPIC_BASE_URL -ErrorAction SilentlyContinue

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$root\cc_switch_claude_provider.py" switch claude-unified | Out-Host

# inject from DB (reuse claude_code_start logic inline)
$envJson = python -c @"
import json,sqlite3
c=sqlite3.connect(r'$env:USERPROFILE\.cc-switch\cc-switch.db')
row=c.execute("SELECT settings_config FROM providers WHERE app_type='claude' AND is_current=1").fetchone()
print(row[0] if row else '{}')
"@
$cfg = $envJson | ConvertFrom-Json
if ($cfg.env) {
  $cfg.env.PSObject.Properties | ForEach-Object {
    Set-Item -Path "Env:$($_.Name)" -Value $_.Value
  }
}

try {
  Invoke-WebRequest -Uri "http://127.0.0.1:8337/v1/models" -Headers @{
    Authorization = "Bearer sk-local-claude-unified-2026"
    "x-api-key" = "sk-local-claude-unified-2026"
  } -UseBasicParsing -TimeoutSec 5 | Out-Null
  Write-Host "[ok] claude unified :8337 up"
} catch {
  Write-Host "[warn] start D:\cli-proxy-api\start-claude.bat first"
}

& claude @args
```

也可改为直接调用现有 `claude_code_start.ps1`（switch 之后）——若 `claude_code_start.ps1` 已读 current provider，则：

```powershell
python "$root\cc_switch_claude_provider.py" switch claude-unified
& "$root\claude_code_start.ps1" @args
```

优先 **复用** `claude_code_start.ps1`，避免两套注入逻辑。

---

### Task 4: 端到端验收 + 文档

**Files:**
- Modify: `docs/CODEX_CLAUDE_OPS.md`
- Modify: `docs/COMMUNITY_THICKEN.md`
- Test: curl + `claude -p "Reply: OK"`（若本机有 claude CLI）

- [ ] **Step 1: 冒烟清单**

```bash
curl -s http://127.0.0.1:8337/v1/messages \
  -H "x-api-key: sk-local-claude-unified-2026" \
  -H "Authorization: Bearer sk-local-claude-unified-2026" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d "{\"model\":\"claude-opus-4-8\",\"max_tokens\":32,\"messages\":[{\"role\":\"user\",\"content\":\"Reply: PONG\"}]}"

python scripts/cc_switch_claude_provider.py current
# 回归
curl -s http://127.0.0.1:8317/v1/models -H "Authorization: Bearer sk-local-grok-pool-2026" | head -c 80
curl -s http://127.0.0.1:8327/v1/models -H "Authorization: Bearer sk-local-codex-unified-2026" | head -c 80
```

- [ ] **Step 2: 故障注入（可选）**

临时注释掉 config 中第一家 `claude-api-key`，重启 8337，确认仍能 hop 第二家。

- [ ] **Step 3: 更新文档**

`CODEX_CLAUDE_OPS.md` Claude 段改为：

| 项 | 值 |
|----|-----|
| Provider | `claude-unified` → `http://127.0.0.1:8337` |
| 启动网关 | `D:\cli-proxy-api\start-claude.bat` |
| 启动 CC | `scripts/claude_unified.ps1` 或 switch 后 `claude_code_start.ps1` |
| 回退 GLM | `python scripts/cc_switch_claude_provider.py switch glm52-team-fallback-1784118927115` |
| 回退单站 100xlabs | switch 对应 sub2api id |

`COMMUNITY_THICKEN.md` 增加：

```markdown
| Claude Code 多反代同池 hop | `docs/CLAUDE_UNIFIED_POOL.md` + CLIProxy `:8337` + `claude-unified` |
```

- [ ] **Step 4: 提交（仅仓内文件，须用户同意）**

```bash
git add scripts/probe_claude_upstreams.py \
  scripts/cc_switch_claude_provider.py \
  scripts/cc_upsert_claude_unified.py \
  scripts/claude_unified.ps1 \
  docs/CLAUDE_UNIFIED_POOL.md \
  docs/CODEX_CLAUDE_OPS.md \
  docs/COMMUNITY_THICKEN.md \
  docs/superpowers/plans/2026-07-16-claude-unified-pool.md
git status
```

---

## 明确不做（YAGNI）

- 不打开 cc-switch GUI `auto_failover` 作为主路径（可后续实验，不阻塞本计划）。
- 不把 Grok/Codex 上游并进 Claude 池。
- 不把 GLM 默认映射成 `claude-opus-4-8`（禁止静默降智）。
- 不引入 LiteLLM/New-API 第二套网关。
- 不做 Claude OAuth 号池文件（当前全是 sk 反代；有 OAuth 时另开任务）。

## 风险与回滚

| 风险 | 缓解 |
|------|------|
| 反代只支持 OpenAI 不支持 Anthropic messages | Task1 `/v1/messages` 探活过滤 |
| 模型 id 带 `[1M]` 后缀不一致 | probe `model_used` + alias 多映射 |
| 空流不触发 CLIProxy hop | 已知上游 issue；disabled 坏站 + 多站冗余 |
| 8337 配错进 8317 | 独立 config-claude.yaml + 回归 curl |
| 当前默认 GLM 用户习惯 | 文档写明回退 switch id；切换前 backup DB |

回滚：

```bash
python scripts/cc_switch_claude_provider.py switch glm52-team-fallback-1784118927115
# 或 switch 原 100xlabs provider id
# 停 8337 进程
```

## Self-Review

1. **Spec coverage:** 社区 CLIProxy claude-api-key、分端口、探活、真 Claude 默认/GLM 降级、cc-switch 单入口、文档、回归 Grok/Codex — 均有 Task。  
2. **Placeholder scan:** 密钥从 probe/cc-switch 实取；INSERT 按 PRAGMA 动态列（已写）。  
3. **Type consistency:** provider id `claude-unified`；port `8337`；key `sk-local-claude-unified-2026`；默认 model `claude-opus-4-8`。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-16-claude-unified-pool.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — 每任务新 subagent + review  
2. **Inline Execution** — 本会话按任务连续做  

你要哪种？回复 **1** / **2** /「按计划执行」即可开工。
