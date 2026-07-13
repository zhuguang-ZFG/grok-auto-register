# 运行状态快照

> 记录时间：2026-07-13 15:37（本机 Asia/Shanghai 墙钟）  
> 仓库：`zhuguang-ZFG/grok-auto-register`  
> 不含密钥 / 号池 JSON / 订阅 token / `mail_credentials` / Hotmail 号池正文。

## 1. 进程与入口

| 组件 | 状态 | 备注 |
|------|------|------|
| CLIProxyAPI | **运行中** `:8317` | `D:\cli-proxy-api\cli-proxy-api.exe`；`auth-dir=cpa_auths`；`proxy-url=http://127.0.0.1:7897` |
| 注册机 `grok_register_ttk.py auto` | **运行中** | 15:34 经 `start_register_hidden.vbs` 恢复；`register_count=8` / `concurrent_count=1` |
| `quota_watch` | **运行中** | 15:34 经 `start_quota_watch_hidden.vbs` 恢复；`poll=15s` / soft-disable + pool rotate |
| Kimi CLI | `local-cpa/grok-4.5` → `http://127.0.0.1:8317/v1` | key 与 CLIProxy `api-keys` 一致；短 chat 实测 **200 / pong** |
| Dahl proxy（支线） | 可选 `:8330` | `start_dahl_proxy_hidden.vbs`；本地 key `sk-local-dahl`（非密钥，固定本地口令） |

路由（CLIProxy）：

```text
strategy=round-robin
session-affinity=true
session-affinity-ttl=4h
auth-auto-refresh-workers=16
```

### 中午中断与恢复（同日）

- ~12:54：`register_auto` / `quota_watch` 日志停更（进程不在）；号池文件仍有 `last_refresh`（CLIProxy 自刷新）。
- ~15:34：用 **VBS 隐藏启动** 恢复注册机 + 额度轮换；`scripts/_restart_register_auto.py` 在 Git Bash 下易误匹配命令行字符串，优先用 `start_register_hidden.vbs`。
- **Databricks 试用自动化**：OTP + select-product + CapSolver 可出 token；卡在 `setup-account` Continue（reCAPTCHA Enterprise 原生 getResponse）。**用户已放弃深挖 CDP**，不再推进 live 出号；代码与文档保留作半成品。

## 2. 号池水位（约 15:36）

| 指标 | 数值 |
|------|------|
| CPA 文件 | **~3631** |
| 未 disabled（粗判） | **~3140** |
| disabled | **~491** |
| 近 1h mtime 刷新 | ~223（多为 token refresh，非新注册） |
| `@hotmail.com` CPA | ~171 |
| 策略 | 见 `config.json` 的 `pool_prefer_mode`（本机，不入库） |

粗分域（文件名后缀，含缓冲导入）：

| 域 | 约数 |
|----|------|
| `*.oo-ooo.fun` 系（缓冲） | ~1500+ |
| `baoxia.top` | ~351 |
| `zhuguang.ccwu.cc` / `lima.cc.cd` / `zhuguang.de5.net` | 各 ~351 |

自有域名（`defaultDomains`）：

- `zhuguang.ccwu.cc`
- `lima.cc.cd`
- `zhuguang.de5.net`
- `baoxia.top`（第二 CF 后端）

社区 Cloud Mail 缓冲（`vip0.xyz` 等，**不进** `defaultDomains`）：

- 模块：`cloud_mail_otp.py`；纯用：`email_provider=cloud_mail`
- **混投**（主粮仍 CF）：`email_mix_cloud_mail=true` + `email_mix_cloud_mail_ratio=0.1`
  - 与 Hotmail 互斥切片：`[0,hm)` Hotmail，`[hm,hm+cm)` Cloud Mail，其余自有 CF
- 多后缀：`cloud_mail_domains`（默认 `["vip0.xyz"]`）+ `cloud_mail_domain_mode=random|rr|first`
  - 服务端 `domainList` 还有 vip9.cyou / sismi6.bond / news.cc.cd；主机常 403/停放，**默认只开 vip0**
  - 要扩域：`cloud_mail_domains: ["vip0.xyz","vip9.cyou",...]`，建箱失败会换下一域重试
- 凭证：`vip0_mail.local.json`（gitignore）；CapSolver + `POST /api/account/add`
- 收信：`GET /api/email/list?type=0`

缓冲来源示例：

- 社区包 `777.zip` → `*.oo-ooo.fun`（777，已 probe+全量 refresh）
- 其它共享域 / `unknown.local` 历史导入
- **未入库**：`D:\Downloads\auth-dir.7z`（@dogdogwang.xyz 抽样全 revoked）

Hotmail 铸造进 CPA（当缓冲弹药，不当 own 水位）：

| 指标 | 约值 |
|------|------|
| `@hotmail.com` CPA | ~171 |
| 池文件 | `data/hotmail_pool.txt`（gitignore） |

巡检：`python scripts/hotmail_cpa_health.py`

## 3. 邮箱与注册策略

| 项 | 值 |
|----|-----|
| `email_provider` | `cloudflare`（主路径） |
| CF Worker | `cloudflare_temp_email`（dreamhunter2333 系 API） |
| `mail_backends` | 自有三域 + `baoxia.top` 第二后端 |
| `email_mix_hotmail` | **true** |
| `email_mix_hotmail_ratio` | **0.35**（约 35% Hotmail / 65% 自有域） |
| Hotmail 池 | `data/hotmail_pool.txt`（gitignore） |
| 固定 OTP 备用 | `mailsapi_otp` + `mail_credentials.txt`（不进 bulk 主路径） |

出口：

- 统一 `http://127.0.0.1:7897`（Clash）
- `clash_rotate_per_account` + `clash_rotate_every_n=5`（弱多 IP，非每号独立住宅 IP）
- `http_proxy_enabled=false`（`all_proxies.txt` 未作主路径）

## 4. 支线：Dahl / Databricks / GLM

| 路径 | 状态 | 备注 |
|------|------|------|
| **Grok CPA + CLIProxy** | **主粮** | 见上；Kimi `local-cpa/grok-4.5` |
| **Dahl Inference** | 可用支线 | `docs/DAHL_PIPELINE.md`；chat 需浏览器会话；catalog 中 `zai-org/GLM-5.2-FP8` **chat unsupported** |
| **智谱 coding plan GLM-5.2** | 可用 | Kimi `zhipuai-coding-plan/glm-5.2`（非 Databricks） |
| **Databricks 14 天 $400** | **自动化未通 / 已停手** | `databricks_pipeline/` 保留；无 live host+token |

## 5. 本阶段已落地（相对 06:00 快照）

1. 号池扩到 ~3600+；Hotmail CPA 增至 ~171  
2. **Cloud Mail** 模块与测试：`cloud_mail_otp.py`、`tests/test_cloud_mail_otp.py`  
3. **Dahl 流水线**：`dahl_pipeline/`、隐藏启动、文档、quota 单测  
4. **Databricks**：Express 选择器、CapSolver 解 reCAPTCHA、email_bridge 加固；**setup-account 未过，用户放弃 CDP 深挖**  
5. 运维：注册机/quota 中断后用 VBS 恢复；CLIProxy 自带 token auto-refresh  

## 6. 运维命令速查

```bat
python pool_status.py
python set_pool_prefer.py status
python set_pool_prefer.py check
python scripts/hotmail_cpa_health.py
python hotmail_pool.py
python hotmail_pool.py --smoke --imap-list
wscript start_register_hidden.vbs
wscript start_quota_watch_hidden.vbs
wscript start_dahl_proxy_hidden.vbs
python grok_register_ttk.py auto
```

## 7. 已知边界

- 合盖/睡眠仍影响无人值守（见 `docs/UNATTENDED.md` / power 脚本）  
- sticky reselect 偏高时：查 soft-disable / REMOVE，勿硬删 live  
- 共享缓冲与 Hotmail 野号：注册成功率高 ≠ 长期不废；持续用 `hotmail_cpa_health` + hard_purge 盯  
- 避免同时跑多个 `auto`/`start`（抢浏览器）  
- Databricks setup-account reCAPTCHA Enterprise 未解；不要默认指望其出 live 号  

## 8. 不提交内容

`config.json`、`cpa_auths/`、`cpa_auths_dead/`、`data/hotmail_pool.txt`、`mail_credentials.txt`、`token.json`、`dahl_keys/*.local.json`、`vip0_mail.local.json`、`logs/`、`screenshots/`、代理明文列表、导入包 `_import_*` / `_community_ref/`。
