# Grok 4.5 号池快速耗尽 — 原因与修复（2026-07-18）

## 现场快照（修复前）

| 指标 | 值 |
|------|-----|
| `cpa_auths` | ~608（enabled ~465，disabled ~143） |
| 粗判 access 未过期 | ~65（与 heartbeat live≈81 同量级） |
| `cpa_auths_quarantine` | **9322**（quota≈896，refresh_revoked≈5282） |
| `cpa_auths_dead` | ~7517 |
| 注册机 | **未运行** |
| CLIProxy 路由 | `round-robin` + **`session-affinity: false`** |
| keepalive | 一轮 **469 号 chat `/responses`** |

## 根因（按影响排序）

### 1. 自伤：探活 / 保活在烧 free 额度（主因）

社区实测（linux.do [2562837](https://linux.do/t/topic/2562837)）：

- free 单号 **~2M tokens / 滚动 ~24h**
- 错误码：`subscription:free-usage-exhausted` / `grok-4.5-build-free`

本机违规默认（相对 `docs/HARDEN.md`）：

| 项 | 危险值 | 加固值 |
|----|--------|--------|
| `cpa_probe_chat` | **true** | **false** |
| `cpa_probe_after_write` | **true** | **false** |
| `quota_watch_sample_probe_n` | **5 / 60s** | **0** |
| `cpa_keepalive` | 默认 **chat** | 默认 **models-only** |

一轮 keepalive 对 469 个号各打一次 chat，等于把弹药打成「探活税」。  
pool health 日志里大量 `quota_exhausted` 与此同向。

### 2. 路由：关 affinity + 纯 round-robin → 全池匀速烧光

`config.yaml` 曾注释「free churn 关 sticky」。结果：

- 每个请求换号 → **所有号并行撞 2M 窗**
- sticky miss / reselect 上升，prompt cache 失效 → **同样任务更多 token**

社区 / 本仓 HARDEN：`session-affinity: true`，TTL **1h**（Grok 池）。

### 3. 补水停了 + 号龄天花板

- free/cli-chat-proxy **约 24–48h 寿命**（`ban_regression.py` / `AGENTS.md`）
- 注册机停 → 只出不进
- 共享缓冲域（ak1314 / kongbao…）占比高，寿命更短

### 4. 复测逻辑把软 hold 当死号

`retest_quarantine.py` 原逻辑：非 403 → **discard**。  
429 / `quota_exhausted` 被丢掉，无法 6h 滚动回池。

`iter_quarantined` 还 **跳过 `disabled: true`**，导致带 soft-disable 的 quarantine 永远进不了复测队列（426 个 `quota_exhausted` 卡死）。

### 5. 非根因（勿再烧成本）

- 改 UA / 出口 / 指纹：号龄回归已证伪主轴
- 生日 / 网页 TOS：解不了 cli-chat-proxy 403
- `invalid_grant`：先 `raceguard`，不是号死

## 已落地修复

1. **config.json**：`cpa_probe_*=false`，`quota_watch_sample_probe_n=0`，`register_count=3`
2. **CLIProxy**：`set_cliproxy_routing.py cache` + `session-affinity-ttl: "1h"`
3. **`scripts/cpa_keepalive.py`**：默认 `/models`；`--chat` 才 responses；429/403 → soft-disable
4. **`scripts/run_keepalive.ps1`**：去掉默认 chat 假设
5. **`scripts/retest_quarantine.py`**：quota/403/network **extend hold**，仅 auth/anti-bot/revoked discard
6. **`cpa_xai/quarantine.py`**：`iter_quarantined` 不再因 `disabled` 跳过 soft hold；`move_to_live` 清 `disabled`/quota_state

## 运维动作（建议立即）

```bat
REM 1) 启动注册机维持档（水位塌时）
wscript start_register_hidden.vbs

REM 2) 复测 quarantine 里到期的 soft hold（models-only 对 quota）
python scripts/retest_quarantine.py

REM 3) 确认路由
python set_cliproxy_routing.py status

REM 4) 水位
python pool_status.py
```

## 社区交叉

| 帖 | 结论 |
|----|------|
| 2562837 | free 2M / 24h 刷新 |
| 2584634 | 官方 capacity 满 ≠ 号死 |
| 2561769 | 走 `cli-chat-proxy` + grok-cli headers 吃 build 额度 |
| COMMUNITY_ABSORB / HARDEN | 403 soft；禁止 mint 后 chat probe；sticky 省 token |

## 原则（写进日常）

1. **测活先 AT/models，chat 是破坏性探活**  
2. **quota = soft hold 6h，不是 dead**  
3. **sticky 减跨号烧额度 + 利 cache**  
4. **free 当 24–48h 耗材：持续补号，别赌长寿**  
5. **禁止全池 chat 扫 / 高密度 sample_probe**
