# 号池维持

## 目标

稳定小批量补号，域名轮换，控制节奏，本地始终有可用 `cpa_auths`。

## 加固基线（必读）

完整约定见 **[docs/HARDEN.md](docs/HARDEN.md)**（铸造三层、soft-disable、sticky、Clash 不掐连接、性能默认）。  
最新本机运行快照（无密钥）：**[docs/STATUS.md](docs/STATUS.md)**。  
Clash「注册专用」隔离：**[docs/CLASH_ISOLATE.md](docs/CLASH_ISOLATE.md)**。

要点速查：

- 铸造：`device → authcode → browser`；`cpa_probe_after_write=false`
- 耗尽：只 `disabled`，**不删** CPA 文件；恢复默认 6h 滚动
- CLIProxy：`session-affinity=true` + 较长 TTL；`set_cliproxy_routing.py cache`
- Clash：`clash_close_conns=false`、`clash_force_global=false`
- 号池大时：`concurrent_count=1`，`quota_watch_poll_sec≥15`

## 当前域名（config.defaultDomains）

```
zhuguang.ccwu.cc
lima.cc.cd
zhuguang.de5.net
baoxia.top
```

### 邮件后端（重要：别指错 Worker）

| 地址 | 域名 | 说明 |
|------|------|------|
| `https://cloudflare_temp_email.barbarhonmamxi20.workers.dev` | zhuguang.ccwu.cc / lima.cc.cd / zhuguang.de5.net | **当前正确号池后端**（v1.10.0） |
| `https://mail.kanxue.workers.dev` | 仅 baoxia.top | 旧实例 |
| `https://mail.baoxia.top` | 仅 baoxia.top | 旧实例别名 |

创建邮箱用公开接口：`POST /api/new_address`（无需 admin 密码）。  
拉信：`GET /api/mails?limit=20&offset=0`，Header `Authorization: Bearer <jwt>`。

验证：

```bash
python -c "from curl_cffi import requests; print(requests.get('https://cloudflare_temp_email.barbarhonmamxi20.workers.dev/open_api/settings').json()['domains'])"
```

## 日常命令

```bat
run_maintain.bat       :: 健康检查 + 不足则补号 + 同步 cli_live（计划任务用这个）
run_pool.bat           :: 只补号（不先健康检查）
run_pool.bat 8 1
run_pool.bat --status
python pool_health.py  :: 刷新 token / 踢死号 / 同步 CLI
python sync_cli_live.py
```

```bash
python status.py                 # 一键看板：号池+路由+进程+本机 auth
python pool_status.py
python pool_status.py --json     # 机器可读快照
python pool_status.py --json --procs
python pool_maintain.py
python grok_register_ttk.py -n 6 -c 1 -y
run_status.bat                   # Windows 快捷

# 外部 CPA zip / 目录导入号池
python import_cpa_batch.py D:/Downloads/batch_0001-0500.zip D:/Downloads/batch_0501-1000.zip

# 批量刷新临期/过期 access_token（有 refresh_token）
python refresh_pool.py --within-hours 3 --workers 3
python refresh_pool.py --domain lsw666.dpdns.org --within-hours 6 --max 400
```

### HTTP 代理列表（社区节点包）

支持 `host:port:user:pass` / `http://user:pass@host:port` 文本列表（如 7 天有效期 HTTP 节点）：

```bash
python http_proxy_pool.py status --path D:/Downloads/all_proxies.txt
python http_proxy_pool.py probe --path D:/Downloads/all_proxies.txt --sample 10
```

配置：

| 键 | 含义 |
|----|------|
| `http_proxy_list_path` | 列表路径 |
| `http_proxy_enabled` | 是否启用 |
| `http_proxy_prefer_over_clash` | true=优先 HTTP 列表，false=Clash 优先、失败再 HTTP |

说明：很多社区节点 **仅境外可达**，国内直连 probe 会全 FAIL；需境外机器或能出国的前置网络。

### 无感运维（计划任务）

```powershell
powershell -ExecutionPolicy Bypass -File .\enable_autonomy.ps1
# 卸载: powershell -ExecutionPolicy Bypass -File .\enable_autonomy.ps1 -Unregister
Get-ScheduledTask | ? TaskName -like 'Grok*'
```

| 任务 | 周期 | 作用 |
|------|------|------|
| GrokPoolMaintain | 每 2h + 登录 | **proxy_health**→refresh→health→条件补号→status |
| GrokPoolRefresh | 每 2h | `refresh_pool --purge-dead` |
| GrokPoolHealth | 每 45m | `proxy_health --rotate-if-bad` + pool_health + CLI 同步 |
| GrokQuotaWatch | 登录常驻 | 额度换号 |
| GrokRegisterAuto | 登录常驻 | 后台 auto 补号 |
| CLIProxyAPI-Local | 登录常驻 | 本地 8317 |

`pool_maintain` 已内置：临期 token 刷新。  
**加固默认** `pool_maintain_purge_dead=false`（硬删会触发 CLIProxy REMOVE 风暴）；吊销 RT 用 soft-disable。  
静默刷新也可由 `quota_watch` 周期调用 `refresh_pool.silent_refresh_pool`（见 `quota_watch_pool_refresh_*`）。

### 电源 / 睡眠 / 代理（补闭环缺口）

本机 7×24 需要 **插电 + 不睡 + Clash 稳**：

```powershell
# 插电：睡眠=从不，合盖=不采取任何操作（不影响息屏）
powershell -ExecutionPolicy Bypass -File .\scripts\ensure_power_awake.ps1
# 电池也禁用睡眠（费电，慎用）: ... -AlsoBattery

# 代理体检（Clash + 出口 IP + accounts.x.ai）；坏则换节点
python proxy_health.py
python proxy_health.py --rotate-if-bad
```

| 项 | 建议 |
|----|------|
| 插电 STANDBY | 0（从不） |
| 合盖 AC | Do nothing |
| 息屏 | 可关（省电，不杀进程） |
| Clash | 保持运行；`proxy_health` 进 maintain/health 任务 |
| 真 7×24 | 仍建议小 VPS；本机方案是「插电不睡」最小改动 |

说明：Windows Modern Standby 机型合盖仍可能进 S0 低电；若合盖后任务停，BIOS/厂商「合盖睡眠」再关一层，或合盖但接电外接屏。

### 自有域 / 缓冲域分层

- **自有**：`defaultDomains` 四域名  
- **缓冲**：其它域名（如 `lsw666.dpdns.org`）  

```bash
python set_pool_prefer.py status
python set_pool_prefer.py buffer   # 先烧缓冲：自有 soft-hold(disabled)，CLIProxy 只用缓冲
python set_pool_prefer.py own      # 恢复：自有重新进轮询，本地换号优先自有
```

| `pool_prefer_mode` | 行为 |
|--------------------|------|
| `own_first`（默认） | 本地换号优先自有；缓冲作后备 |
| `buffer_first` | 自有 `disabled+hold_reason=prefer_buffer`；先消耗缓冲额度 |

maintain 在 `buffer_first` 下会反复 re-hold 新注册的自有号。

### 域名健康与自动降权

注册成功/失败会写入 `.domain_health.json`（gitignore）。连续失败或成功率过低时，域名会被临时降权，选邮箱时优先其它域。

```bash
python pool_status.py                    # 含域名健康摘要
# 配置项见 config.example.json:
# domain_health_fail_streak_demote / domain_health_demote_sec / domain_health_min_success_rate
```

### CPA mint：协议优先（社区 v2 对齐）

注册拿到 `sso` 后，铸造默认 **先纯 HTTP Device Flow**（`cpa_xai/protocol_mint.py`），不弹铸造浏览器；失败再回退 `browser_confirm`。

| 配置 | 含义 |
|------|------|
| `cpa_prefer_protocol` | 默认 `true`：有 SSO 先协议铸造 |
| `cpa_protocol_only` | `true` 时协议失败不回退浏览器 |
| `cpa_protocol_poll_timeout_sec` | 协议轮询 token 超时（默认 90） |
| `cpa_protocol_attempts` | 整次协议重试次数（默认 2，瞬时 TLS） |
| `cpa_mint_rotate_egress` | 铸造前再轮换 Clash/HTTP 出口（默认 true） |
| `cpa_mint_rotate_on_tls` | 协议 TLS 失败时再换出口后重试（默认 true） |

社区参考：`_community_ref/grok_auto_register_share_20260712/`（分层 mail/session/credential；TempMail.lol；砍 headless 注册幻想）。  
本仓库仍以 `grok_register_ttk.py` 为主路径：

- **协议铸造** → `cpa_xai.mint_and_export` / `cpa_export`（默认开）
- **TempMail.lol** → `email_provider: "tempmail_lol"`（可选；默认仍用 cloudflare 四域名）

```json
"email_provider": "tempmail_lol",
"tempmail_lol_api_base": "https://api.tempmail.lol/v2",
"tempmail_lol_api_key": ""
```

协议铸造冒烟（有 SSO 的 accounts 行）：约 4s 写出 CPA + `/models` 含 grok-4.5。

外部 CPA 包导入：

```bash
python import_cpa_batch.py D:/Downloads/grok_cpa_30.zip
```

### CPA mint 工作池（R 注册 + M mint）

异步 mint 默认走有界队列，避免每号一条无限线程：

| 配置 | 含义 |
|------|------|
| `cpa_mint_workers` | mint 并发；`-1`=min(注册并发,4)；`0`=旧式无限线程 |
| `cpa_mint_queue_max` | 队列上限；`-1`≈2×workers；满则同步回退 |
| `cpa_mint_queue_block_sec` | 入队最长等待 |

### 号池抽检水位

`quota_watch` 可按间隔随机抽 `quota_watch_sample_probe_n` 个号做 `/models` 探测，用 live 比例缩放文件水位，避免「JWT 未过期但已死」挡住补号。

### 抗检测 A/B

- `anti_detect_viewport` / `anti_detect_tz_locale`：建议开（默认 True）
- `anti_detect_ua_pool`：建议关（真 Chrome UA 更稳；乱换 UA 易触发 CF）

### CLIProxy 路由策略（缓存 vs 号池）

| 命令 | 含义 |
|------|------|
| `python set_cliproxy_routing.py status` | 查看当前 |
| `python set_cliproxy_routing.py pool` | 轮询、关粘性（免费池默认，利换号） |
| `python set_cliproxy_routing.py cache` | 开 session-affinity（利 prompt 缓存命中） |

默认保持 `pool`。长会话想提高缓存命中再切 `cache`。

## Grok CLI 无感切换

1. 定时任务跑 `run_maintain.bat` 维持 `cpa_auths` 健康  
2. 健康号自动同步到 `cpa_auths/`  
3. CLIProxyAPI / Grok CLI 的 **auth-dir 指向 `cli_live`**  

详见 `cpa_auths/README.md`。

## 推荐 config 节奏（已写入）

| 项 | 值 | 原因 |
|----|----|------|
| register_count | 8 | 单批少而稳 |
| concurrency | 1 | 同出口 IP |
| batch_delay_sec | 15 + jitter 12 | 降批量特征 |
| batch_pause_every | 4 / 150s | 中途歇脚 |

## 计划任务（可选）

管理员 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_pool_task.ps1
# 或每 2 小时：
.\install_pool_task.ps1 -EveryHours 2
```

## 产物

- `accounts_*.txt`：email----password----sso
- `cpa_auths/xai-*.json`：CPA/OIDC
- `cpa_auths/cpa_push_pending.txt`：远端推送失败待重推
