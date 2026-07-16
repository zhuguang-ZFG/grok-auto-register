# 四池日检 / 小时轻探（固化）

更新：2026-07-17

## 目标

公益站 **额度签到会回、慢源/耗尽要早出局**：硬失败快摘；额度类软失败 **临时出局 + recover_after**，到期自动复测放回。

## 任务

| 任务 | 频率 | 脚本 |
|------|------|------|
| `GrokThreePoolsDailyProbe` | 每天 ~09:17 | `scripts/run_daily_pool_health.cmd` |
| `GrokHourlyRemoteProbe` | 每小时 :23 | `scripts/run_hourly_remote_probe.cmd` |
| `GrokOpsHeartbeat` | 按原计划 | `ops_heartbeat.py --write logs/heartbeat.json` |

安装小时任务：

```bat
powershell -ExecutionPolicy Bypass -File scripts\install_hourly_remote_probe.ps1
```

## 日检流水线

1. `cliproxy_fleet_watchdog.ps1 -Once`
2. `probe_three_pools.py` → 客户端面冒烟
3. `disable_bad_upstreams.py --auto`（真写 + 重载）
4. 后台 `pool_health.py --probe`（本地 CPA，长）
5. `ops_heartbeat.py --write logs/heartbeat.json`

小时任务 **不做** 全量 CPA probe，只跑 `--auto` + heartbeat。

## `--auto` 规则

| 结果 | 行为 |
|------|------|
| **401 / 403 / 404** | 当天 streak≥1 → **永久 disabled**（key 死） |
| **额度/限流类 soft**（429、body 含 quota/额度/weekly…） | soft_streak≥2 → **临时 disabled** + `recover_after`（默认 **6h**，签到窗口） |
| **主路径慢**（chat 仍 200 且 ≥8000ms） | main_slow_streak 连续 ≥2 → 每个主 alias 改名为 `remote-*`；源保持启用，不整源 disabled |
| **仅 `remote-*` 慢**（chat≥15s 仍 200） | soft_streak≥2 → 临时 disabled + `recover_after` |
| **0 / 5xx 非额度** | soft，不摘（防误杀） |
| 到期 `recover_after` | **同轮先 re-enable 再 probe**；仍额度/慢则 **立刻再 temp 并续期**（不空窗进主路径） |
| Claude | 默认探 **`:8337` 聚合面**；直连 100xlabs 仅 `--include-claude` |

streak **只在 `--auto` 写盘**；`--auto` 进程 **exit 0**（pending 在 JSON）。

## 账本文件

- `logs/disable_bad_upstreams.json` — 最近一次探测 + applied/revived
- `logs/upstream_bad_streak.json` — 硬失败 streak
- `logs/upstream_soft_streak.json` — 额度/仅远端慢源 streak
- `logs/upstream_main_slow_streak.json` — 主路径 ≥8000ms 连续慢 streak
- `logs/upstream_temp_disable.json` — 临时出局 + recover_after
- `logs/heartbeat.json` — 含 `upstream_applied` / `temp_disabled`

最近报告的 `main_demote_ms`、`main_slow_streak_to_demote`、`main_slow_streak` 和 `demoted` 给出阈值、当前连续次数和实际降级结果。

## 会话亲和 / 主路径分层

- Grok、GLM：`session-affinity-ttl: "1h"`；Codex、Claude：保持 `"4h"`。
- **进主 alias RR**：本地源和未达到主路径慢阈值的远端。
- **只挂 `remote-*` 调试别名**：手工远端别名，以及被 `--auto` 连续 2 次测得 ≥8000ms 后自动降级的主 alias。
- 降级只改 alias，不设整源 `disabled: true`；该源仍可通过 `remote-*` 单独探测。
- Grok `max-retry-credentials: 4`（主路径更短）。

## 看门狗

| 脚本 | 状态 |
|------|------|
| `cliproxy_fleet_watchdog.ps1` | **唯一合法** 分实例 |
| `cliproxy_mem_watchdog.ps1` | **no-op**（防误杀） |

## 探测口径

- `probe_three_pools.py` 只验证 **本地聚合端口 alive**（models 200 + chat 可达）。
- Claude `:8337` chat 非 200 时标记为 `[CLOAK]`：这是上游 kiro/any 反代对非 Claude Code 客户端的权限/上下文/Cloudflare 门，不是本地池 down。上游质量由 `disable_bad_upstreams.py` 单独监控。
- `ops_heartbeat.py` 返回非零属正常告警语义（例如注册机未运行、`temp_disabled_n>0`）。

## 当前状态（2026-07-17）

| 池 | 端口 | 状态 |
|--|--|--|
| Grok | 8317 | OK，20 models |
| Codex | 8327 | OK，8 models |
| Claude | 8337 | CLOAK（本地 alive，上游门） |
| GLM | 8347 | OK，11 models |

- `pool_live_est=54 / 6392`，满足 `min_live=50`。
- 临时出局：`grok/vibes`、`glm/zhipu-plan`。
- 注册机已按用户要求关闭，`heartbeat` 会报告 `register process not running`。

## 手工

```bat
python scripts\disable_bad_upstreams.py --auto --soft-recover-hours 6
python ops_heartbeat.py --write logs\heartbeat.json
```
