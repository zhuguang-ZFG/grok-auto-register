# 运行状态快照

> 记录时间：2026-07-16（墙钟；**Grok 维持档 + K12 全禁 + 清池收口**）  
> 仓库：`zhuguang-ZFG/grok-auto-register`  
> 不含密钥 / 号池 JSON / 订阅 token / `mail_credentials` / Hotmail 号池正文。

## 0. 一句话

| 线 | 状态 |
|----|------|
| **Grok CPA 主粮** | 正常；注册机维持补号；网关 8317 实测 ~8s 出 token |
| **K12 共享池** | **全 disabled**（`fc4f8db5`）；勿作默认；禁止 pre_slim 灌回 |
| **默认路由** | Kimi → `local-cpa/grok-4.5`；Codex → `mycodex`（非 k12-local） |

## 1. 进程与入口

| 组件 | 状态 | 备注 |
|------|------|------|
| CLIProxyAPI | **主粮** `:8317` | Grok CPA；`auth-dir=cpa_auths`；`proxy-url=http://127.0.0.1:7897` |
| 注册机 `grok_register_ttk.py auto` | **维持档在跑** | 见 §4 |
| `quota_watch` | 在跑 | soft-disable + pool rotate（长跑 CPU 偏高时可加大 interval） |
| **chatgpt2api（K12）** | 可运行 / **池全禁** `:8124` | clone **不入库**；勿当默认 provider |
| FlareSolverr | 可选 | K12 死池时可减负只留 1 实例或停 |
| K12 stack / pool maintain | 计划任务 | maintain：**无头** Hidden；全 disabled 时 skip refill/probe |
| Kimi CLI | **默认 Grok CPA** | `local-cpa/grok-4.5`；备源 `cunai/*`；`k12/*` 勿手选 |
| Codex | **已切离 K12** | `mycodex-1782970213160`（sharedchat） |

### 注册机维持档（2026-07-15 固化）

| 键 | 值 | 说明 |
|----|-----|------|
| `register_count` | **3** | 自有域已超 target，维持而非冲量 |
| `concurrent_count` | **1** | |
| `block_media_fonts` | **false** | true 时资料页/Turnstile 成功率掉；代码已接线 `apply_bandwidth_saver`，默认关 |
| `enable_nsfw` | **false** | 跳过 grok.com 生日/NSFW（常 CF 403） |
| `cpa_probe_after_write` / `cpa_probe_chat` | **false** | mint 后不 probe，减误杀与上游流量 |
| `email_mix_tempmail_lol` / `mailtm` / `yunmeng` / `cloud_mail` | **关** | OTP 空等 / 501 disabled |
| `email_mix_hotmail_ratio` | ~0.30 | 主路径自有 CF 域 |
| `register_daily_success_cap` | **120** | |
| `sso_timeout_rotate_after` | **2** | |
| `clash_rotate_every_n` | **8** | |

**近期成功率（`logs/reg_metrics.jsonl`）**

| 窗 | 约成功率 |
|----|----------|
| 近 1h | ~73% |
| 近 3–6h / 今日 | **~76%–77%** |
| 铸造 protocol | 明显高于失败；主损耗在浏览器页/SSO |

### Kimi / Codex

```text
default_model = "local-cpa/grok-4.5"
# cunai: https://capi.cun.ai/v1 （本机 key；实测 glm 组）
# k12/* 别名可保留，勿默认
# Codex: mycodex-1782970213160，勿切回 k12-local 除非有新活 K12
```

## 2. K12 号池（全禁收口）

| 指标 | 数值 |
|------|------|
| live | **total≈1541，normal/ready=0，disabled=全量** |
| workspace | `fc4f8db5-…` 共享快照，无 RT |
| refill | **全 disabled → skip**；死 id 前缀黑名单 `fc4f8db5*` |
| maintain | Hidden 任务；`status --no-probe`；无浏览器 |
| 假活 | 全 disable 后 chat 仍可能 200 → **不以 chat 当复活** |

禁止：从 `pre_slim` 整库灌回同 workspace；free hotmail 当 K12 补号。

## 3. Grok 主粮水位（约数，会变）

| 项 | 约值 |
|----|------|
| CPA 文件 | **~4.7k**（enabled ~4.36k + disabled ~328） |
| 死号库 | **7009**（+3982 本轮清入，见 §4） |
| 自有域存活 | **10.6%**（2786 死 / 329 活；hotmail 仅 3.3%）→ 按管道指纹批量封 |
| prefer | `buffer_first` |
| 域名健康 | 自有 CF 域 ok 率多在 **0.96+**；hotmail 略低 |

## 4. 本阶段入库改动（相对上一推送）

1. **清池**：3982 个 `refresh_revoked` 确证死号（抽样 8/8 RT 服务端 revoked）从 `cpa_auths` 搬入 `cpa_auths_dead`（累计 7009）；池扫描提速  
2. **permission-denied 软禁用**：`cpa_xai/usage.py` 打标设 `recover_after=+24h`（env `GROK_POOL_PERM_DENIED_RECOVER_HOURS`），`reenable_recovered_accounts` 移出 terminal；`hard_purge_pool.py` 将其从 TERMINAL 挪到 HOLD（不白烧 RT 轮换）；存量旧文件已回填。**自愈实测 5/5 chat_ok 已放回**（24h 窗口）  
3. **生日修复证伪**（linux.do 2564817/2579539 对 cli 面不成立）：9 号 API 设生日全仍 403；TOS 接受+浏览器过墙+网页端发对话成功 → cli-chat-proxy 仍 403。结论写入 `AGENTS.md` 判死铁律第 4 条  
4. **封禁特征结论**：xAI 按注册管道指纹批量封（hotmail 死 97% 证非域名；binbim 同 tmp 命名 70% 活证非命名）  
5. 既有加固一并入库：chat 准入探针、`import_cpa_with_probe.py`、`pool_sample.py` / `quota_watch.py` / `mint.py` 系列、167/167 测试绿  
6. 新脚本：`scripts/repair_birthday_403.py`（复测+放回）、`scripts/merge_clash_grok_nodes.py`（**已回滚勿用**，见下）  

**⚠ clash/verge 勿碰**：本轮试合并节点进 mihomo 被喝止，已回滚（`.bak_20260716` 恢复 + reload）。不要再动 clash 配置。

**不入库（本机 only）**

- `config.json`、`cpa_auths/`、`chatgpt2api/`、Kimi `config.toml` / `mcp.json`  
- serial-mirror（`~/.kimi-code/tools/serial-mirror`，Agent 串口旁路，非本仓）

## 5. 运维速查

```bat
python pool_status.py
wscript start_register_hidden.vbs
wscript start_quota_watch_hidden.vbs

python scripts/k12_pool_refill.py status --no-probe
python scripts/k12_pool_refill.py refill --min-ready 800 --target 1800
REM 全 disabled 会 skip；新 workspace 才 --force

python scripts\cc_switch_codex_provider.py current
python scripts\cc_switch_codex_provider.py switch mycodex-1782970213160
```

## 6. 边界

- 共享 K12 无 RT；空间可整锅死  
- 16GB 机：双 Kimi / 双 A2A MCP / 注册 Chrome 易把 CPU 打满 → 减负见会话运维笔记  
- 合盖睡眠：`docs/UNATTENDED.md` / `ensure_power_awake.ps1`  
- 不提交：号池、密钥、代理明文、logs 正文、本机 clone 网关数据  

## 7. 不提交内容

`config.json`、`cpa_auths/`、`cpa_auths_quarantine/`、`chatgpt2api/`（含 `data/accounts.db` 与本机协议补丁）、`chatgpt_auths/`、`data/hotmail_pool*.txt`、`mail_credentials.txt`、`token.json`、`vip0_mail.local.json`、`logs/`、`screenshots/`、代理明文、导入包、`_import_*` / `_community_ref/`、`.omk/`、本机 Kimi/cc-switch 配置与 DB。
