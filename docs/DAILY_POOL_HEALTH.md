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

## 当前状态（2026-07-17 16:50Z 加固：Codex + 火山 coding plan 兜底）

| 池 | 端口 | 状态 |
|--|--|--|
| Grok | 8317 | OK models+chat 200（硬摘 chuanapi 后已重拉） |
| Codex | 8327 | OK models+chat+responses 200（新增火山双 key 兜底） |
| Claude | 8337 | OK models+chat 200 |
| GLM | 8347 | OK models+chat 200 |

- **Codex :8327 503 恢复（16:50Z）**
  - 根因：local-k12 / muyuan / apinebula / zmoon2 全部进入 auth unavailable / cooldown，无可用上游。
  - 修复：在 `D:/cli-proxy-api/config-codex.yaml` 新增 `volc1` / `volc2` 两个火山 coding plan 源：
    - `deepseek-v4-pro-260425` → `gpt-5.6`
    - `deepseek-v4-flash-260425` → `gpt-5.5`
    - `doubao-seed-2-0-code-preview-260215` → `gpt-5.6-sol`
  - 已重启 Codex CLIProxy；`:8327` `/v1/models` 10 models，`/v1/chat/completions` 与 `/v1/responses` 双 200。

- **本轮 `--auto`（06:43Z）**
  - **硬摘**：`grok/chuanapi`（401 Invalid token）
  - **pending**：`grok/yxxb` soft=1/2（429 额度耗尽）、`glm/hcnsec` soft=1/2（429）、`codex/hhhl` main_slow=1/2（~9.8s）
  - **仍 temp**：`glm/zhipu-plan`、`volc-ark-a6807`、`volc-ark-62c6d`（+6h）
  - **新源确认 OK**：`mskxaigrok`、`nocdn939593` 进主路径 chat 200
- **Claude CLI 已切到本地统一池**：`cc-switch` → `claude-unified`（`http://127.0.0.1:8337`）；`C:/Users/zhugu/.claude/settings.json` 同步更新；`:8337` models+message 双 200。
- **代理接入 Clash**：
  - `data/proxies_clash_fragment.yaml` 已生成 1000 个 http 节点 + `代理池1000` selector；
  - 已拷到 Clash Verge profiles 并合并进 `grok_merged.yaml`（总 1613 节点，selector 大小 1637）。
  - 因不知道 Clash REST secret，自动 reload 返回 401；需在 Clash Verge 里手动切到 `grok_merged.yaml`。
  - 注册机 `config.json` 已设为低频：`concurrent_count=1`、`auto_loop_count=2`、`auto_loop_pause_sec=1800`、`clash_selector=代理池1000`、`http_proxy_list_path=data/proxies-all-auth-1000.txt`（fallback）。
  - **注意**：从当前 CN 主机直接/经 Clash 链测这 1000 个 HTTP 代理均不通（0/100），可能是代理已死或当前 Clash 节点无法到达；建议切到 `grok_merged.yaml` 后从能通境外的节点再测。
- **全量 `pool_health.py --probe`**：已优化 `pool_health.py` 跳过未到期的 `disabled` 账号，避免对成千上万 `invalid_grant` 反复刷新；后台进程已按用户切任务时停止。当前 `cpa_auths/pool_health_report.json` 仍是 2026-07-16 旧数据（live=906），如需最新报告可择时再跑。
- **cpa_auths.7z 导入**：+195 新号；本地 `cpa_auths` **~8941**；**enabled≈2049** / disabled≈6892；`cpa_auths_dead` 约 7517。
- **Clash 1000 代理测活**：尝试合并到 `grok_merged.yaml` 并切换 selector 实测，**20/20 死**（连接被拒绝/重置），且导致断网。已立即恢复为之前的活动配置 `RtwJL9IAeu1a.yaml`；`scripts/merge_clash_grok_nodes.py` 已移除 `proxies_clash_fragment.yaml` donor。注册机 `config.json` 的 `clash_selector` 已清空、`http_proxy_enabled=false`，不再尝试使用这批死代理。
- **全量 `pool_health.py --probe`**：已按用户要求停止，未跑完。
- heartbeat critical = register 未跑 / upstream_applied，**≠ 四池 down**。

## 手工

```bat
python scripts\disable_bad_upstreams.py --auto --soft-recover-hours 6
python ops_heartbeat.py --write logs\heartbeat.json
```
