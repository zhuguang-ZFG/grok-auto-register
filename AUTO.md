# 全自动流水线（Grok 4.5 · 仅本机 Grok CLI）

唯一对外路径：注册 → CPA OIDC → `~/.grok/auth.json` → `grok` CLI / TUI（模型 `grok-4.5`）。
quota_watch 监听 CLI 自身日志，凭证失效/额度耗尽时自动换证，保持 `grok-4.5` 不停。

```
注册成功
  ├─ tokens.txt
  ├─ 本机 grok2api 池 token.json （可选，留作本地池）
  ├─ 远端 grok2api 池 （可选，见 config.grok2api_auto_add_remote）
  ├─ CPA OIDC 导出 -> cpa_auths/   （换号用的凭证池）
  └─ 本机 ~/.grok/auth.json        （grok CLI 实际读取）
```

## 一键持续注册

```powershell
cd D:\Users\grok-auto-register
# 建议用 scoop/系统完整 Python，不要用残缺嵌入式解释器
C:\Users\zhugu\scoop\apps\python313\current\python.exe grok_register_ttk.py auto
```

| 命令 | 含义 |
|------|------|
| `auto` / `--auto` | 持续循环（`auto_loop=true`，默认每轮 1 个，间隔 45s） |
| `start` / `--start` | 只跑一轮（本轮数量 = `register_count`） |
| `Ctrl+C` | 停止（连按两次强制退出） |

## 调用 Grok 4.5

唯一入口是本机 `grok` CLI（TUI / `grok` 命令），它读 `~/.grok/auth.json`：

```powershell
grok models      # 应出现 grok-4.5
grok             # 进入 TUI，/model grok-4.5
```

CPA probe 已验证 `grok-4.5` 在官方 `cli-chat-proxy` 侧可用。

## 额度耗尽 → 自动换号（本机 Grok CLI）

目标：官方 `auth.json` 额度/鉴权失效时，自动换凭证，无需手改。

```
本机 Grok CLI 用尽/401/429
  ├─ 扫 ~/.grok/logs/unified.jsonl（过滤第三方 NewAPI 噪声）
  ├─ 或 CPA probe（cli-chat-proxy /v1/models）
  ▼
优先 cpa_auths/ 池轮换写 auth.json
  └─ 池无可用 → 单次注册 + CPA + 写 auth.json
冷却 + 每日上限，避免连打注册
```

### 启动监视

```powershell
cd D:\Users\grok-auto-register
C:\Users\zhugu\scoop\apps\python313\current\python.exe quota_watch.py
```

| 命令 | 含义 |
|------|------|
| `quota_watch.py` | 常驻轮询（默认 20s） |
| `quota_watch.py --once` | 跑一轮检测后退出 |
| `quota_watch.py --once --dry-run` | 只检测，不换号/不注册 |
| `quota_watch.py --status` | 看当前 email / 池数量 / 上次触发 |
| `quota_watch.py --force-refill` | 立即池轮换或注册（绕过冷却） |
| `quota_watch.py --force-refill --dry-run` | 演练触发逻辑 |

### 相关配置（config.json）

| 键 | 默认 | 作用 |
|----|------|------|
| `quota_watch_enabled` | true | 总开关 |
| `quota_watch_poll_sec` | 20 | 日志扫描间隔 |
| `quota_watch_cooldown_sec` | 1800 | 两次补号最短间隔（秒） |
| `quota_watch_max_triggers_per_day` | 20 | 每日最多触发次数 |
| `quota_watch_probe_enabled` | true | 主动 CPA probe |
| `quota_watch_probe_kind` | `models` | probe 端点（`models`=凭证有效性；`responses`=chat，Free 号会 403） |
| `quota_watch_probe_interval_sec` | 300 | probe 间隔 |
| `quota_watch_prefer_pool` | true | 先用 `cpa_auths/` |
| `quota_watch_register_on_miss` | true | 池没有则跑 `grok_register_ttk.py start` |
| `quota_watch_min_pool` | 3 | CPA 池水位下限，低于则后台补号（**不碰当前 auth.json**） |
| `quota_watch_pool_topup_cooldown_sec` | 600 | 补号冷却（独立于换证冷却） |
| `quota_watch_pool_topup_max_per_day` | 30 | 每日补号上限 |
| `quota_watch_refresh_enabled` | true | 主动刷新 auth.json（临过期前用 refresh_token 续期） |
| `quota_watch_refresh_interval_sec` | 600 | 刷新检查间隔 |
| `quota_watch_refresh_margin_sec` | 1800 | 临过期前多久触发刷新（默认 30 分钟） |
| `local_grok_auth_auto` | true | 注册成功写 `auth.json`（必须开） |

状态文件：`.quota_watch_state.json`（offset / 冷却 / 已用池文件）。

**触发语义**：`429`/quota/rate-limit 文本、`401`/unauthorized/refresh failed → 换号；
`403 permission-denied`（Free 号在 chat 端点的权限拒绝，非额度）→ soft-fail，不换。

### 池水位自动补号（无感）

quota_watch 每轮还会检查 `cpa_auths/` 里**有效**（JWT 未过期）的 token 数。低于 `quota_watch_min_pool`（默认 3）时，后台注册一个新号补进池 + grok2api 远端池，**全程不碰正在用的 `auth.json`**——grok CLI 会话不受影响。补号有自己的冷却（`quota_watch_pool_topup_cooldown_sec`，默认 600s）和每日上限（30），不会连打注册。池轮换时自动跳过 JWT 已过期的死号，不再浪费时间探测。

### 主动 token 刷新（零中断关键）

CPA access_token 6 小时过期，但配有 refresh_token。quota_watch 每 10 分钟（`quota_watch_refresh_interval_sec`）检查 `auth.json` 的 token 是否临近过期（默认 `quota_watch_refresh_margin_sec=1800`，即过期前 30 分钟），是则用 refresh_token 主动向 `auth.x.ai` 续期，原子写回 `auth.json`，保留 email/user_id。**刷新失败不触发换号**——refresh_token 可能已失效，仍由正常换号逻辑兜底。这样 grok CLI 几乎不会因为 token 过期而中断。

### Windows 计划任务（开机/登录自启）

```powershell
cd D:\Users\grok-auto-register
powershell -ExecutionPolicy Bypass -File .\scripts\install_quota_watch_task.ps1
```

| 操作 | 命令 |
|------|------|
| 安装（登录时启动） | `.\scripts\install_quota_watch_task.ps1` |
| 立刻跑一次 | `Start-ScheduledTask -TaskName GrokQuotaWatch` |
| 查看状态 | `Get-ScheduledTask -TaskName GrokQuotaWatch \| Get-ScheduledTaskInfo` |
| 卸载 | `.\scripts\install_quota_watch_task.ps1 -Remove` |

任务名默认 `GrokQuotaWatch`，以当前用户交互登录触发，失败自动重试 3 次。

## 本机模型列表：为什么看不到「官方」模型

`/model` 或 `grok models` 里看到的名字分两类：

| 类型 | 例子 | 来源 |
|------|------|------|
| **官方 session 模型** | **`grok-4.5`** | 登录 `auth.json` 后从 `cli-chat-proxy.grok.com/v1/models` 拉取 |
| **自定义中转** | `free-az-grok-4-5`、`rainflow-grok-4-5`、`voya-*` | `~/.grok/config.toml` 的 `[model.*]` |

### 之前看不到官方模型的原因

1. **`auth.json` 格式不完整**：CLI 0.2.93 要求 entry 含 `auth_mode`、`create_time`（RFC3339）、`user_id`。缺字段会 `auth.json unreadable`，官方 catalog 拉不下来。
2. **`auth_mode` 不能是 `web_login`**：新版把 WebLogin 当 legacy，会拒绝/清空（日志：`ignoring legacy WebLogin token`）。CPA 导出写 **`auth_mode: "oidc"`**（`local_grok_auth.py` 已修好）。
3. **默认模型指向了中转**：`config.toml` 里 `default = "free-az-grok-4-5"` 时 TUI 默认走社区，不是官方 session。应改为：

```toml
[models]
default = "grok-4.5"
```

### 验证官方模型在不在

```powershell
grok models      # 应出现 grok-4.5
# TUI 内
/model grok-4.5
```

若仍只有 `*-az-*` / `rainflow-*`：检查 `auth.json` 是否被写成 `{}`，再跑注册/CPA 或 `quota_watch.py --force-refill`。

## 组件位置

| 组件 | 位置 |
|------|------|
| 注册机 | 本机 `D:\Users\grok-auto-register` |
| 邮箱 | Cloudflare Worker + 域名 `zhuguang.ccwu.cc` |
| 本机 Grok CLI 凭证 | `C:\Users\zhugu\.grok\auth.json` |
| CPA 凭证池 | `cpa_auths/` |

## 注意

1. 本机 `auth.json` 用 CPA OIDC，是 `grok-4.5` 的唯一对外路径。
2. 勿把 `config.json` / `token.json` / `cpa_auths/` 提交 git（已在 `.gitignore`）。
3. 池要够用：保持 `grok2api_auto_add_local`/`grok2api_auto_add_remote` 或定期 `auto` 批量注册补池。
