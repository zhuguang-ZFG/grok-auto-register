# Codex + Claude Code 本机运维

更新：2026-07-16

## Codex（统一池，推荐）

| 项 | 值 |
|----|-----|
| Provider | `codex-unified` → `http://127.0.0.1:8327/v1` |
| 底层本地 | chatgpt2api `:8124`（OAuth 号池） |
| 底层远端 | CLIProxy `openai-compatibility`（lyclaude 等，见 [CODEX_UNIFIED_POOL.md](./CODEX_UNIFIED_POOL.md)） |
| Model | `gpt-5.6`（远端映射 `gpt-5.6-sol`） |
| 启动 CLIProxy | `D:\cli-proxy-api\start-codex.bat` |
| 启动 Codex | `scripts/codex_unified.ps1` |
| 切 provider | `python scripts/cc_switch_codex_provider.py switch codex-unified` |
| 探活 | `curl http://127.0.0.1:8327/v1/models -H "Authorization: Bearer sk-local-codex-unified-2026"` |

### 纯本地回退

| 项 | 值 |
|----|-----|
| Provider | `k12-local-chatgpt2api` → `http://127.0.0.1:8124/v1` |
| 启动 | `scripts/codex_k12.ps1` / `codex_k12.sh` |

### 已做优化
- 本地 + 远端同 alias 混池（与 Grok `REMOTE_POOL_SUPPLEMENT` 同构，**端口分离** 8327 vs 8317）
- `model_catalog_json` **绝对路径**（k12 路径）
- 去掉重复 `context7-1` MCP
- `hooks.json` 去掉非法顶层 `state`（信任哈希挪到 `hooks.state.json`）
- 选号 tier：plus/go/team+RT 优先，k12 无 RT 快照末位（`account_service._text_account_tier`）
- 无 RT k12 软禁走**网关 API**：`python scripts/k12_prioritize_rt.py --apply`（**禁止**直写 sqlite，会被 flush 覆盖）
- 网关重启：`python scripts/restart_chatgpt2api.py`（改完 `chatgpt2api/services/*` 后必须执行，否则跑旧代码）

### 注意
- Shell 里残留的 `OPENAI_API_KEY`（muyuan）会盖过 `auth.json` → 必须用 `codex_unified` / `codex_k12` 启动
- 共享 K12 **无 RT** 快照易 401；优先 go/plus+RT 或远端兜底
- SaladDay `cc-switch` CLI 5.9.0 打不开 GUI 3.17 的 DB schema v13 → 用 `cc_switch_codex_provider.py`
- **不要**把 Grok 池并进 Codex alias

## Claude Code（统一池，推荐）

| 项 | 值 |
|----|-----|
| Provider | `claude-unified` → `http://127.0.0.1:8337` |
| 底层 | CLIProxy `claude-api-key` 多反代（100xlabs / 林夕 / AnyRouter；见 [CLAUDE_UNIFIED_POOL.md](./CLAUDE_UNIFIED_POOL.md)） |
| 默认模型 | `claude-opus-4-8`（GLM 仅显式 `glm-5.2`，不冒充 Opus） |
| 启动网关 | `D:\cli-proxy-api\start-claude.bat` |
| 启动 CC | `scripts/claude_unified.ps1`（switch + `claude_code_start.ps1`） |
| 切 provider | `python scripts/cc_switch_claude_provider.py switch claude-unified` |
| 回退 GLM | `python scripts/cc_switch_claude_provider.py switch glm52-team-fallback-1784118927115` |

### 已做优化
- 多真 Claude 反代同池 hop + `cloak.mode: always`（上游要求 Claude Code 指纹）
- 启动脚本从 **当前** claude provider 注入 `ANTHROPIC_*`
- 与 Grok/Codex **端口分离**：8317 / 8327 / **8337**

### 建议
- 复杂/安全改动用 Claude；大批量低风险实现仍可 Reasonix（见 A2A risk 路由）
- 勿把 `ANTHROPIC_AUTH_TOKEN` 写进仓库；密钥在 `config-claude.yaml`

## 快速自检

```bat
curl -s http://127.0.0.1:8124/healthz
curl -s http://127.0.0.1:8327/v1/models -H "Authorization: Bearer sk-local-codex-unified-2026"
curl -s http://127.0.0.1:8337/v1/models -H "Authorization: Bearer sk-local-claude-unified-2026" -H "x-api-key: sk-local-claude-unified-2026"
python scripts\cc_switch_codex_provider.py current
python scripts\cc_switch_claude_provider.py current
python scripts\probe_three_pools.py --skip-chat
.\scripts\codex_unified.ps1 exec -s read-only --ephemeral -m gpt-5.6 "Reply: OK"
.\scripts\claude_unified.ps1 -p "Reply: OK"
```
