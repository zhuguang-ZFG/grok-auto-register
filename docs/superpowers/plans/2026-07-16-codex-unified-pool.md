# Codex 本地+远端统一池 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Codex / cc-switch 只认一个入口；本地 `chatgpt2api` OAuth 号池与远端 ChatGPT/Codex 兼容 sk 在网关内自动 hop，不中断、不手动切 provider。

**Architecture:** 复用社区 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 的 `openai-compatibility` **同 alias 内池**（与本仓 Grok 的 `docs/REMOTE_POOL_SUPPLEMENT.md` 同一模式）。本地 `http://127.0.0.1:8124/v1` 与 lyclaude / sharedchat / zmoon 等远端都作为 **OpenAI 兼容上游**，客户端模型名统一为 `gpt-5.6`（及 sol/luna/terra 别名）。**独立端口**，禁止与 Grok `:8317` 混池。cc-switch 只保留/切换到 `codex-unified`。

**Tech Stack:** CLIProxyAPI（本机 `D:/cli-proxy-api`）、chatgpt2api `:8124`、cc-switch SQLite、`scripts/cc_switch_codex_provider.py`、Python 探活脚本、Clash `:7897`。

## Global Constraints

- 客户端日常 **只** 使用一个 base_url + 模型族 `gpt-5.6*`；禁止用 cc-switch 多 provider 假装 failover。
- **Grok 与 Codex 分池**：Grok 继续 `8317` + `cpa_auths`；Codex 用新端口（建议 `8327`）+ 纯 `openai-compatibility`（本地 8124 也是一条 compat 上游，不塞 xai auth-dir）。
- 远端 **sk- 不得** 写入 `chatgpt2api` 当 OAuth 账号；不得写入 `cpa_auths/`。
- 密钥只落在 `D:/cli-proxy-api/config-codex.yaml`（或等价路径），**勿 commit** 到 grok-auto-register。
- 上游须尽量支持 Codex 常用的 **`responses` / chat 兼容**；探活失败的站 `disabled: true`，不进默认池。
- 本地优先：先 hop `local-k12`，再远端；避免远端抢流量。
- 改完 `.py` 跑 `python -m py_compile`；git 提交须用户明确同意。
- 遵守 AGENTS.md 判死铁律（本计划不碰 xAI RT 判死）。

## 社区对照（本计划对齐什么）

| 社区实践 | 本计划动作 |
|----------|------------|
| CLIProxy 同 alias 多上游，失败 hop 下一凭据 | `openai-compatibility` 多项，`alias: gpt-5.6` |
| 本地 OAuth 与远端 sk 职责分离 | 本地仍 chatgpt2api；远端只做 compat 渠道 |
| 客户端一个 endpoint | cc-switch `codex-unified` → `:8327` |
| 可选 remote-only 调试别名 | `remote-gpt-5.6` 仅调试 |
| 死号/坏上游冷却 | `max-retry-credentials` + 本地 `auto_remove_invalid` + 远端 disabled |
| 不盲导共享包 | 远端先 `probe_codex_upstreams.py` 再写入 config |

## File Map

| Path | Role |
|------|------|
| `D:/cli-proxy-api/config-codex.yaml` | Codex 专用 CLIProxy 配置（新建，密钥在此） |
| `D:/cli-proxy-api/start-codex.bat` / `_start_cliproxy_codex_hidden.vbs` | 独立拉起 `:8327` |
| `scripts/probe_codex_upstreams.py` | 探活远端 + 本地 8124 |
| `scripts/cc_upsert_codex_unified.py` | 写入/更新 cc-switch provider `codex-unified` |
| `scripts/k12_prioritize_rt.py`（可选） | 无 RT k12 降权/软禁，减少 401 首跳 |
| `docs/CODEX_UNIFIED_POOL.md` | 运维说明（对齐 REMOTE_POOL_SUPPLEMENT） |
| `docs/CODEX_CLAUDE_OPS.md` | 追加统一入口与启动方式 |
| `docs/COMMUNITY_THICKEN.md` | 一行对照：Codex 混池已落地 |
| `scripts/codex_unified.ps1` | 清 env 后走 unified 的 Codex 启动器 |

---

### Task 1: 探活矩阵（本地 + 现有 cc-switch 远端）

**Files:**
- Create: `scripts/probe_codex_upstreams.py`
- Create: `logs/codex_upstream_probe.json`（运行产物，gitignore 已有 logs/ 则不动）
- Test: 脚本自检 `python scripts/probe_codex_upstreams.py --dry-print`

**Interfaces:**
- Consumes: cc-switch DB 中 `app_type=codex` 的 `settings_config` base_url + key；固定本地 `http://127.0.0.1:8124/v1` + `k12-pool-local`
- Produces: JSON 报告字段 `[{name, base_url, models_ok, chat_ok, responses_hint, error, recommend}]`

- [ ] **Step 1: 写探活脚本**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe local chatgpt2api + cc-switch codex remotes for unified pool eligibility."""
from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
LOCAL = {
    "name": "local-k12",
    "base_url": "http://127.0.0.1:8124/v1",
    "api_key": "k12-pool-local",
}
OUT = Path(r"D:/Users/grok-auto-register/logs/codex_upstream_probe.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def load_from_cc_switch() -> list[dict]:
    if not DB.is_file():
        return []
    c = sqlite3.connect(str(DB))
    rows = c.execute(
        "SELECT id, name, settings_config FROM providers WHERE app_type='codex'"
    ).fetchall()
    c.close()
    out = []
    for pid, name, sc in rows:
        try:
            j = json.loads(sc or "{}")
        except Exception:
            continue
        key = str((j.get("auth") or {}).get("OPENAI_API_KEY") or "").strip()
        cfg = str(j.get("config") or "")
        base = ""
        for line in cfg.splitlines():
            s = line.strip()
            if s.startswith("base_url"):
                # base_url = "https://..."
                if "=" in s:
                    base = s.split("=", 1)[1].strip().strip('"').strip("'")
        if not base or "127.0.0.1:8124" in base or "8317" in base:
            # skip local k12 (added separately) and grok pool
            if "8317" in base or "grok" in (name or "").lower():
                continue
            if "127.0.0.1:8124" in base:
                continue
        if not base or not key:
            continue
        if not base.rstrip("/").endswith("/v1") and "/codex" not in base:
            # keep as-is; some hosts use /codex without /v1
            pass
        out.append({"name": str(name), "id": str(pid), "base_url": base, "api_key": key})
    return out


def http_json(url: str, key: str, body: dict | None = None, timeout: float = 25.0):
    data = None
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": UA,
        "Accept": "application/json",
    }
    method = "GET"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw[:200]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return e.code, raw[:300]
    except Exception as e:
        return 0, str(e)


def probe_one(entry: dict) -> dict:
    base = entry["base_url"].rstrip("/")
    key = entry["api_key"]
    # models
    m_url = base if base.endswith("/models") else f"{base}/models"
    if base.endswith("/v1"):
        m_url = f"{base}/models"
    elif "/codex" in base and not base.endswith("/v1"):
        m_url = f"{base.rstrip('/')}/models"  # best-effort
    code_m, body_m = http_json(m_url, key)
    models_ok = code_m == 200
    # chat
    chat_url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/chat/completions"
    code_c, body_c = http_json(
        chat_url,
        key,
        {
            "model": "gpt-5.6",
            "messages": [{"role": "user", "content": "Reply: PONG"}],
            "max_tokens": 8,
        },
    )
    chat_ok = code_c == 200
    if not chat_ok:
        # try sol alias
        code_c2, body_c2 = http_json(
            chat_url,
            key,
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Reply: PONG"}],
                "max_tokens": 8,
            },
        )
        if code_c2 == 200:
            chat_ok = True
            code_c, body_c = code_c2, body_c2
    recommend = models_ok or chat_ok
    return {
        "name": entry["name"],
        "base_url": base,
        "models_http": code_m,
        "chat_http": code_c,
        "models_ok": models_ok,
        "chat_ok": chat_ok,
        "recommend": recommend,
        "error_snippet": None if chat_ok else str(body_c)[:180],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-print", action="store_true")
    args = ap.parse_args()
    entries = [LOCAL] + load_from_cc_switch()
    # de-dup by base_url
    seen = set()
    uniq = []
    for e in entries:
        b = e["base_url"].rstrip("/")
        if b in seen:
            continue
        seen.add(b)
        uniq.append(e)
    if args.dry_print:
        print(json.dumps([{k: v for k, v in e.items() if k != "api_key"} for e in uniq], indent=2))
        return 0
    results = [probe_one(e) for e in uniq]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        mark = "OK" if r["recommend"] else "NO"
        print(f"[{mark}] {r['name']} models={r['models_http']} chat={r['chat_http']} {r['base_url']}")
        if r.get("error_snippet"):
            print(f"      {r['error_snippet']}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 语法检查并跑探活**

```bash
python -m py_compile scripts/probe_codex_upstreams.py
python scripts/probe_codex_upstreams.py
```

Expected: 打印每条 `[OK]/[NO]`，写出 `logs/codex_upstream_probe.json`。本地 8124 至少应 `recommend=true`（若 chat 401，仍记 models/health，进入 Task 2 先治本地池）。

- [ ] **Step 3: 记录入池名单**

根据 JSON，列出 `recommend=true` 的 `name/base_url` 供 Task 3 写入 config。`recommend=false` 的站不进默认 alias，可留 `disabled: true` 注释。

---

### Task 2: 本地池首跳质量（减少 401 拖垮混池）

**Files:**
- Create: `scripts/k12_prioritize_rt.py`
- Modify: 仅通过网关 API / sqlite 状态字段，不改 chatgpt2api 源码（除非 API 不足）
- Test: dry-run 统计

**Interfaces:**
- Consumes: `chatgpt2api/data/accounts.db` 或 `GET /api/accounts`
- Produces: 无 RT 且 plan=k12 的账号 `status` 软禁用或 priority 降低；go/plus+RT 保持正常

- [ ] **Step 1: 写 dry-run 统计**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prefer RT-capable go/plus; soft-disable snapshot k12 without RT (dry-run default)."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

DB = Path(r"D:/Users/grok-auto-register/chatgpt2api/data/accounts.db")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write soft-disable for no-RT k12")
    ap.add_argument("--limit", type=int, default=0, help="max rows to disable (0=all)")
    args = ap.parse_args()
    c = sqlite3.connect(str(DB))
    stats = {"k12_no_rt": 0, "k12_rt": 0, "go_rt": 0, "plus_rt": 0, "other": 0}
    to_disable = []
    for id_, data in c.execute("SELECT id, data FROM accounts"):
        try:
            j = json.loads(data)
        except Exception:
            continue
        plan = str(j.get("plan_type") or "").lower()
        rt = bool(j.get("refresh_token"))
        if plan == "k12" and not rt:
            stats["k12_no_rt"] += 1
            to_disable.append((id_, j))
        elif plan == "k12" and rt:
            stats["k12_rt"] += 1
        elif plan == "go" and rt:
            stats["go_rt"] += 1
        elif plan == "plus" and rt:
            stats["plus_rt"] += 1
        else:
            stats["other"] += 1
    print("stats", stats, "would_disable", len(to_disable))
    if not args.apply:
        print("dry-run only; pass --apply to soft-disable no-RT k12")
        c.close()
        return 0
    n = 0
    for id_, j in to_disable:
        if args.limit and n >= args.limit:
            break
        j["status"] = "禁用"  # gateway treats non-正常 as skipped when selecting
        j["last_refresh_error"] = "soft-disable: k12 snapshot no RT (codex-unified plan)"
        c.execute(
            "UPDATE accounts SET data=? WHERE id=?",
            (json.dumps(j, ensure_ascii=False), id_),
        )
        n += 1
    c.commit()
    c.close()
    print("disabled", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: dry-run → 确认数量 → apply**

```bash
python -m py_compile scripts/k12_prioritize_rt.py
python scripts/k12_prioritize_rt.py
# 确认 would_disable 合理后：
python scripts/k12_prioritize_rt.py --apply
curl -s http://127.0.0.1:8124/healthz
curl -s http://127.0.0.1:8124/v1/chat/completions \
  -H "Authorization: Bearer k12-pool-local" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"gpt-5.6\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply: PONG\"}],\"max_tokens\":8}"
```

Expected: dry-run 打印 `k12_no_rt` 等；apply 后 chat 冒烟尽量 200（若仍失败，检查 go/plus 是否被选中、代理 7897）。

---

### Task 3: Codex 专用 CLIProxy 配置（:8327）

**Files:**
- Create: `D:/cli-proxy-api/config-codex.yaml`（**仓库外**，含密钥）
- Create: `D:/cli-proxy-api/start-codex.bat`
- Create: `docs/CODEX_UNIFIED_POOL.md`（无密钥，只写结构）
- Modify: 无 grok `config.yaml`（禁止改坏 8317）

**Interfaces:**
- Consumes: Task 1 的 OK 上游列表
- Produces: `http://127.0.0.1:8327/v1` 暴露 `gpt-5.6` 等 alias

- [ ] **Step 1: 写 config-codex.yaml 骨架**（密钥从 probe/cc-switch 现网复制，此处用占位说明）

```yaml
# Codex unified pool — DO NOT mix with Grok config.yaml (:8317)
host: "127.0.0.1"
port: 8327
# 无 xAI auth-dir：本地号已在 chatgpt2api 聚合
# auth-dir: 省略

api-keys:
  - "sk-local-codex-unified-2026"

remote-management:
  allow-remote: false

debug: false
logging-to-file: true
commercial-mode: true
usage-statistics-enabled: false

request-retry: 1
max-retry-credentials: 12
max-retry-interval: 5
disable-cooling: false
transient-error-cooldown-seconds: 10

# 本机 chatgpt2api 不走 Clash 也可；远端走 7897
# 全局 proxy 仅当需要时打开；本地 entry 可 proxy-url: ""
# proxy-url: "http://127.0.0.1:7897"

openai-compatibility:
  - name: "local-k12"
    disabled: false
    base-url: "http://127.0.0.1:8124/v1"
    api-key-entries:
      - api-key: "k12-pool-local"
    models:
      - name: "gpt-5.6"
        alias: "gpt-5.6"
      - name: "gpt-5.6-sol"
        alias: "gpt-5.6-sol"
      - name: "gpt-5.6-luna"
        alias: "gpt-5.6-luna"
      - name: "gpt-5.6-terra"
        alias: "gpt-5.6-terra"
      - name: "gpt-5.5"
        alias: "gpt-5.5"

  # 以下仅纳入 Task1 recommend=true 的站；密钥从本机 cc-switch 复制
  # - name: "lyclaude"
  #   base-url: "https://free.lyclaude.site/v1"
  #   headers:
  #     User-Agent: "Mozilla/5.0 ..."
  #   api-key-entries:
  #     - api-key: "<from cc-switch>"
  #       proxy-url: "http://127.0.0.1:7897"
  #   models:
  #     - name: "gpt-5.6"
  #       alias: "gpt-5.6"
  #     - name: "gpt-5.6-sol"
  #       alias: "gpt-5.6"
  #     - name: "gpt-5.6"
  #       alias: "remote-gpt-5.6"

routing:
  strategy: round-robin
  session-affinity: true
  session-affinity-ttl: "2h"
```

实现时：**先只启用 local-k12**，冒烟通过后再追加远端（降低一次配挂风险）。

- [ ] **Step 2: 启动脚本**

`D:/cli-proxy-api/start-codex.bat`:

```bat
@echo off
cd /d D:\cli-proxy-api
cli-proxy-api.exe -config config-codex.yaml
```

- [ ] **Step 3: 启动并验收**

```bash
# 另开进程启动 start-codex.bat 或:
# cd /d/cli-proxy-api && ./cli-proxy-api.exe -config config-codex.yaml
curl -s http://127.0.0.1:8327/v1/models -H "Authorization: Bearer sk-local-codex-unified-2026"
curl -s http://127.0.0.1:8327/v1/chat/completions \
  -H "Authorization: Bearer sk-local-codex-unified-2026" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"gpt-5.6\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply: PONG\"}],\"max_tokens\":8}"
```

Expected: models 含 `gpt-5.6`；chat 200。确认 **8317 Grok 仍正常**（回归）：

```bash
curl -s http://127.0.0.1:8317/v1/models -H "Authorization: Bearer sk-local-grok-pool-2026" | head -c 200
```

- [ ] **Step 4: 追加远端 OK 站**

把 Task1 `recommend=true` 的远端按 `REMOTE_POOL_SUPPLEMENT` 模式写入，**同一 `alias: gpt-5.6`**；调试用 `remote-gpt-5.6`。重启 codex CLIProxy，再打一枪 chat。

- [ ] **Step 5: 写 `docs/CODEX_UNIFIED_POOL.md`**

内容镜像 `docs/REMOTE_POOL_SUPPLEMENT.md`，替换端口/模型/本地上游为 8124；强调与 Grok 分池；密钥路径；探活命令。

---

### Task 4: cc-switch 接入 `codex-unified`

**Files:**
- Create: `scripts/cc_upsert_codex_unified.py`
- Modify: `C:/Users/zhugu/.cc-switch/cc-switch.db`（脚本内 backup）
- Modify: `C:/Users/zhugu/.codex/config.toml` / `auth.json`（经 switch）
- Test: `python scripts/cc_switch_codex_provider.py current`

**Interfaces:**
- Consumes: `:8327` + `sk-local-codex-unified-2026`
- Produces: provider id `codex-unified`，`is_current=1`

- [ ] **Step 1: upsert 脚本**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Upsert cc-switch codex provider pointing at CLIProxy codex unified :8327."""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
SETTINGS = Path(r"C:/Users/zhugu/.cc-switch/settings.json")
PROVIDER_ID = "codex-unified"
NAME = "Codex Unified (local+remote)"
API_KEY = "sk-local-codex-unified-2026"
BASE = "http://127.0.0.1:8327/v1"

CONFIG_TOML = f'''model_provider = "codexunified"
model = "gpt-5.6"
model_reasoning_effort = "none"

[model_providers.codexunified]
name = "Codex Unified"
base_url = "{BASE}"
wire_api = "responses"
requires_openai_auth = true

[model_providers.codexunified.http_headers]
User-Agent = "codex-cli"
'''

SETTINGS_CONFIG = json.dumps(
    {
        "auth": {"OPENAI_API_KEY": API_KEY},
        "config": CONFIG_TOML,
    },
    ensure_ascii=False,
)


def main() -> None:
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = DB.with_name(f"cc-switch.db.bak-codex-unified-{ts}")
    shutil.copy2(DB, bak)
    print("backup", bak)
    c = sqlite3.connect(str(DB))
    row = c.execute(
        "SELECT id FROM providers WHERE id=? AND app_type='codex'", (PROVIDER_ID,)
    ).fetchone()
    now = int(time.time() * 1000)
    if row:
        c.execute(
            "UPDATE providers SET name=?, settings_config=?, updated_at=? WHERE id=? AND app_type='codex'",
            (NAME, SETTINGS_CONFIG, now, PROVIDER_ID),
        )
        print("updated", PROVIDER_ID)
    else:
        # schema may have more NOT NULL columns — inspect first if insert fails
        cols = [r[1] for r in c.execute("PRAGMA table_info(providers)").fetchall()]
        print("providers cols", cols)
        c.execute(
            "INSERT INTO providers (id, name, app_type, settings_config, is_current, created_at, updated_at) VALUES (?,?,?,?,0,?,?)",
            (PROVIDER_ID, NAME, "codex", SETTINGS_CONFIG, now, now),
        )
        print("inserted", PROVIDER_ID)
    c.commit()
    c.close()
    print("next: python scripts/cc_switch_codex_provider.py switch codex-unified")


if __name__ == "__main__":
    main()
```

若 `INSERT` 因列不全失败：用 `PRAGMA table_info` 补齐默认列后重试（实现时按实际 schema 改一行 INSERT）。

- [ ] **Step 2: 写入并切换**

```bash
python -m py_compile scripts/cc_upsert_codex_unified.py
python scripts/cc_upsert_codex_unified.py
python scripts/cc_switch_codex_provider.py switch codex-unified
python scripts/cc_switch_codex_provider.py current
```

Expected: `id: codex-unified`，`base_url = http://127.0.0.1:8327/v1`，key_len > 0。

- [ ] **Step 3: 启动器**

Create `scripts/codex_unified.ps1`（仿 `codex_k12.ps1`：清 `OPENAI_API_KEY` 等后再 `codex`）：

```powershell
# scripts/codex_unified.ps1
$env:OPENAI_API_KEY = $null
$env:OPENAI_BASE_URL = $null
# ensure cc-switch applied
python "$PSScriptRoot\cc_switch_codex_provider.py" switch codex-unified | Out-Host
codex @args
```

---

### Task 5: 端到端验收 + 文档收口

**Files:**
- Modify: `docs/CODEX_CLAUDE_OPS.md`
- Modify: `docs/COMMUNITY_THICKEN.md`（K12 段落后加 Codex 统一池一行）
- Test: 手工 curl + 可选 codex exec

- [ ] **Step 1: 冒烟清单**

```bash
# 1) 本地网关
curl -s http://127.0.0.1:8124/healthz
# 2) 统一入口
curl -s http://127.0.0.1:8327/v1/chat/completions \
  -H "Authorization: Bearer sk-local-codex-unified-2026" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"gpt-5.6\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":8}"
# 3) Grok 未回归
curl -s http://127.0.0.1:8317/v1/models -H "Authorization: Bearer sk-local-grok-pool-2026" | head -c 120
# 4) cc-switch
python scripts/cc_switch_codex_provider.py current
```

Expected: 1–2 成功；3 仍有 grok 模型；4 为 codex-unified。

- [ ] **Step 2: 故障注入（可选）**

临时 `local-k12.disabled: true` 重启 8327，确认仍能靠远端返回；再恢复 local。验证「协力不中断」。

- [ ] **Step 3: 更新运维文档**

`docs/CODEX_CLAUDE_OPS.md` 顶部表改为：

| 项 | 值 |
|----|-----|
| Provider | `codex-unified` → `http://127.0.0.1:8327/v1` |
| 底层本地 | chatgpt2api `:8124` |
| 底层远端 | CLIProxy openai-compatibility（见 CODEX_UNIFIED_POOL.md） |
| 启动 | `scripts/codex_unified.ps1` |
| 切回纯本地 | `python scripts/cc_switch_codex_provider.py switch k12-local-chatgpt2api` |

`COMMUNITY_THICKEN.md` 增加：

```markdown
| Codex 本地+远端同 alias 混池 | `docs/CODEX_UNIFIED_POOL.md` + CLIProxy `:8327` |
```

- [ ] **Step 4: 提交（仅仓库内文件，须用户同意）**

```bash
git add scripts/probe_codex_upstreams.py scripts/k12_prioritize_rt.py \
  scripts/cc_upsert_codex_unified.py scripts/codex_unified.ps1 \
  docs/CODEX_UNIFIED_POOL.md docs/CODEX_CLAUDE_OPS.md docs/COMMUNITY_THICKEN.md \
  docs/superpowers/plans/2026-07-16-codex-unified-pool.md
git status
# 用户同意后再 commit
```

---

## 明确不做（YAGNI）

- 不把 Claude / Grok / NVIDIA 并进 `gpt-5.6` alias。
- 不在 Kimi `config.toml` 做 Codex failover（Codex 客户端走 cc-switch）。
- 不新写完整 LB 服务（优先 CLIProxy 已有能力）。
- 不自动 commit `config-codex.yaml` 密钥。

## 风险与回滚

| 风险 | 缓解 |
|------|------|
| 远端不支持 responses | 探活 + disabled；Codex 可试 `wire_api` 与 chat 双测 |
| 本地死 k12 拖死首跳 | Task 2 软禁无 RT |
| 8327 与 8317 配错文件 | 独立 config-codex.yaml + 回归 curl 8317 |
| cc-switch schema INSERT 失败 | backup DB；失败则 GUI 手工加 provider 再 switch 脚本 |
| 混池后难排查 | 日志 `logging-to-file`；保留 `remote-gpt-5.6` 与 `k12-local-chatgpt2api` |

回滚：`python scripts/cc_switch_codex_provider.py switch k12-local-chatgpt2api`，停 8327 进程。

## Self-Review

1. **Spec coverage:** 聚合本地+远端、单入口、社区同 alias、分 Grok 池、探活、cc-switch、文档 — 均有 Task。  
2. **Placeholder scan:** 密钥在本机文件用真实值填充；计划内用说明而非 TBD。INSERT 列需实现时按 PRAGMA 补齐（已写明）。  
3. **Type consistency:** provider id 统一 `codex-unified`；端口 `8327`；key `sk-local-codex-unified-2026`；模型 `gpt-5.6`。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-16-codex-unified-pool.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — 每任务新 subagent，任务间 review  
2. **Inline Execution** — 本会话按 executing-plans 顺序做，检查点停顿  

你要哪种？直接说「按计划执行」或选 1/2 即可。
