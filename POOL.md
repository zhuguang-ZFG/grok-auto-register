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
python pool_status.py
python pool_maintain.py
python grok_register_ttk.py -n 6 -c 1 -y
```

### 域名健康与自动降权

注册成功/失败会写入 `.domain_health.json`（gitignore）。连续失败或成功率过低时，域名会被临时降权，选邮箱时优先其它域。

```bash
python pool_status.py                    # 含域名健康摘要
# 配置项见 config.example.json:
# domain_health_fail_streak_demote / domain_health_demote_sec / domain_health_min_success_rate
```

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
