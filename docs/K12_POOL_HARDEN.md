# K12 号池三线加固（A/B/C）

更新：2026-07-14

## 背景

- 已导入共享 K12：约 80500（`plan_type=k12`，**无 refresh_token**，窗口约至 7/23）
- 自注册 hotmail free：有 RT，但 **不能 request 进共享 K12**（同域限制）
- `invites/accept` 可能 HTTP 200 但未真正入 K12（社区称假成功）

社区参考：
- NodeLoc 油猴 join 脚本（强调本地检测，避免假成功）
- `chatgpt-register-sub2api` / `chatgpt-register-k12`（注册+join+导出）
- chatgpt2api 号池：失效剔除 / 刷新 / 代理 clearance

## A. 把现有 80500 用到极致

脚本：`scripts/k12_pool_ops.py`

```bash
# 状态 + 聊天探测
python scripts/k12_pool_ops.py status

# 抽样探测（默认只观察；共享 K12 的 /accounts/check 常 401，不能当死号依据）
python scripts/k12_pool_ops.py sample-probe --n 30

# 只有确认 direct-check 可靠时才允许禁用
python scripts/k12_pool_ops.py disable-dead --n 50 --trust-direct-check

# 清理 abnormal
python scripts/k12_pool_ops.py purge-abnormal --max 200

# 常驻：5 分钟一轮，聊天探测 + 观察抽样，并清 abnormal（不因 direct-check 误杀）
python scripts/k12_pool_ops.py watch --interval 300 --probe-n 10 --auto-purge-abnormal
```

注意：共享 K12 快照 token 经常 **网关可聊** 但 **裸 check 401**。  
因此默认 **不以 direct-check 结果禁用账号**；真正服务健康以 `chat probe` 和网关 `auto_remove_invalid_accounts` 为准。

网关加固（`chatgpt2api/config.json`）：
- `auto_remove_invalid_accounts=true`（鉴权失效自动剔除）
- `refresh_account_interval_minute=30`
- `proxy_runtime.clearance.mode=flaresolverr`（注册/部分链路过 CF）
- FlareSolverr: `http://127.0.0.1:8191`

配套：
- `scripts/chatgpt2api_watchdog.ps1`：网关挂了自动拉起
- `scripts/k12_pool_monitor.py --watch`：存活率/聊天探测

## B. 带 RT 的 K12 导入/续期流程

脚本：`scripts/k12_rt_import.py`

```bash
# 先分类（看有没有 K12+RT）
python scripts/k12_rt_import.py inspect "D:/Downloads/your_export.zip"

# 优先导入 K12 且带 RT
python scripts/k12_rt_import.py import "D:/Downloads/your_export.zip" --require-k12 --require-rt

# 仅快照 K12（无 RT，短窗口）需显式允许
python scripts/k12_rt_import.py import "D:/Downloads/snapshot.zip" --require-k12 --allow-no-rt

# 触发网关刷新（对库内有 RT 的号）
python scripts/k12_rt_import.py refresh-gateway --limit 500
```

导入规则：
1. 拒绝 synthetic（`alg=none` / dummy kid / example.invalid）
2. 默认优先 `K12 + refresh_token`
3. 无 RT 快照必须 `--allow-no-rt`
4. 去重按 `access_token`

等你有新数据时，先 `inspect` 再 `import`。

## C. 母号邀请入 K12（唯一可持续补 K12 路径）

脚本：`scripts/k12_mother_invite.py`

硬条件：母号/管理员对 workspace 有邀请权限。

```bash
# 只看计划
python scripts/k12_mother_invite.py plan --workspace fc4f8db5-72cd-44cb-ae0d-fef1370a16c8 --emails a@x.com,b@y.com

# 母号邀请
python scripts/k12_mother_invite.py invite --mother-session mother_session.json --workspace fc4f... --emails-file kids.txt

# 子号 accept + 硬校验 plan_type
python scripts/k12_mother_invite.py accept --child-token <AT> --workspace fc4f...

# 全自动：invite -> accept -> verify is_k12
python scripts/k12_mother_invite.py run --mother-session mother_session.json --workspace fc4f... --children children.jsonl
```

母号 session 获取：浏览器登录 chatgpt.com 后打开  
`https://chatgpt.com/api/auth/session` 保存 JSON。

**禁止依赖：**
- 子号对共享 K12 的 `request`（同域 401）
- 仅看 `accept` 的 HTTP 200（必须 `accounts/check` 验证 `plan_type=k12`）

当前共享 workspace（来自 80500 导出）：
`fc4f8db5-72cd-44cb-ae0d-fef1370a16c8`

## 推荐运行组合

1. 网关 + FlareSolverr 常驻  
2. `k12_pool_ops.py watch` 清死号  
3. 有 RT 新货：`k12_rt_import.py inspect/import`  
4. 有母号：`k12_mother_invite.py run` 补真 K12  

## 当前资源判断

| 资源 | 价值 |
|------|------|
| 80500 共享 K12（无 RT） | 短期可打，尽快用 |
| hotmail 自注册 free（有 RT） | 无母号邀请前，不作为 K12 补号源 |
| free request 进共享 K12 | 社区无稳解 |

## 社区调优补充（2026-07-14 晚）

### 大号池存储：JSON → SQLite

`chatgpt2api/data/accounts.json` 在 8 万号时约 **230MB+**，每次读写/启动成本高。  
社区与上游 README 推荐大号池用：

```bat
set STORAGE_BACKEND=sqlite
set DATABASE_URL=sqlite:///D:/Users/grok-auto-register/chatgpt2api/data/accounts.db
```

迁移脚本（网关需先停）：

```bat
python scripts/k12_migrate_sqlite.py
python scripts/k12_migrate_sqlite.py --force
```

验证：`python scripts/k12_pool_ops.py status` 数量与 chat probe 正常后，再考虑挪走 `accounts.json` 冷备份。

### 其它已固化调优

| 项 | 设置 |
|----|------|
| 失效剔除 | `auto_remove_invalid_accounts=true` |
| 刷新间隔 | `refresh_account_interval_minute=30` |
| CF 清障 | FlareSolverr + `clearance.mode=flaresolverr` |
| Kimi reasoning | 网关忽略 `reasoning_effort`（防 422） |
| 上下文 | `gpt-5-5` 1M / 其它 400k；`reserved_context_size=50k` |
| 死号判定 | **chat probe + 网关剔除**；勿用裸 check 批量禁用共享 K12 |
