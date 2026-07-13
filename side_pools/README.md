# 旁路号池（非 Grok）

| 组件 | 路径 | 端口 | 作用 |
|------|------|------|------|
| Kiro-Go | `side_pools/kiro-go/` | **8080** | OpenAI/Anthropic 网关 + 号池 |
| KiroX_Cli | `side_pools/kirox-cli/` | — | AWS Builder ID / Kiro **自动注册** |
| 编排 | `scripts/kiro_side_pool_pipeline.py` | — | 邮池切片 → 注册 → 导入 8080 → probe |

**禁止**写入 `cpa_auths/`。Grok 主粮仍是 CLIProxy `:8317`。

## 一键

```bash
# 空网关
powershell -File scripts/start_kiro_go_side_pool.ps1

# dry-run（默认）：检查二进制 / 8080 / 代理 / 抽 1 封邮
python scripts/kiro_side_pool_pipeline.py --n 1

# live：注册 + 导入（可换 Clash 节点重试）
python scripts/kiro_side_pool_pipeline.py --live --n 1 --retries 3 --rotate-clash --proxy http://127.0.0.1:7897

# 仅导入已有 results.json
python scripts/kiro_side_pool_pipeline.py --import-only side_pools/kirox-cli/output/results_XXXX.json
```

Admin：http://127.0.0.1:8080/admin  
默认密码：`local-kiro-side-pool`（`ADMIN_PASSWORD` 可覆盖）

## 2026-07-13 实测

- dry-run：**PASS**
- live ×3（换 CDN 节点 + 新 hotmail）：均在 **SendOTP** 被 AWS  
  `{"errorCode":"BLOCKED","message":"Request was blocked by TES."}`  
- IMAP / OIDC / Portal 前序步骤正常；出口 IP 检测多为 `23.80.80.220`（Leaseweb）
- 空池 chat 仍为 `No available accounts`（预期）

TES = Amazon 反滥用。**2026-07-13 后半**：宝可梦家宽节点下 TES 已过，卡在 **注册后秒 suspended**（Kiro 安全锁）。

### 秒封时社区怎么说（摘要）

1. **没有**公开自动解封 API；官方文案：support 身份验证（[例 issue](https://github.com/kirodotdev/Kiro/issues/8253)）。
2. **KiroX**：住宅 + 低并发；FAQ 主写 OTP/TES，秒封靠 IP/节奏经验。
3. **kiro-register-en**：住宅 + **有头浏览器**（不要 headless）。
4. **kiro-account-manager**：管号侧识别 `suspended` 并停用；支持 **Google/GitHub Social**，不全是邮箱 Builder 批量。
5. 交流：KiroX TG `@kiroXaitg`、QQ（见上游 README）；LINUX DO 搜实时帖。

落地纪律：**只 rule 模式**；只切 `宝可梦` 组；`GLOBAL=DIRECT`；产号用 `-p http://127.0.0.1:7897`。

### 已实现的「社区向」缓解（本仓库）

| 手段 | 命令 / 位置 | 实测 |
|------|-------------|------|
| 默认跳过注册后 ListModels/用量 | kirox `-skip-verify`；pipeline 默认开启 | 仍能出 token；**首次 chat 仍可能 suspended**（封号在上游，不单是验活） |
| 强制验活 | `pipeline --verify` | 易在验活步直接 suspended |
| 延迟复检 | `python scripts/kiro_recheck_accounts.py --wait-min 30` | 用于养号后再 probe |
| suspended 仍落盘导入 | 本地 KiroX 补丁 | 号进 8080，enabled 可能被网关标 BANNED |
| 真浏览器 Playwright | `python scripts/kiro_pw_register.py --n 1` | **流程已通**（见下）；**秒封仍在** |
| Social 线 | 见 kiro-register-en、account-manager | **未接产线** |

### 2026-07-14 Playwright 封号率（headed）

详细过程：`docs/KIRO_PW_STATUS.md`。

| 指标 | 结果 |
|------|------|
| token 成功样例 | AdamsKinart / AdamsPurser / AdaThrift（3） |
| 导入 Kiro-Go | 3/3 `http=200` |
| 首聊 live | **0/3** → 全 `403 temporarily is suspended` → BANNED |
| 秒封率（有 token） | **100%（n=3）** |

```bash
# Playwright 产号（headed，浏览器勿挂显式 proxy）
python scripts/kiro_pw_register.py --n 1
python scripts/kiro_side_pool_pipeline.py --import-only side_pools/kirox-cli/output/pw_results_XXXX.json
python scripts/kiro_recheck_accounts.py --wait-min 0
```

**2026-07-14 结论**：真浏览器 **能稳定出 token + 导入**，**不能**单独解秒封。TES/流程问题与账号级 suspend 分层处理。真解仍依赖更干净 sticky 住宅、降频、Social、或官方申诉——**禁止**因此改 Clash 全局。
## 重建

```bash
cd _community_ref/cursor_kiro_research/Kiro-Go && go build -o ../../../side_pools/kiro-go/kiro-go.exe .
cd ../KiroX_Cli && go build -o ../../../side_pools/kirox-cli/kirox-cli.exe .
```
