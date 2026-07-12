# 号池维持

## 目标

稳定小批量补号，域名轮换，控制节奏，本地始终有可用 `cpa_auths`。

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
| GrokPoolMaintain | 每 2h + 登录 | refresh→health→条件补号→status |
| GrokPoolRefresh | 每 2h | `refresh_pool --purge-dead` |
| GrokPoolHealth | 每 45m | 健康检查 |
| GrokQuotaWatch | 登录常驻 | 额度换号 |
| GrokRegisterAuto | 登录常驻 | 后台 auto 补号 |
| CLIProxyAPI-Local | 登录常驻 | 本地 8317 |

`pool_maintain` 已内置：临期 token 刷新 + 吊销 RT 清理（`pool_maintain_purge_dead`）。

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
