# Codex 本地 + 远端统一池

更新：2026-07-16

与 [REMOTE_POOL_SUPPLEMENT.md](./REMOTE_POOL_SUPPLEMENT.md) 同构：**客户端一个 endpoint / 一个模型名**，CLIProxy 内同 alias 多上游 hop。

## 拓扑

```
Codex / cc-switch  codex-unified
        │
        ▼
CLIProxy :8327   (config-codex.yaml，与 Grok :8317 分离)
        │
   ┌────┴─────┐
   ▼          ▼
local-k12   lyclaude (FREE)
:8124       free.lyclaude.site
OAuth 号池  sk（上游模型 gpt-5.6-sol 映射为客户端 gpt-5.6）
```

| 项 | 值 |
|----|-----|
| 统一入口 | `http://127.0.0.1:8327/v1` |
| API Key | `sk-local-codex-unified-2026` |
| 默认模型 | `gpt-5.6`（远端映射到 `gpt-5.6-sol`） |
| 配置文件 | `D:/cli-proxy-api/config-codex.yaml`（**勿 commit 密钥**） |
| 启动 | `D:/cli-proxy-api/start-codex.bat` |
| cc-switch | `python scripts/cc_switch_codex_provider.py switch codex-unified` |
| Codex 启动 | `scripts/codex_unified.ps1` |

## 社区对齐

- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) `openai-compatibility` 同 alias 内池
- 本地 OAuth（chatgpt2api）与远端 sk **职责分离**；sk 不进 OAuth 库
- 可选 `remote-gpt-5.6` 仅调试强制远端

## 探活 / 运维

```bash
python scripts/probe_codex_upstreams.py
python scripts/k12_prioritize_rt.py          # dry-run
python scripts/k12_prioritize_rt.py --apply  # 软禁无 RT k12 快照

curl -s http://127.0.0.1:8327/v1/models \
  -H "Authorization: Bearer sk-local-codex-unified-2026"
curl -s http://127.0.0.1:8327/v1/chat/completions \
  -H "Authorization: Bearer sk-local-codex-unified-2026" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.6","messages":[{"role":"user","content":"ping"}],"max_tokens":8}'
```

## 当前上游状态（2026-07-16）

| 上游 | 状态 | 备注 |
|------|------|------|
| local-k12 `:8124` | models OK，chat 常 401 | 无 RT k12 已大批软禁；go/plus+RT 仍需网关选号/刷新稳定 |
| lyclaude FREE | chat OK | 默认兜底，`gpt-5.6`→`gpt-5.6-sol` |
| sharedchat | **disabled** | 返回「公益站旧链路关闭」 |
| ZMoon | 未接入 | SSL 失败 |

## 不要做

- 不要把 Grok `:8317` 与 Codex `:8327` 合成一个 alias
- 不要在 cc-switch 多 provider 之间手动切来当 failover
- 不要把远端 sk 写入 `chatgpt2api` 当 OAuth 账号

## 常驻（防 8327「莫名掉线」）

**根因：** 在 agent/终端会话里前台启动 `cli-proxy-api.exe`，会话结束进程一起没；旧 `cliproxy_mem_watchdog` 按**进程名**只认一个实例，重启会误杀兄弟端口。

**方案：**

| 动作 | 命令 |
|------|------|
| 隐藏启动 Codex | `wscript D:\cli-proxy-api\start-codex-hidden.vbs` 或 fleet `-Once` |
| 三池探活+补齐 | `powershell -File scripts\cliproxy_fleet_watchdog.ps1 -Once` |
| 状态 | `powershell -File scripts\cliproxy_fleet_watchdog.ps1 -Status` |
| 登录常驻 | `powershell -File scripts\install_cliproxy_fleet_startup.ps1` → Startup 里 `CLIProxyFleetWatchdog.cmd`（无需管理员） |

看门狗按 **CommandLine 里的 config 名** 分别管 `config.yaml` / `config-codex.yaml` / `config-claude.yaml`，只重启挂掉的那一个。  
已验证：杀掉 8327 后 `-Once` 只拉 Codex，8317/8337 仍 200。

**不要**再用只认单实例的 `cliproxy_mem_watchdog.ps1` 去管多端口舰队（会按进程名误杀）。

## 回滚

```bash
python scripts/cc_switch_codex_provider.py switch k12-local-chatgpt2api
# 停 8327 进程；Grok 8317 不受影响
```
