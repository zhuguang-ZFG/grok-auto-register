# 运行状态快照

> 记录时间：2026-07-13 06:00（本机 Asia/Shanghai 墙钟）  
> 仓库：`zhuguang-ZFG/grok-auto-register`  
> 不含密钥 / 号池 JSON / 订阅 token / `mail_credentials` / Hotmail 号池正文。

## 1. 进程与入口

| 组件 | 状态 | 备注 |
|------|------|------|
| CLIProxyAPI | 运行中 `:8317` | 本机 `cli-proxy-api`；`auth-dir=cpa_auths` |
| 注册机 `grok_register_ttk.py auto` | 运行中 | 隐藏窗口 / `concurrent_count=1` / `register_count=8` |
| `quota_watch` | 运行中 | soft-disable + 静默 refresh + buffer failover |
| Kimi CLI | `local-cpa/grok-4.5` → `http://127.0.0.1:8317/v1` | 本地号池 |

路由（CLIProxy）：

```text
strategy=round-robin
session-affinity=true
profile=cache
```

## 2. 号池水位（约 05:59）

| 指标 | 数值 |
|------|------|
| CPA 文件 | ~2431 |
| access 未过期（粗判） | ~2420 |
| disabled | ~1309（含 prefer_buffer soft-hold） |
| 自有域文件 | ~1138 / 目标 2000（~56.9%） |
| 缓冲域文件 | ~1293 |
| 策略 | **`pool_prefer_mode=buffer_first`**（先烧缓冲） |
| 缓冲 live（约） | ~1000+；failover 阈值 `pool_buffer_min_live=50` |

自有域名（`defaultDomains`）：

- `zhuguang.ccwu.cc`
- `lima.cc.cd`
- `zhuguang.de5.net`
- `baoxia.top`

缓冲来源示例：

- 社区包 `777.zip` → `*.oo-ooo.fun`（777，已 probe+全量 refresh）
- 其它共享域 / `unknown.local` 历史导入

Hotmail 铸造进 CPA（当缓冲弹药，不当 own 水位）：

| 指标 | 约值 |
|------|------|
| `@hotmail.com` CPA | ~26 |
| disabled / quota_cool | 0 / 0（样本仍小，新号为主） |

巡检：`python scripts/hotmail_cpa_health.py`

## 3. 邮箱与注册策略

| 项 | 值 |
|----|-----|
| `email_provider` | `cloudflare`（主路径） |
| CF Worker | `cloudflare_temp_email`（dreamhunter2333 系 API） |
| `mail_backends` | 自有三域 + `baoxia.top` 第二后端 |
| `email_mix_hotmail` | **true** |
| `email_mix_hotmail_ratio` | **0.35**（约 35% Hotmail / 65% 自有域） |
| Hotmail 池 | `data/hotmail_pool.txt`（~6.4 万 unique，gitignore） |
| 固定 OTP 备用 | `mailsapi_otp` + `mail_credentials.txt`（不进 bulk 主路径） |

出口：

- 统一 `http://127.0.0.1:7897`（Clash）
- `clash_rotate_per_account` + `clash_rotate_every_n=5`（弱多 IP，非每号独立住宅 IP）
- `http_proxy_enabled=false`（`all_proxies.txt` 未作主路径）

## 4. 近期质量（观察窗）

修复 CF 收码 `resend_callback` 参数名 + 验证码误抽欢迎信 `app-img` 之后：

| 窗口 | 成功率（约） |
|------|----------------|
| RESTART 后连续多轮 | **~100%**（多轮 8/0） |
| 更大日志尾部（含历史噪声） | **~90%** |

域名健康粗率：自有四域 ~95%+；`hotmail.com` 累计 ~85%+（含早期失败）。

## 5. 本阶段已落地（相对上一快照）

1. **分层号池**：`buffer_first` + 自有 soft-hold；`ensure_buffer_failover`（缓冲 live&lt;50 → 自动放开自有）挂 maintain / quota_watch / `set_pool_prefer.py check`  
2. **777 CPA 导入**：probe fuse + 全量 refresh 写入缓冲  
3. **Hotmail 号池**：入库、IMAP XOAUTH2 收码、注册机 `email_provider=hotmail` 与 **CF 混用 0.35**  
4. **mailsapi 固定 OTP**：备用通道，不替代 CF 批量  
5. **巡检脚本**：`scripts/hotmail_cpa_health.py`、`scripts/_restart_register_auto.py`  
6. **正确性修复**：`cloudflare_get_oai_code(..., resend_callback=)`；Hotmail 验证码过滤 Outlook 欢迎信  

## 6. 运维命令速查

```bat
python pool_status.py
python set_pool_prefer.py status
python set_pool_prefer.py check
python scripts/hotmail_cpa_health.py
python hotmail_pool.py
python hotmail_pool.py --smoke --imap-list
python grok_register_ttk.py auto
python scripts/_restart_register_auto.py
```

## 7. 已知边界

- 合盖/睡眠仍影响无人值守（见 `docs/UNATTENDED.md` / power 脚本）  
- sticky reselect 偏高时：查 soft-disable / REMOVE，勿硬删 live  
- 共享缓冲与 Hotmail 野号：注册成功率高 ≠ 长期不废；持续用 `hotmail_cpa_health` + hard_purge 盯  
- 避免同时跑多个 `auto`/`start`（抢浏览器）

## 8. 不提交内容

`config.json`、`cpa_auths/`、`data/hotmail_pool.txt`、`mail_credentials.txt`、tokens、代理明文列表。
