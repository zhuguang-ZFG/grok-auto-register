# 域名邮箱部署与交接文档（zhuguang.ccwu.cc + Cloudflare 临时邮箱）

> 用途：为本项目（grok-register）提供**独享域名邮箱**，替代公共临时邮箱域名。
> 技术链：DNSHE 二级域名 → Cloudflare → `cloudflare_temp_email` worker → Email Routing catch-all → 本项目 cloudflare provider。

---

## 一、当前交接状态（2026-07-11 更新 #3）

| 步骤 | 状态 | 说明 |
| --- | --- | --- |
| 域名 NS | ✅ | `zhuguang.ccwu.cc` → aitana/robert.ns.cloudflare.com |
| `config.json` | ✅ | `cloudflare_api_base` 已填真实 Worker URL |
| 上游 fork | ✅ | https://github.com/zhuguang-ZFG/cloudflare_temp_email |
| D1 | ✅ | `temp-email-db` / `231dc81e-a490-41dc-9555-9bd75a082775`，schema 27 条已执行 |
| Worker 部署 | ✅ | GitHub Action 成功；URL 见下 |
| Email Routing | ✅ | enabled；catch-all → Worker `cloudflare_temp_email` |
| 创建邮箱 API | ✅ | `/api/new_address` 返回 `tmp…@zhuguang.ccwu.cc` |
| 跑通首个注册 | ⏳ | `python grok_register_ttk.py cli`（`register_count=1`） |

**Worker URL（当前）**

```text
https://cloudflare_temp_email.barbarhonmamxi20.workers.dev
```

**GitHub Deploy 记录**

- https://github.com/zhuguang-ZFG/cloudflare_temp_email/actions/runs/29147860890

---

## 二、架构

```text
Grok 验证码邮件
  → *@zhuguang.ccwu.cc
  → Email Routing catch-all → Worker cloudflare_temp_email
  → D1 temp-email-db
  → 本项目 GET /api/mails 轮询验证码
```

---

## 三、验证命令

```bash
python cf_mail_debug.py --api-base "https://cloudflare_temp_email.barbarhonmamxi20.workers.dev" --auth-mode none --create-path /api/new_address --domain zhuguang.ccwu.cc

python grok_register_ttk.py cli
```

admin 备选（匿名失败时）：`cloudflare_auth_mode=x-admin-auth`，`cloudflare_api_key` 用 `.arts/generated_secrets.txt` 里的 ADMIN_PASSWORD，`path`=`/admin/new_address`。

---

## 四、本地物料（.arts/，已 gitignore）

- `BACKEND_TOML.ready` — 已填 database_id
- `schema.sql` / `generated_secrets.txt`
- `auto_deploy.py`
- `cloudflare_temp_email/` — 本地 clone（部署用）

---

## 五、多 Worker / 多域名分摊（P1）

`mail_backends` 支持 **≥2 套** Cloudflare 临时邮箱 Worker，按域名路由创建与收信：

| Worker | 域名 | 创建路径 |
|--------|------|----------|
| `cloudflare_temp_email.barbarhonmamxi20.workers.dev` | `zhuguang.ccwu.cc` / `lima.cc.cd` / `zhuguang.de5.net` | 匿名 `/api/new_address` |
| `mail.kanxue.workers.dev` | `baoxia.top` | **匿名** `/api/new_address`（勿用错 `/admin/new_address` 除非 admin 密码可用） |

探活（创建 + 列信）：

```bat
python scripts/cf_mail_backends_health.py
```

要求：`create=200` 且 `mails=200`（`Authorization: Bearer <jwt>` + `limit/offset`）。  
`domain_health` 会临时降权连续失败域名；修后端后可清 `demoted_until` 或等冷却。

再挂第三套时：新域名 Email Routing → 新/同 Worker → `mail_backends` 追加一项 → 跑 health → 重启 register。

## 六、风险与注意

- 勿把 token / admin 密码提交仓库；对话里用过的 token 建议轮换。
- ccwu.cc 免费后缀存在整域拉黑风险；**用第二 Worker + 不同 TLD（如 baoxia.top）分摊**。
- 注册成功率仍主要取决于代理 IP 与 Turnstile。
- admin 路径 401 时优先改回匿名 `path_accounts=/api/new_address`，不要硬扛错误 admin key。
