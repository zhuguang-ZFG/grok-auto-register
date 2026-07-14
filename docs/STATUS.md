# 运行状态快照

> 记录时间：2026-07-14 19:50（本机 Asia/Shanghai 墙钟）  
> 仓库：`zhuguang-ZFG/grok-auto-register`  
> 不含密钥 / 号池 JSON / 订阅 token / `mail_credentials` / Hotmail 号池正文。

## 1. 进程与入口

| 组件 | 状态 | 备注 |
|------|------|------|
| CLIProxyAPI | **主粮** `:8317` | Grok CPA；`auth-dir=cpa_auths`；`proxy-url=http://127.0.0.1:7897` |
| 注册机 `grok_register_ttk.py auto` | 长期稳跑 | SSO 超时 180s；连续 1 次 sso_timeout 即换 Clash 节点 |
| `quota_watch` | 主粮配套 | soft-disable + pool rotate |
| **chatgpt2api（K12）** | **运行中** `:8124` | 本地 clone（**不入库**）；`STORAGE_BACKEND=sqlite` → `data/accounts.db`；auth-key 本机 only |
| FlareSolverr | **运行中** `:8191` | scoop `flaresolverr@3.5.0` |
| K12 stack watchdog | 计划任务 `K12StackWatchdog` | 登录自启；`scripts/k12_stack_watchdog.ps1` |
| Kimi CLI | 多 provider | 默认 `local-cpa/grok-4.5`；K12 见 `/model k12/gpt-5.6` |
| Codex | 本地 K12 provider | `k12-local-chatgpt2api`；CLI schema 见下 |

### Kimi 模型别名（K12 池）

| 别名 | 上游 model | max_context | 备注 |
|------|------------|-------------|------|
| `k12/gpt-5.6` / `sol` / `terra` / `luna` | 同名 | **1_000_000** | 无 thinking capability（防 reasoning_effort 422） |
| `k12/gpt-5-5` 等 | `gpt-5-5`… | **1_000_000** | |
| `k12/gpt-5` 等 | `gpt-5`… | **400_000** | |
| 全局 `reserved_context_size` | — | **50_000** | |

```text
[providers.k12]
base_url = http://127.0.0.1:8124/v1
# api_key 仅本机 config.toml（勿提交）
```

### Codex / cc-switch

| 项 | 说明 |
|----|------|
| Provider id | `k12-local-chatgpt2api` → `http://127.0.0.1:8124/v1`，`wire_api=responses`，`model=gpt-5.6`，`reasoning=none` |
| 启动 | `scripts/codex_k12.ps1` / `codex_k12.sh`（清 muyuan `OPENAI_*` env） |
| CLI 卡壳 | GUI **3.17** 把 DB 升到 **schema v13**；SaladDay CLI **5.9.0 最高 v11** |
| 绕过 | `python scripts/cc_switch_codex_provider.py list\|current\|switch <id>` |

## 2. K12 号池水位（约 19:40）

| 指标 | 数值 |
|------|------|
| 网关账号 | **~80_507**（导入 skip 后略有浮动） |
| 主来源 | `sub2api_…80500…zip` 真 K12 快照 |
| plan_type | **k12** |
| refresh_token | **无**（短窗口，约至 **2026-07-23**） |
| workspace | 以 `fc4f8db5-72cd-44cb-ae0d-fef1370a16c8` 为主 |
| 服务健康 | **`GET /healthz`** + chat/responses 探针（SSOT） |
| 同批子集 | `alive.zip` / `authconv_500…zip` / 前 500 split → **import skip 全量** |

**不要**把 hotmail 自注册 free 当 K12 补号源：

- free 可注册且常有 RT  
- `invites/request` → `401 same domain`  
- `invites/accept` 可能 **假成功**（HTTP 200 仍 personal free）

## 3. 本阶段已落地（K12 支线 · 加厚）

1. **网关**：SQLite 迁库；`auto_remove_invalid_accounts`；FlareSolverr clearance  
2. **空响应轮换**（本机 clone，不入库）：`stream_text_deltas` 在 200 无 SSE 内容时换号  
3. **探活**：`GET /healthz` 轻量；`/health?format=json` 仍做完整 stats  
4. **运维脚本（入库）**  
   - `scripts/k12_pool_ops.py` — watch 默认 `--probe-n 0`；单实例 lock；日志轮转  
   - `scripts/k12_pool_monitor.py` — 单实例 lock  
   - `scripts/k12_stack_watchdog.ps1` + `install_k12_stack_watchdog_task.ps1`  
   - `scripts/k12_rt_import.py` / `k12_mother_invite.py` / `chatgpt2api_watchdog.ps1`（启网关带 sqlite env）  
   - `scripts/codex_k12.ps1` / `.sh`  
   - `scripts/cc_switch_codex_provider.py`  
   - `scripts/sso_batch_to_cpa.py` — **Grok SSO→CPA**（非 K12）  
5. **文档**：`docs/K12_POOL_HARDEN.md`、`docs/K12_DOMAIN_RESEARCH.md`、`docs/COMMUNITY_THICKEN.md`  
6. **模块骨架**：`chatgpt_k12/`  

## 4. Grok 主粮（摘要）

| 路径 | 状态 |
|------|------|
| Grok CPA + CLIProxy `:8317` | **主粮** |
| `cpa_auths/` | 持续自注册 + 社区 SSO 批量 mint（binbim 等） |
| 社区 CPA zip（已 revoke RT） | **熔断不入库**（`import_cpa_with_probe`） |
| 智谱 coding plan | 可用 |
| Databricks 试用自动化 | **已停手** |
| Kiro 旁路号池 | 秒封率高；见 `side_pools/README.md` |

## 5. 运维命令速查

```bat
REM --- Grok 主粮 ---
python pool_status.py
wscript start_register_hidden.vbs
wscript start_quota_watch_hidden.vbs
python scripts/import_cpa_with_probe.py D:\Downloads\pack.zip
python scripts/sso_batch_to_cpa.py D:\Downloads\output.zip --concurrency 2

REM --- K12 网关 ---
curl http://127.0.0.1:8124/healthz
python scripts/k12_pool_ops.py status
python scripts/k12_pool_ops.py watch --interval 300 --probe-n 0 --auto-purge-abnormal
python scripts/k12_pool_monitor.py --watch --interval 300
python scripts/k12_rt_import.py inspect D:\Downloads\new_export.zip
powershell -ExecutionPolicy Bypass -File scripts\k12_stack_watchdog.ps1
powershell -ExecutionPolicy Bypass -File scripts\install_k12_stack_watchdog_task.ps1

REM --- Codex 切 K12 ---
python scripts\cc_switch_codex_provider.py switch k12-local-chatgpt2api
.\scripts\codex_k12.ps1

REM FlareSolverr（若未常驻）
flaresolverr
```

## 6. 已知边界 / 社区结论

- 共享 K12 **无 RT** → 到期即废；优先用、监控掉号  
- 裸 `GET /backend-api/accounts/check` 常 401 → **禁止** direct-check 批量禁用  
- free→K12：需 **母号 invite** 或同域邮箱；无造假入学/SheerID  
- 网关忙时完整 `/health` 可能慢 → 用 **`/healthz`**  
- cc-switch CLI 等上游支持 schema v13 后再 `cc-switch update`  
- 合盖/睡眠影响无人值守（见 `docs/UNATTENDED.md`）

## 7. 不提交内容

`config.json`、`cpa_auths/`、`chatgpt2api/`（含 `data/accounts.db` 与本机协议补丁）、`chatgpt_auths/`、`data/hotmail_pool*.txt`、`mail_credentials.txt`、`token.json`、`vip0_mail.local.json`、`logs/`、`screenshots/`、代理明文、导入包、`_import_*` / `_community_ref/`、本机 Kimi/cc-switch 配置与 DB。
