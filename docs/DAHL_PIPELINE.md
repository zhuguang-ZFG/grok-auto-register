# Dahl Inference 全自动流水线

[Dahl](https://inference.dahl.global/)：OpenAI 兼容推理，`POST /tokens` 可直接发 key（文档称约 **1e8 tokens** 起步额度）。

## 为什么用浏览器会话

- `POST /tokens`、`GET /v1/models` 可用普通 HTTP。
- `POST /v1/chat/completions` 在本机对纯 `requests`/`curl_cffi` 返回 **Cloudflare 403**；**在已打开站点的 Chromium 里 `fetch` 则 200**。
- 因此全自动路径：`DrissionPage` 打开站点 → 页内 `fetch` 完成 mint/chat；本地再暴露 `127.0.0.1:8330/v1` 给 Kimi。

## 命令

```bash
# 端到端：领 key + 列模型 + chat + 落盘 dahl_keys/
python -m dahl_pipeline e2e --proxy http://127.0.0.1:7897

# 常驻代理（浏览器挂着，OpenAI 兼容）
python -m dahl_pipeline proxy --port 8330 --api-key sk-local-dahl
```

产物：

- `dahl_keys/active.local.json` — 含完整 token（**gitignore**）
- `dahl_keys/latest.json` — 无密钥摘要

## Kimi Code CLI

```toml
[[providers]]
name = "dahl-local"
type = "openai"
base_url = "http://127.0.0.1:8330/v1"
api_key = "sk-local-dahl"

# 模型 id 以 proxy 启动时 chat probe 通过的为准（2026-07-13 实测）：
# MiniMaxAI/MiniMax-M2.7  OK
# moonshotai/Kimi-K2.6    OK
# zai-org/GLM-5.2-FP8     可能出现在 catalog，但 chat 报 unsupported — 勿配进 Kimi
```


先 `python -m dahl_pipeline proxy`，再在 Kimi 里选上述模型。

## 与 Databricks / Grok

| | Dahl | Databricks 试用 | Grok CPA |
|--|------|-----------------|----------|
| 全自动领凭证 | **是**（本流水线） | 卡 reCAPTCHA | **是**（主产线） |
| 模型 | 开放权重类 | FM 企业侧 | Grok |
| 建议 | 第二/三 provider | 人肉备用 | 主粮 |

## 浏览器常驻问题（有解，但去不掉浏览器）

**事实（已测）：** 即使带上 `cf_clearance`，本机纯 HTTP/`curl_cffi` 调 chat 仍 **403**。  
因此 **不能**「过一次 CF 就关浏览器只留 key」——会话绑定在真实浏览器环境。

**工程解法（推荐）：把浏览器变成后台服务，而不是每次手动挂。**

| 手段 | 作用 |
|------|------|
| `start_dahl_proxy_hidden.vbs` | 无黑窗启动 proxy（同 Grok 注册机） |
| proxy 内置 **watchdog** | 浏览器崩了自动 `ensure()` 重开 + remint |
| 固定 profile ` .browser_profiles/dahl/proxy_main` | 重启少过 CF |
| 窗口甩到屏外（默认，非 headless） | 不挡视线；比 headless 更不易被 CF 加严 |
| `scripts/dahl_proxy_watchdog.ps1` + 计划任务 | 进程被杀后每 N 分钟自愈 |

```bash
# 一次启动（隐藏）
wscript start_dahl_proxy_hidden.vbs

# 或前台调试
python -m dahl_pipeline proxy --port 8330 --show-window

# 可选：计划任务每 5 分钟探活
# schtasks /Create /TN "DahlProxyWatchdog" /SC MINUTE /MO 5 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\Users\grok-auto-register\scripts\dahl_proxy_watchdog.ps1" /F
```

Kimi 只连 `http://127.0.0.1:8330/v1`，**不必关心**后面有没有 Chrome 窗口。

## 有限自动续额度（remint）

| 触发 | 行为 |
|------|------|
| HTTP **401**（key 失效） | 若当日未超 cap → `POST /tokens` 换 key 再试 |
| **402/429** 或 body 含 quota/credit 等 | 同上 |
| 本地估算 `available_tokens` **&lt; 阈值**（默认 5 万） | 主动 remint（仍计 cap） |
| 当日已达 **`remint_max_per_day`（默认 5）** | **停止换号**，请求失败，health 可见 remaining=0 |

状态文件：`dahl_keys/remint_state.json`（gitignore）。  
health：`GET /health` → `remint.remint_used_today` / `remint_remaining_today`。

```bash
python -m dahl_pipeline proxy --remint-max-per-day 5 --remint-low-threshold 50000
```

**不是无限续杯**：UTC 日切后 cap 归零；改大 cap 需自担风控。

## 限制

- **不能**无浏览器纯 HTTP 调 chat（CF）；只能守护浏览器会话。
- 免费额度与节点稳定性以官方为准；503 需重试。
- 勿把 `active.local.json` / `remint_state.json` 提交 git。


