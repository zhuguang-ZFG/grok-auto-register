# Kiro Playwright 产号状态（2026-07-14）

> 主线：真浏览器 Playwright 注册 → token → 导入 Kiro-Go `:8080` → chat 测活。  
> **禁止**改 Clash 全局（`mode: rule`、`GLOBAL=DIRECT`）。浏览器**不要**显式 proxy（走 TUN）。  
> 结果只进 `side_pools/`，不写 `cpa_auths/`。

## 一句话结论

| 项 | 状态 |
|----|------|
| Playwright 流程（到 token） | **已通**（2026-07-14 ~02:38） |
| 导入 Kiro-Go | **已通** |
| 注册后 chat 是否 live | **仍秒封**（`temporarily is suspended` → BANNED） |
| 宣称「秒封已解」 | **否** |

## 组件与路径

| 路径 | 作用 |
|------|------|
| `scripts/kiro_pw_register.py` | Playwright 注册引擎（headed 默认） |
| `scripts/kiro_side_pool_pipeline.py --import-only <json>` | 导入 8080 |
| `scripts/kiro_recheck_accounts.py` | 盘上号 ban 状态 + chat probe |
| `side_pools/kirox-cli/output/pw_results_*.json` | 注册结果 |
| `side_pools/kirox-cli/work/sso_debug_*.png` / `otp_miss_*.png` | 失败截图 |
| `side_pools/kiro-go/` | 网关，admin 密码 `local-kiro-side-pool` |

## 流程水位（已验证）

```
OIDC client/register (httpx + :7897)
  → 本地 :3128 callback server
  → Chromium headed 无 proxy 打开 app.kiro.dev
  → Builder ID → 本地 signin params
  → oidc.../authorize → signin.aws 邮箱
  → profile.aws 姓名 → Confirm your name（若出现）
  → OTP（IMAP XOAUTH2：先 MS token 再连）
  → 密码（React native value setter，双 password 字段）
  → view.awsapps.com consent（Allow，非密码页 Continue）
  → 127.0.0.1:3128?code= 或 page.url 解析 code
  → OIDC token exchange → refreshToken
  → import 8080 → 首聊 403 suspended
```

## 关键修复（相对早期 no-callback / no-OTP）

1. **Name**：校验 `input_value`；JS Continue；处理 **Confirm your name**（勿点 Edit）。
2. **Password**：与 `kiro-register-en` 一致，用 `HTMLInputElement.prototype.value` setter + input/change 事件；提交后等字段消失。
3. **SSO 等待**：
   - 密码表单仍在时只重填密码，**禁止**把 Continue 当 consent；
   - consent 只点 Allow/Authorize/允许/授权；
   - 从 `page.url` 解析 `code=`（排除 `code_challenge`）；
   - `srv.shutdown()` + `server_close()` 释放 3128。
4. **OTP 失败 dump**：URL + 可见按钮/输入 + 截图。
5. **不要**挂 playwright-stealth 全量（会炸 kiro.dev `createElement`）。

## 成功样例

- 文件：`side_pools/kirox-cli/output/pw_results_20260714_023616.json`
- 邮箱：`AdamsKinart48844@hotmail.com`
- `status=success`，`refreshToken` 长度 232，`clientId` 有值
- 导入：`POST /admin/api/auth/credentials` → http 200
- 首聊：`HTTP 403` … `temporarily is suspended` → 盘上 `ban=BANNED`

## 失败模式（历史）

| error | 根因 | 处置 |
|-------|------|------|
| `no OTP input found` | 停在 name / Confirm / SPA 慢 | Confirm + 重试 Continue |
| `OTP not received` | IMAP / MS token 400 或信慢 | 换号重试；查 refresh_token |
| `no callback code` | 密码未真正提交，consent 误点 Continue | React setter + 密码优先 |
| `chrome-error://` | 导航/出口抖动 | 重试；勿改全局代理 |
| chat 403 suspended | 账号级安全锁 | 出口/节奏/Social；无公开自动解封 |

## 与 KiroX_Cli 对照

| | KiroX_Cli | Playwright |
|--|-----------|------------|
| TES / SendOTP | 干净节点可过 | 浏览器路径，OTP 页可到 |
| 出 token | 可 | **可** |
| 首聊 | 高比例 suspended | **样例 1/1 仍 suspended** |
| 根因假设 | 合成 TES 指纹 | 出口 ASN / 批量 hotmail / 无养号 |

公开仓库：**无**养号自动解封，仅识别 suspended 并停用。

## 纪律（仍有效）

- Clash：**不要** `mode: global`；产号 `http://127.0.0.1:7897` 仅 httpx；浏览器靠 TUN。
- NovProxy 住宅此前：大陆 IP / 链路异常，**暂不可用**。
- VLESS CF-WARP **不解秒封**。
- 稳定 live 前，pipeline **默认引擎不要**从 kirox 切到 pw 并宣称已解决。

## 命令

```bash
# headed 单号 / 多样本
python scripts/kiro_pw_register.py --n 1
python scripts/kiro_pw_register.py --n 3

# 导入 + 立即 recheck
python scripts/kiro_side_pool_pipeline.py --import-only side_pools/kirox-cli/output/pw_results_XXXX.json
python scripts/kiro_recheck_accounts.py --wait-min 0
```

## 封号率实验（2026-07-14 02:36–03:05，headed，固定流程）

出口：Clash rule + TUN（未改全局）。邮箱：hotmail 池。引擎：`scripts/kiro_pw_register.py`。

### 样本表（含调试轮）

| 时间 | 邮箱 | 结果 | 备注 |
|------|------|------|------|
| 02:36 | AdamsKinart48844 | **token OK** | 导入 200 → 首聊 **403 suspended** |
| 02:46 | AdamsPurser39852 | **token OK** | 导入 200 → **403 suspended** |
| 02:53 | AdanBernales… | fail no callback | 半途 OTP 复用/未进密码页（旧逻辑） |
| 02:57 | AdanSelvaggi… | fail invalid_grant | 误从 profile URL 抽 code（已修：仅 127.0.0.1/oidc） |
| 03:00 | AdaThrift586466 | **token OK** | 导入 200 → **403 suspended** |
| 03:03 | AddaButchko… | fail SSL EOF | code 已收到，token 交换网络 EOF（已加 4 次重试） |

### 比率（可引用）

| 指标 | 数值 |
|------|------|
| 明确 **token success** | **3**（Kinart / Purser / Thrift） |
| 其中导入成功 | **3/3** |
| 其中 **live chat** | **0/3**（全部 `temporarily is suspended` → BANNED） |
| **秒封率（有 token 的号）** | **100%**（n=3） |
| 流程失败（OTP/callback/SSL/误 code） | 调试中另有若干，不计入秒封分母 |

### 解读

1. **产 token 已可重复**（非单次运气）；连续 headed 可在约 2–3 分钟/号完成 happy path。  
2. **真浏览器不能解秒封**（至少在当前 hotmail + 当前出口下）。  
3. 失败噪声：IMAP 400、密码页偶发不提交、profile 假 code、OIDC SSL EOF——脚本已分别加固，**与账号是否 suspended 无关**。  
4. 盘上 recheck（含历史 kirox 号）：**全部 BANNED / No available accounts**。

## 下一步候选

1. 换 **sticky 住宅 / 非机房 ASN** 后再跑 n≥5 看 live 是否 >0（仍不改 Clash 全局）。  
2. `--wait-min 30` 延迟 recheck 对照（预期仍 suspended）。  
3. Social 注册线调研（未接产线）。  
4. token 交换 SSL 抖动：已重试 4 次；仍失败则换节点后 `--import-only` 前先本地 `oidc/token` 复验。