# 远端 URL 作为本地号池补充（自动协力、不中断）

## 目标

客户端**始终**只打 `local-cpa/grok-4.5`（一个 endpoint、一个模型名）。  
本地 OAuth 号与远端 OpenAI 兼容渠道在 **CLIProxy 内部**互相补位：某一跳 403/429/网络失败时自动 hop 下一凭据，**不用**改模型、不用手动切远端。

## 原则（社区共识）

| 组件 | 职责 |
|------|------|
| **CPA / CLIProxy** | 本地 `cpa_auths/` + `openai-compatibility` 远端，统一对外 |
| **Sub2API / 远程 sk-** | 对方已聚合的号池，只当**渠道凭据**，不是 CPA 文件 |
| **New-API** | 对外聚合/售卖（本机自用可不要） |

不能把 `sk-…` 写进 `cpa_auths/`：没有 RT、不能 refresh、不能走 raceguard 判死。

## 本机接线（`D:/cli-proxy-api/config.yaml`）

1. **本地弹药**：`auth-dir` → `cpa_auths/`（多账号 round-robin + `max-retry-credentials` 失败换号）
2. **远端弹药**：`openai-compatibility` → 例如 junhuang，`models.alias: grok-4.5` 与本地**同名**
3. **路由**：`routing.strategy: round-robin`；`session-affinity: true`（多轮粘会话；绑定号不可用时仍会 failover）
4. **出口**：全局 / 远端 entry 的 `proxy-url` 走 Clash；CF 1010 站点加浏览器 `User-Agent`

同 alias 内部池（官方注释）：请求 round-robin；**在产出输出前**失败则续试同 alias 下一上游。  
日志可见：`auth=openai-compatibility:junhuang:… provider=mixed model=grok-4.5` 与本地 `grok-4.5-build-free` 交替，说明已混池。

可选别名 `remote-grok-4.5`：仅调试/强制打远端，日常 Kimi **不要**切这个。

改完后重启 `cli-proxy-api.exe`。启动日志应含 `+ N OpenAI-compat`（N≥1）。

### 探活

```bash
# 远端直连（经代理 + UA）
curl -x http://127.0.0.1:7897 \
  -H "Authorization: Bearer <sk>" \
  -H "User-Agent: Mozilla/5.0 ..." \
  https://sub123.example/v1/models

# 统一入口（客户端只认这个）
curl http://127.0.0.1:8317/v1/models \
  -H "Authorization: Bearer sk-local-grok-pool-2026"
curl http://127.0.0.1:8317/v1/chat/completions \
  -H "Authorization: Bearer sk-local-grok-pool-2026" \
  -H "Content-Type: application/json" \
  -d '{"model":"grok-4.5","messages":[{"role":"user","content":"ping"}],"max_tokens":8}'
```

### 再加一家远端

复制 `openai-compatibility` 下一项，**同样** `alias: grok-4.5`，即可并入同一自动 hop 池。  
密钥只放 `D:/cli-proxy-api/config.yaml`（勿提交到 grok-auto-register 仓）。

## 不要做的事

- 不要在 Kimi 里为「failover」再配第二个 default 模型（那是手动切，不是混池）
- 不要指望远端进 `quota_watch` / 号池水位文件统计（只统计 `cpa_auths/`）
- 不要把共享包无 probe 盲导入（见 `scripts/import_cpa_with_probe.py`）

## toolbridge 子模块

`toolbridge/` → `Oct4Pie/toolbridge`（`.gitmodules`）。  
本地 `toolbridge/config.json` 用 `assume-unchanged`，避免脏 status；勿 push 本机 config。
