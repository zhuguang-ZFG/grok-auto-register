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
| `cpa_probe_after_write` | **`false`**（本机已关） | 铸造后立刻 chat probe 易 `permission-denied` 误杀；延后交给 `quota_watch`；亦省上游流量 |
| `cpa_probe_chat` | **`false`** | 与上配套；勿 mint 后立刻 chat |
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

可选彻底隔离：见 **[CLASH_ISOLATE.md](CLASH_ISOLATE.md)**（rule 模式 + 分组 `注册专用` + `clash_selector`）。

## 4. 本机性能 / 省流（号池已大时）

注册机流量大头在 **有头 Chromium 拉注册页静态资源**（图/字体/追踪），不是 CPA 协议 mint。

| 键 | 建议（本机已落地） | 原因 |
|----|-------------------|------|
| `concurrent_count` | **`1`** | 多开浏览器 = 流量×N + 内存 |
| `register_count` | **`3`**（满水位维持） | 自有域已达标时别每轮 8 号 |
| `enable_nsfw` | **`false`** | 跳过 grok.com 生日/NSFW（常 403），省一次 CF 往返 |
| `auto_loop_pause_sec` | **`180`** | 降 CF/出口压力与失败重试流量 |
| `browser_restart_every` | **`3`** | 更勤清 profile，防泄漏；略增启动成本但减异常 |
| `block_media_fonts` | **默认 `false`（本机当前）**；满池可试 `true` | 省流；若资料页/Turnstile 掉成功率立刻改回 `false` |
| `chromium_slim` | `true` | mute + 减后台网络等 flag |
| `chromium_mute_audio` | `true` | |
| `cpa_mint_workers` | **`1`** | |
| `cpa_probe_after_write` | **`false`** | 铸造后不 probe，省上游流量 + 减误杀 |
| `cpa_probe_chat` | **`false`** | 同上；存活交给 `quota_watch` |
| `register_daily_success_cap` | **`120`** | 满池后限制日成功，防尖刺与浪费流量 |
| `sso_timeout_rotate_after` | **`2`** | 少因单次超时就换节点（换节点也费流量） |
| `clash_rotate_every_n` | **`8`** | 降频换出口 |
| `quota_watch_sample_probe_n` | `0` | 禁止抽样 chat 扫池 |
| `pool_probe_on_health` | `false` | 禁止全池 `/models` |

### 4.0 省流注意

- `block_media_fonts=true` 后若 **CF/OTP 成功率明显掉**，改回 `false` 并看 `TURNSTILE.md`。  
- 协议 mint（`cpa_prefer_protocol`）本身很轻；**别为了省流去 headless**（CF 更重、重试更费流量）。  
- 水位已满时最大省流 = **少开浏览器轮次**（count/pause/cap），比抠静态资源更有效。

### 4.1 注册指标（2026-07-14）

```bash
# 日成功/失败粗看
python -c "import json,collections; c=collections.Counter();
from pathlib import Path
p=Path('logs/reg_metrics.jsonl')
if p.exists():
  for l in p.read_text(encoding='utf-8').splitlines():
    try: r=json.loads(l); c[(r.get('event'), r.get('reason'))]+=1
    except: pass
print(c)"
```

事件：`success` / `fail`（reason: `sso_timeout|otp|cloudflare|timeout|blocked|other`）/ `egress_rotate` / `cap_hit`。
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

## 7. CLIProxy 出口加固（防 region block / 死代理）

### 7.1 全局 proxy-url（必须）
CLIProxy `config.yaml` **必须**设 `proxy-url: "http://127.0.0.1:7897"`（Clash）。
不设则直连出口 → 数据中心 IP（SG/DO/等）被 grok 判 `403 region-denied`，
CLIProxy 轮换所有号都 403 → 20s 超时 500。

### 7.2 CPA 文件 proxy 字段污染（已知坑）
部分导入的 CPA 文件带 `proxy: http://127.0.0.1:18478`（旧端口），
**覆盖**全局 proxy-url → CLIProxy 用死端口 refresh token → 全部失败。

修复 + 预防：
```bat
:: 清除所有 CPA 文件中的死 proxy 字段（保留 7897）
python scripts\clean_cpa_proxy.py
```

`clean_cpa_proxy.py` 扫 `cpa_auths/xai-*.json`，删除非 7897 的 per-auth proxy。
建议注册机每次导入号后跑一次，或挂进 `pool_maintain`。

### 7.3 出口节点区域
`注册专用` 组只选 grok 接受的区域：**TW / HK / US / JP**。
**禁止**：SG（新加坡）、DE（德国）、RU 等 → region block。

验证链路（社区 403 排查法）：
```bat
python pool_status.py          :: 看代理健康
curl -s -m 10 -x http://127.0.0.1:7897 https://ifconfig.me  :: 出口 IP
:: 直连 cli-chat-proxy 看是否 region block
curl -s -m 10 -x http://127.0.0.1:7897 "https://cli-chat-proxy.grok.com/v1/models" -H "Authorization: Bearer <token>"
```

## 8. 相关文件

- `cpa_xai/mint.py` / `authcode_mint.py` / `protocol_mint.py`
- `quota_watch.py` / `refresh_pool.py` / `pool_health.py` / `cpa_xai/usage.py`
- `clash_proxy.py` / `grok_register_ttk.py`（出口）
- `set_cliproxy_routing.py` / `POOL.md` / `KIMI_CLIPROXY.md`
- 最新本机快照（无密钥）：[STATUS.md](STATUS.md)
- Clash 专用组步骤：[CLASH_ISOLATE.md](CLASH_ISOLATE.md)


## Import fuse

`python scripts/import_cpa_with_probe.py <pack>` — sample RT refresh; abort if ok_rate < 0.7. Watermark counts **own domains only** (`pool_watermark_own_only`).


## P1 hard_purge + import survivors (2026-07-13)

- **hard_purge default scope=buffer**, max 500/run, maintain interval **6h** (`pool_maintain_hard_purge_every_hours`).
- Unknown `disabled` with RT is **probed** (not forever hold).
- **import_cpa_with_probe**: sample fuse, then `--refresh-all` (default) only writes RT-ok survivors.
  ```bat
  python scripts/import_cpa_with_probe.py D:/Downloads\pack.zip
  python scripts/hard_purge_pool.py --scope buffer --max 500
  python scripts/hard_purge_pool.py --scope all
  ```
