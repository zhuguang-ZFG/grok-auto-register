# Claude Code 多反代统一池

更新：2026-07-16

与 [CODEX_UNIFIED_POOL.md](./CODEX_UNIFIED_POOL.md)、[REMOTE_POOL_SUPPLEMENT.md](./REMOTE_POOL_SUPPLEMENT.md) 同构：**客户端一个 BASE_URL + 一个 token**，CLIProxy 内多 `claude-api-key` 失败 hop。

## 拓扑

```
Claude Code / cc-switch  claude-unified
        │
        ▼
CLIProxy :8337   (config-claude.yaml)
        │
   ┌────┼────────────┬─────────────┐
   ▼    ▼            ▼             ▼
100xlabs 林夕公益   AnyRouter    GLM(仅 glm-5.2 别名)
cloak always        cloak always last-resort 显式模型
```

| 项 | 值 |
|----|-----|
| 统一入口 | `http://127.0.0.1:8337` |
| API Key | `sk-local-claude-unified-2026` |
| 默认模型 | `claude-opus-4-8` |
| 配置 | `D:/cli-proxy-api/config-claude.yaml`（**勿 commit**） |
| 启动 | `D:/cli-proxy-api/start-claude.bat` |
| cc-switch | `python scripts/cc_switch_claude_provider.py switch claude-unified` |
| Claude 启动 | `scripts/claude_unified.ps1` |

## 端口分离（硬）

| 端口 | 用途 |
|------|------|
| 8317 | Grok |
| 8327 | Codex |
| **8337** | **Claude** |
| 8347 | GLM（zhipu plan/trial/team 三 key 互备，见 `config-glm.yaml`） |

## 入池规则

1. 真 Claude 反代（100xlabs / 林夕 / AnyRouter）进默认 `claude-opus-*` alias  
2. 上游常要求 **Claude Code 客户端指纹** → 配置 `cloak.mode: always`  
3. **GLM 只映射 `glm-5.2` / `glm-5.1`**，禁止把 GLM 写成 `claude-opus-4-8`（静默降智）  
4. 探活：`python scripts/probe_claude_upstreams.py`

## 探活 / 冒烟

```bash
python scripts/probe_claude_upstreams.py

curl -s http://127.0.0.1:8337/v1/messages \
  -H "x-api-key: sk-local-claude-unified-2026" \
  -H "Authorization: Bearer sk-local-claude-unified-2026" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-opus-4-8","max_tokens":32,"messages":[{"role":"user","content":"Reply: PONG"}]}'
```

## 常驻

与 Codex 共用舰队看门狗（按 config 分实例，不误杀）：

```bat
wscript D:\cli-proxy-api\start-claude-hidden.vbs
powershell -File scripts\cliproxy_fleet_watchdog.ps1 -Once
powershell -File scripts\cliproxy_fleet_watchdog.ps1 -Install
```

## 回滚

```bash
python scripts/cc_switch_claude_provider.py switch glm52-team-fallback-1784118927115
# 或 switch 原 100xlabs provider id
# 停 8337 进程
```

## 注意

- 直连 curl 打 100xlabs 常 503「only allows Claude Code clients」；经 CLIProxy cloak 后可通  
- AnyRouter 可能要求 1m beta；坏时 hop 下一凭据  
- 密钥只放 `config-claude.yaml`，不进 git  
