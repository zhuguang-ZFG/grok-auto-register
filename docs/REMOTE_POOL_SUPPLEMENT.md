# 远端 URL 作为本地号池补充

## 原则（社区共识）

| 组件 | 职责 |
|------|------|
| **CPA / CLIProxy** | 管本地 `cpa_auths/` 认证文件 + 可选 OpenAI 兼容上游 |
| **Sub2API / 远程 sk-** | 对方已聚合的号池，只当**渠道**，不是 CPA 文件 |
| **New-API** | 对外聚合/售卖（本机自用可不要） |

不能把 `sk-…` 写进 `cpa_auths/`：没有 RT、不能 refresh、不能走 raceguard 判死。

## 本机方案（已接线）

`D:/cli-proxy-api/config.yaml` → `openai-compatibility`：

- 本地：`auth-dir` = `cpa_auths/`（OAuth 号池）
- 远端：`openai-compatibility[].base-url` + `api-key-entries`
- 模型别名：`grok-4.5` 与本地共用（CLIProxy 同 alias 可进内部池 / 失败续试）；另暴露 `remote-grok-4.5` 便于强制打远端
- 全局 `proxy-url` + 远端 entry 的 `proxy-url`：走 Clash（区域 + CF）
- 部分远端（如 junhuang）需浏览器 UA，写在 `headers`

改完后重启 `cli-proxy-api.exe`（热更不一定覆盖 openai-compatibility）。

### 探活

```bash
# 经代理 + UA
curl -x http://127.0.0.1:7897 \
  -H "Authorization: Bearer <sk>" \
  -H "User-Agent: Mozilla/5.0 ..." \
  https://sub123.example/v1/models

# 经本机 CLIProxy
curl http://127.0.0.1:8317/v1/models \
  -H "Authorization: Bearer sk-local-grok-pool-2026"
```

### 再加一家远端

复制 `openai-compatibility` 下一项，改 `name` / `base-url` / `api-key` / `models`。  
密钥只放 `D:/cli-proxy-api/config.yaml`（勿提交到 grok-auto-register 仓）。

## 客户端侧（可选）

Kimi `~/.kimi-code/config.toml` 也可单独加 `providers.xxx`，与 CLIProxy 内合并是两条线：

- **CLIProxy 内合并**：Kimi 仍只打 `local-cpa`，失败时由 CPA 自己 hop 到远端
- **Kimi 多 provider**：手动 `-m junhuang/grok-4.5`，不进本地号池水位统计

自用优先 **CLIProxy 内合并**。

## toolbridge 子模块

`toolbridge/` 是 gitlink（`Oct4Pie/toolbridge`），本地 `config.json` 后端指向 K12 `:8124`。  
该文件用 `assume-unchanged`，避免污染父仓 status；勿向上游 push 本机 config。
