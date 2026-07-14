# 运行状态快照

> 记录时间：2026-07-14 17:00（本机 Asia/Shanghai 墙钟）  
> 仓库：`zhuguang-ZFG/grok-auto-register`  
> 不含密钥 / 号池 JSON / 订阅 token / `mail_credentials` / Hotmail 号池正文。

## 1. 进程与入口

| 组件 | 状态 | 备注 |
|------|------|------|
| CLIProxyAPI | **主粮** `:8317` | Grok CPA；`auth-dir=cpa_auths`；`proxy-url=http://127.0.0.1:7897` |
| 注册机 `grok_register_ttk.py auto` | 长期稳跑路径 | 见 `HARDEN.md` / `COMMUNITY_THICKEN.md` |
| `quota_watch` | 主粮配套 | soft-disable + pool rotate |
| **chatgpt2api（K12）** | **运行中** `:8124` | 本地 clone（**不入库**）；auth-key 本机配置 |
| FlareSolverr | **运行中** `:8191` | scoop `flaresolverr@3.5.0`；给注册/CF 清障 |
| Kimi CLI | 多 provider | 默认见本机 `config.toml`；K12 别名见下 |

### Kimi 模型别名（K12 池）

| 别名 | 上游 model | max_context | 备注 |
|------|------------|-------------|------|
| `k12/gpt-5-5` | `gpt-5-5` | **1_000_000** | 已修正；`max_output=128k` |
| `k12/gpt-5` 等 | `gpt-5`… | **400_000** | 系列统一 |
| 全局 `reserved_context_size` | — | **50_000** | 长仓库更激进（文档默认） |

Provider：

```text
[providers.k12]
base_url = http://127.0.0.1:8124/v1
# api_key 仅本机 config.toml
```

网关注意：Kimi 默认 `reasoning_effort` 会触发 chatgpt2api → backend 422；本机已在  
`chatgpt2api/services/protocol/reasoning.py` **忽略 `reasoning_effort`**（只认原生 `thinking_effort`）。

## 2. K12 号池水位（约 16:50）

| 指标 | 数值 |
|------|------|
| 网关账号 | **~80_503** |
| 主来源 | `sub2api_…80500…zip` 真实 K12 快照 |
| plan_type | **k12** |
| refresh_token | **无**（短窗口，约至 **2026-07-23**） |
| workspace_id | `fc4f8db5-72cd-44cb-ae0d-fef1370a16c8` |
| chat 探测 | **OK**（经 Clash `:7897`） |
| 同批子集 | `k12.zip` / `sub2api-normal-…csv` 已 inspect，无额外 RT |

**不要**把 hotmail 自注册 free 当 K12 补号源：

- free 可注册且常有 RT  
- `invites/request` → `401 same domain`  
- `invites/accept` 可能 **假成功**（HTTP 200 仍 personal free）

## 3. 本阶段已落地（K12 支线）

1. **网关**：chatgpt2api 本地跑通 + 代理 + FlareSolverr clearance  
2. **导入**：80_500 真 K12 灌入网关；合成假数据已识别并拒绝  
3. **Kimi 接入**：`providers.k12` + 多模型别名；修上下文窗口与 `reasoning_effort` 422  
4. **运维脚本（入库）**  
   - `scripts/k12_pool_ops.py` — 状态 / 抽样 / 清 abnormal / watch（**默认不因 direct-check 误杀**）  
   - `scripts/k12_pool_monitor.py` — 存活率 + chat probe  
   - `scripts/k12_rt_import.py` — RT 感知导入（inspect/import/refresh-gateway）  
   - `scripts/k12_mother_invite.py` — 母号邀请链路（需母号 session）  
   - `scripts/k12_auto_register.py` — 调 chatgpt2api 内置注册机（free 后备，非 K12）  
   - `scripts/chatgpt2api_watchdog.ps1` — 网关看门狗  
5. **文档**：`docs/K12_POOL_HARDEN.md`（A/B/C 三线）  
6. **模块骨架**：`chatgpt_k12/`（注册/join/导出 pipeline；join 限制已写明）

## 4. Grok 主粮（摘要）

| 路径 | 状态 |
|------|------|
| Grok CPA + CLIProxy `:8317` | **主粮** |
| 智谱 coding plan | 可用 |
| Databricks 试用自动化 | **已停手**（setup-account reCAPTCHA） |
| Kiro 旁路号池 | 能出 token，**秒封率高**；见 `side_pools/README.md` |

## 5. 运维命令速查

```bat
REM --- Grok 主粮 ---
python pool_status.py
wscript start_register_hidden.vbs
wscript start_quota_watch_hidden.vbs

REM --- K12 网关 ---
python scripts/k12_pool_ops.py status
python scripts/k12_pool_ops.py sample-probe --n 20
python scripts/k12_pool_ops.py watch --interval 300 --probe-n 5 --auto-purge-abnormal
python scripts/k12_pool_monitor.py
python scripts/k12_rt_import.py inspect D:\Downloads\new_export.zip
python scripts/k12_mother_invite.py plan --workspace fc4f8db5-72cd-44cb-ae0d-fef1370a16c8

REM FlareSolverr（若未常驻）
flaresolverr
```

## 6. 已知边界 / 社区结论

- 共享 K12 **无 RT** → 到期即废；优先用、监控掉号  
- 裸 `GET /backend-api/accounts/check` 对共享 token 常 401，**网关 conversation 仍可能可用** → 禁止用 direct-check 批量禁用  
- free→K12：社区无“任意 hotmail 硬塞共享 workspace”稳解；需 **母号 invite** 或同域邮箱  
- chatgpt2api 注册需 **FlareSolverr + 代理**，否则 authorize CF 403  
- 合盖/睡眠影响无人值守（见 `docs/UNATTENDED.md`）

## 7. 不提交内容

`config.json`、`cpa_auths/`、`chatgpt2api/`（含 `data/accounts.json`）、`chatgpt_auths/`、`data/hotmail_pool*.txt`、`mail_credentials.txt`、`token.json`、`vip0_mail.local.json`、`logs/`、`screenshots/`、代理明文、导入包 `_import_*` / `_community_ref/`、本机 Kimi `config.toml`。
