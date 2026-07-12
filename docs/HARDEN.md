# 自用号池加固基线（社区 / GitHub 对齐）

本文固化本仓库**已经落地**的稳定性约定，避免会话失忆后回退到高风险默认值。  
参考：CLIProxyAPI session-affinity、LINUX DO 协议铸造 / soft-disable / 滚动额度恢复、本机 host-safe Clash 轮换。

## 1. 铸造链路（CPA）

```
SSO cookie
  → 1) Device Flow          cpa_xai/protocol_mint.py
  → 2) Auth-code PKCE       cpa_xai/authcode_mint.py   (community fallback)
  → 3) Browser mint         cpa_xai/browser_confirm.py
  → write cli-chat-proxy CPA (headers = grok-shell)
```

| 键 | 加固默认 | 原因 |
|----|----------|------|
| `cpa_prefer_protocol` | `true` | 少开铸造浏览器 |
| `cpa_prefer_authcode_fallback` | `true` | device 挂时社区授权码路径 |
| `cpa_authcode_attempts` | `1` | 失败再 browser，不硬重试炸代理 |
| `cpa_protocol_attempts` | `3` | TLS/56 瞬时错误重试 |
| `cpa_probe_after_write` | **`false`** | 铸造后 probe 费额度且误杀 |
| `cpa_base_url` | `https://cli-chat-proxy.grok.com/v1` | Build free 路径，非 api.x.ai 计费 |
| `cpa_mint_rotate_egress` | `false` | 默认不每号换 IP；`rotate_on_tls=true` |

## 2. 号池 / sticky（CLIProxy）

| 行为 | 约定 |
|------|------|
| 耗尽 | `disabled:true` + `quota_state.recover_after`（**软禁用，不删文件**） |
| 恢复窗口 | 默认 **6h** 滚动（`GROK_POOL_RECOVER_HOURS` 可覆盖）；非整 24h 锁死 |
| 终端死号 | `refresh_revoked` / `missing_refresh_token`：**只写一次**，purge 循环跳过 |
| 健康检查 | `pool_probe_on_health=false`；禁止全池 `/models` 扫活 |
| 静默刷新 | `quota_watch_pool_refresh_*`：临期 JWT 只 refresh，不 probe、不 hard purge |
| CLIProxy | `session-affinity: true`，建议 `session-affinity-ttl: "4h"` |
| 路由切换 | `python set_cliproxy_routing.py cache`（粘性）/ `pool`（纯 failover） |

**禁止**：对 live `cpa_auths` 做 MOVE/unlink 风暴（CLIProxy 会看成 REMOVE → sticky reselect → 缓存全丢）。

## 3. 出口 / 本机网络

| 键 | 加固默认 | 原因 |
|----|----------|------|
| `clash_force_global` | **`false`** | 不改 Clash 全局 mode |
| `clash_close_conns` | **`false`** | 不掐本机所有 TCP（含 CLIProxy） |
| `clash_rotate_every_n` | `5` | 降频换节点 |
| `clash_selector` | `""` | 填专用组名可彻底隔离本机 GLOBAL |
| `http_proxy_prefer_over_clash` | `false` | 社区境外 HTTP 列表国内常不可达 |

可选彻底隔离：Clash **rule** 模式 + 分组 `注册专用` + 规则 `x.ai`/`grok.com` → 该组，再设 `clash_selector`。

## 4. 本机性能（号池已大时）

| 键 | 建议 |
|----|------|
| `concurrent_count` | `1`（2 仅在成功率稳定且内存够时） |
| `register_count` | `4` |
| `auto_loop_pause_sec` | `≥120` |
| `cpa_mint_workers` | `1` |
| `quota_watch_poll_sec` | `15` |
| `quota_watch_sample_probe_n` | `0` |

## 5. 一键自检

```bash
python pool_status.py
python set_cliproxy_routing.py status
python proxy_health.py
# CLIProxy
curl -s http://127.0.0.1:8317/v1/models -H "Authorization: Bearer sk-local-grok-pool-2026" | head -c 200
```

期望：`affinity=true`、铸造 `protocol_ok ≫ fail`、`REMOVE` 日志不刷屏、enabled 水位充足。

## 6. 不要做的事

- 导入已封 SSO 大礼包当「秒刷」
- 指望 Grok free 高 `cached_tokens`（通道限制；靠 sticky 即可）
- 在 live 号池上 `pool_maintain_purge_dead=true` + 硬删
- 把第三方不可用中转写进 Kimi `default_model` 冒充稳

## 7. 相关文件

- `cpa_xai/mint.py` / `authcode_mint.py` / `protocol_mint.py`
- `quota_watch.py` / `refresh_pool.py` / `pool_health.py` / `cpa_xai/usage.py`
- `clash_proxy.py` / `grok_register_ttk.py`（出口）
- `set_cliproxy_routing.py` / `POOL.md` / `KIMI_CLIPROXY.md`
