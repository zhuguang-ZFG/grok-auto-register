# 社区方案 → 本机加厚对照

参考：站内 grok 注册机血缘（AaronL725 / maxucheng0 / grok--main 协议版）、CLIProxy 号池运维实践。

## 已对齐（你这边已有或本轮已合）

| 社区能力 | 本机 |
|----------|------|
| 有头注册 + CF 邮箱 | `grok_register_ttk.py` auto |
| 协议优先 CPA mint | `cpa_prefer_protocol` + `authcode` fallback |
| 异步 mint | `cpa_mint_async` |
| Clash 出口 / 注册专用组 | `clash_proxy` + config |
| 号池换号 / 额度 | `quota_watch` |
| 死号 vs 额度冷却 | `hard_purge` + `rescue_quota_holds` |
| 导入抽检熔断 + 只收存活 | `import_cpa_with_probe --refresh-all` |
| 水位只计自有 | `pool_watermark_own_only` |
| Turnstile 补丁 | `turnstilePatch/script.js`（screenXY + webdriver 合并；本机已含社区 anti-detect，强于上游纯 screenXY） |
| Chromium 轻量 flag | `chromium_mute_audio` 默认；`chromium_slim` 可选 |
| TabPool / 多线程 CLI | `tab_pool.py` + `register_cli.py`（可选，不改默认 auto） |
| 缓冲抽检 | `scripts/buffer_health_sample.py` |
| 分层号池：先烧缓冲 | `set_pool_prefer.py buffer` + soft-hold own |
| 缓冲低水位自动接自有 | `pool_policy.ensure_buffer_failover`（maintain / quota_watch / `check`） |
| SSO 超时换出口 | `sso_timeout_rotate_after` + 强制 `rotate_node`（不改 global） |
| 注册指标 JSONL | `logs/reg_metrics.jsonl`（成功/失败原因/换节点） |
| 日成功上限 | `register_daily_success_cap`（0=关；长期建议 200–500） |
| SSO 等待 + warmup | `sso_cookie_timeout_sec` + 等待期鼠标/滚动 + accounts 刷新 |
## 社区有、仍可选（未默认打开）

| 项 | 原因 | 何时开 |
|----|------|--------|
| `register_cli.py --threads N` | 多浏览器吃内存/代理 | 自有水位低且代理稳 |
| `chromium_slim: true` | 可能影响页面脚本 | 内存紧时试 |
| Hotmail 号池 + IMAP XOAUTH2 | 已接 `email_provider=hotmail` + **CF 混用** | `email_mix_hotmail=true` + `email_mix_hotmail_ratio=0.35`；巡检 `scripts/hotmail_cpa_health.py` |
| HTTP 代理池文件 | 与 Clash 注册组二选一为主 | 节点池更稳时 |
| 无头注册 | CF 常拦 | 协议+打码足够时再碰 |

## 推荐日常命令

```bat
python ops_heartbeat.py
python pool_status.py
python scripts/buffer_health_sample.py --sample 30
python scripts/import_cpa_with_probe.py D:\Downloads\pack.zip
python scripts/hard_purge_pool.py --scope buffer --max 500
python register_cli.py --help
```

## 原则

1. **缓冲当弹药，自有当基本盘**  
2. **共享包必 probe，禁止盲导**  
3. **加吞吐先稳代理，再加线程**  
4. **不覆盖本机 ops 去追 upstream 全文**  
5. **buffer_first 必须有低水位 failover**（否则缓冲烧光 + own hold = 空池）

### 缓冲自动接自有（本机默认）

```text
pool_buffer_failover_enabled: true   # 开
pool_buffer_min_live: 50             # buffer live < 50 → 放开自有 + own_first
pool_buffer_auto_recover: false      # 缓冲回升后是否再 hold 自有（默认关，防抖）
pool_buffer_recover_live: 120        # 仅 auto_recover=true 时生效
```

```bat
python set_pool_prefer.py status
python set_pool_prefer.py check
python set_pool_prefer.py buffer
python set_pool_prefer.py own
python scripts/hotmail_cpa_health.py
```

## K12 / chatgpt2api（2026-07-14）

社区参考：`basketikun/chatgpt2api`、`yukkcat/chatgpt2api`、NodeLoc join 油猴、`chatgpt-register-sub2api` / `chatgpt-register-k12`。

| 社区能力 | 本机固化 |
|----------|----------|
| 号池导入 CPA/sub2api | `scripts/k12_rt_import.py`（先 inspect，优先 K12+RT） |

| 单实例 watchdog + 日志轮转 | `scripts/k12_stack_watchdog.ps1`；`k12_pool_*` lock + log rotate；`chatgpt2api_watchdog.ps1` 启网关带 `STORAGE_BACKEND=sqlite` |
| Codex 本地 K12 | `scripts/codex_k12.ps1` / `.sh`（清 muyuan env 再起） |
| CPA 共享包熔断 | `import_cpa_with_probe`（本批 cpa-grok4.5-100 采样 0% 未入库） |
| 失效剔除 | 网关 `auto_remove_invalid_accounts` + `k12_pool_ops purge-abnormal` |
| 定时刷新 | `refresh_account_interval_minute`；有 RT 时 `k12_rt_import refresh-gateway` |
| CF 清障 | FlareSolverr `:8191` + `proxy_runtime.clearance.mode=flaresolverr` |
| 注册机补号 | `scripts/k12_auto_register.py` 调内置 register API（**free 后备，非 K12**） |
| 子号 join workspace | `k12_mother_invite.py`（**必须母号 invite**；request 同域限制；accept 需硬校验 plan_type） |
| 网关守护 | `scripts/chatgpt2api_watchdog.ps1` |
| 健康探测 | `k12_pool_monitor.py` / `k12_pool_ops.py status`（chat probe 为准） |

### 踩坑固化

1. **不要**用裸 `/accounts/check` 批量禁用共享 K12：常 401，但 conversation 仍可用。  
2. Kimi 默认 `reasoning_effort` → chatgpt2api 透传 → backend **422**；忽略该字段。  
3. Kimi `max_context_size`：`gpt-5-5`=1M，其它 gpt-5 系=400k；`reserved_context_size=50k`。  
4. 共享 K12 快照无 RT → 短窗口；可持续补号只能母号邀请或带 RT 新货。  
5. **不入库** `chatgpt2api/data/`、`chatgpt_auths/`、本机 auth-key 与号池正文。

细则：`docs/K12_POOL_HARDEN.md`、`docs/STATUS.md`。
