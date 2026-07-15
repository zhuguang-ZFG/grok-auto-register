# AGENTS.md — grok-auto-register 项目级指引

> 面向任何在本仓干活的 agent。先读「判死铁律」，再动手。

## ⛔ 判死铁律（读 `docs/RT_ROTATION_RACE.md` 全文）

判定一个 xAI/Grok 号「死 / RT 失效」之前，**必须**遵守，否则会把好号误杀：

1. **`invalid_grant` ≠ 号死。** xAI 的 refresh_token 每次刷新即轮换、旧的立即作废；
   本仓有 6 个互不协调的 refresh 端（keepalive/quota_watch/pool_health/refresh_pool/
   hard_purge/local_grok_auth），并发刷同一号时输家必得 `invalid_grant`——这是
   **轮换竞态假死**，不是账号真死。判死前重读文件：RT 变了 = 别的进程刷过 = 号活着。
2. **测活先测 AT，别只测 RT。** 测 RT 是破坏性操作（消耗轮换）；测 AT 只读。
   AT 能用 = 号现在能用。用 `cpa_xai.probe.probe_models(at, base_url=...)`。
3. **AT 探测 URL 别拼错。** `base_url` 已含 `/v1`，正确地址
   `https://cli-chat-proxy.grok.com/v1/models`；别再拼出 `/v1/v1`。
4. **`permission-denied`(403) ≠ 号死，也 ≠ 缺生日。** 2026-07-16 实测（73 个禁号）：
   13 个零操作自愈；而 设 birthDate API、网页端过 TOS 墙、网页端成功发出一条对话，
   都不能立刻解除 cli-chat-proxy 面的 403（10/10 仍 403）。社区「设置生日即恢复」
   的帖子对 cli 面**不成立**（网页面能聊 ≠ cli 面放行）。正确处理：软禁用 +
   `recover_after`（默认 24h，`GROK_POOL_PERM_DENIED_RECOVER_HOURS` 可调）到期自动
   复测放回。别搬 dead、别为它刷 RT（刷新解不了 chat 面 gate）。

5. **封禁是号龄驱动，别再折腾指纹。** 2026-07-16 号龄回归（`scripts/ban_regression.py`，
   用 `accounts_*.txt` 时间戳 join 死号状态）：按**真实注册号龄** cohort，死亡率
   `<6h 0% → 1-2d 91% → 2-3d 97%` 单调增长。域名/命名/UA/出口/刷新活跃度**都不是主轴**
   （age≥1d 切片下所有域名一律 ~100% 死；活号中位刷新间隔比死号更长）。最简解释：
   free/cli-chat-proxy 号有约 **24-48h 寿命上限**。所以：**把 free 号当 24-48h 耗材，
   靠持续补号维持水位**；别为"降注册指纹/换 UA/换出口"烧成本（改不动主轴）；域名声誉
   只是二阶（自有域比 hotmail 长寿约 50×，仍逃不过号龄）。想验证/复核跑 `ban_regression.py`。

**任何 refresh 消费端，判死/禁用/搬号前必须调
`cpa_xai.raceguard.rt_rotated_by_other(path, tried_rt)`。** 已接入 5 处，新增端照做。

## 号池布局

- `cpa_auths/`：活号（网关读取）。
- `cpa_auths_dead/`：死号（不删，留审计；部分可能是假死，可按上面方法复核救回）。
- own 自注册域：`baoxia.top`、`lima.cc.cd`、`zhuguang.ccwu.cc`、`zhuguang.de5.net`、`hotmail.com`。
- 封禁归因工具：`python scripts/ban_regression.py`（只读；把死/活状态按号龄/域名/出口/UA 回归，
  证实"号龄驱动"结论；出口/指纹轴随新号 metric 积累而完整——注册 metric 现记 email+egress+生效指纹）。

## 改动纪律

- 改完 `.py` 跑 `python -m py_compile <files>` 验证。
- 危险命令（`rm -rf`、`git push --force`、`git reset --hard`、写 `.env`）被 config 硬 deny。
- git 提交/推送必须经用户明确同意。
