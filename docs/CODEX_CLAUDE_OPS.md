# Codex + Claude Code 本机运维

更新：2026-07-14

## Codex（K12 本地网关）

| 项 | 值 |
|----|-----|
| Provider | `k12local` → `http://127.0.0.1:8124/v1` |
| Model | `gpt-5.6` / `reasoning=none` |
| 启动 | `scripts/codex_k12.ps1` 或 `codex_k12.sh` |
| 切 provider | `python scripts/cc_switch_codex_provider.py switch <id>` |
| 探活 | `curl http://127.0.0.1:8124/healthz` |

### 已做优化
- `model_catalog_json` **绝对路径**
- 去掉重复 `context7-1` MCP
- `hooks.json` 去掉非法顶层 `state`（信任哈希挪到 `hooks.state.json`）
- `~/.codex/AGENTS.md` 对齐当前 K12 默认（不再写 muyuan 为主）

### 注意
- Shell 里残留的 `OPENAI_API_KEY`（muyuan）会盖过 `auth.json` → 必须用 `codex_k12` 启动
- 共享 K12 **无 RT**，约至 2026-07-23
- SaladDay `cc-switch` CLI 5.9.0 打不开 GUI 3.17 的 DB schema v13

## Claude Code

| 项 | 值 |
|----|-----|
| 当前 cc-switch provider | `Sub2API`（`settings.json` → `currentProviderClaude`） |
| 启动 | `scripts/claude_code_start.ps1` / `claude_code_start.sh` |
| 切 provider | CC Switch GUI（Claude 应用）或改 DB 后用启动脚本 |

### 已做优化
- 启动脚本从 **当前** claude provider 注入 `ANTHROPIC_*`（避免 GUI 切了但终端 env 仍旧）
- 项目内运维说明：本文件

### 建议
- 复杂/安全改动用 Claude；大批量低风险实现仍可 Reasonix（见 A2A risk 路由）
- 勿把 `ANTHROPIC_AUTH_TOKEN` 写进仓库

## 快速自检

```bat
curl -s http://127.0.0.1:8124/healthz
python scripts\cc_switch_codex_provider.py current
.\scripts\codex_k12.ps1 exec -s read-only --ephemeral -m gpt-5.6 "Reply: OK"
.\scripts\claude_code_start.ps1 -p "Reply: OK"
```
