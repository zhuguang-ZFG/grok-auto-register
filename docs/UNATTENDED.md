# 本机无值守补强（电源 / 睡眠 / 代理）

目标：在 **不搬 VPS** 的前提下，尽量让号池闭环在插电笔记本上连续跑。

## 1. 电源（一次性）

```powershell
cd D:\Users\grok-auto-register
powershell -ExecutionPolicy Bypass -File .\scripts\ensure_power_awake.ps1
```

效果（AC 插电）：

- 睡眠 = 从不  
- 合盖 = 不采取任何操作  
- 休眠定时 = 从不  
- 允许唤醒定时器  

电池策略默认不动。若也要电池不睡：加 `-AlsoBattery`（费电）。

验证：

```bat
powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
powercfg /q SCHEME_CURRENT SUB_BUTTONS LIDACTION
```

AC 当前值应为 `0x00000000`（睡眠/合盖 do-nothing）。

## 2. 代理稳定

```bat
python proxy_health.py
python proxy_health.py --rotate-if-bad
```

- Clash API 可达  
- 探测出口 IP  
- `GET accounts.x.ai`（TLS）；失败则轮换节点再探  
- 结果写入 `.proxy_health.json`，`pool_status` 会显示  

已挂进：

- `run_health_only.bat`（GrokPoolHealth）  
- `pool_maintain.py` 开头（GrokPoolMaintain）  

## 3. 进程 / 任务

保持：

- Clash 常开  
- `Grok*` 计划任务 Enabled  
- 插电、别进现代待机死睡  

日常：

```bat
python pool_status.py
```

看：代理健康、铸造 protocol_ok、CPA 水位、三进程、电源 AC、sticky reselect。

## 3.1 Heartbeat（进程 + 水位，可选计划任务）

```bat
python ops_heartbeat.py
python ops_heartbeat.py --json --write logs/heartbeat.json
```

| exit | 含义 |
|------|------|
| 0 | ok |
| 1 | warn（水位低于 `pool_min_live` / quota_watch 未跑） |
| 2 | critical（注册机或 CLIProxy 未跑） |

建议 Task Scheduler 每 10–15 分钟跑一次并 `--write logs/heartbeat.json`；`pool_status` 会读该文件打一行摘要。  
**不**发网络 probe，不碰号池文件内容以外的读。

## 4. 边界

- 合盖 + 某些 OEM 仍可能进连接待机 → 任务暂停；真要铁 7×24 用小 VPS。  
- 代理节点全挂时，注册/铸造会失败；health 只能换节点，不能造节点。  
- 本方案 **不** 提高并发、不改 buffer_first 策略。


## 5. 号池闭环（死号隔离）

共享批次 RT 被吊销后，JWT 未过期也会让 CLIProxy sticky 整分钟 reselect。

- 日常：`GrokPoolMaintain` 会跑 `refresh_pool --purge-dead` + `scripts/hard_purge_pool.py`（真刷新全池，revoked/disabled 移到 `cpa_auths_dead/`）。
- 手动：`python scripts/hard_purge_pool.py`
- 水位：`quota_watch_min_pool` / `pool_min_live` 只看可续期号；自有 4 域继续补到 target。
- 心跳：计划任务 `GrokOpsHeartbeat` 每 15 分钟写 `logs/heartbeat.json`。


## 6. 死号 vs 额度冷却（勿混淆）

| 状态 | 位置 | 处理 |
|------|------|------|
| `refresh_revoked` / no RT / bad_json | `cpa_auths_dead/` | 真死，hard_purge 搬走 |
| `free-usage-exhausted` | **留在** `cpa_auths/` 且 `disabled:true` | 冷却后 `quota_watch` re-enable |
| 共享缓冲 RT 吊销 | dead | 正常，别当自有水位 |

手动:
```bat
python scripts/hard_purge_pool.py
python scripts/rescue_quota_holds.py --own-only
python scripts/rescue_quota_holds.py --own-only --reenable-ready
```


## 7. P0 缓冲 hygiene + 导入熔断（2026-07-12）

- **水位只计自有域**：`pool_watermark_own_only=true`（`ops_heartbeat` / `count_valid_pool`）。缓冲共享包不抬 `min_live`。
- **source 标记**：CPA JSON 写 `source=own|buffer`（CLIProxy 忽略未知字段）。导入脚本会打 `buffer`。
- **导入熔断**：
  ```bat
  python scripts/import_cpa_with_probe.py D:\Downloads\pack.zip
  python scripts/import_cpa_with_probe.py D:\DownloadsÀx-cpa.txt --sample 30 --min-ok-rate 0.7
  ```
  抽检 RT ok 率 < 阈值 → **不写入 live**（exit 3）。`--force` 可强行导入。
- **存量 SSO 补 CPA**：
  ```bat
  python scripts/backfill_cpa_xai_from_accounts.py --limit 5
  python scripts/export_cpa_xai_from_grok_auth.py
  ```
- **prefer**：`pool_prefer=own_first`（与 `pool_prefer_mode` 同义兼容）。


## P1 hard_purge + import survivors (2026-07-13)

- **hard_purge default scope=buffer**, max 500/run, maintain interval **6h** (`pool_maintain_hard_purge_every_hours`).
- Unknown `disabled` with RT is **probed** (not forever hold).
- **import_cpa_with_probe**: sample fuse, then `--refresh-all` (default) only writes RT-ok survivors.
  ```bat
  python scripts/import_cpa_with_probe.py D:/Downloads\pack.zip
  python scripts/hard_purge_pool.py --scope buffer --max 500
  python scripts/hard_purge_pool.py --scope all
  ```


## 8. 电源复核

插电运行：`powershell -ExecutionPolicy Bypass -File .\scripts\ensure_power_awake.ps1`
目标：AC 睡眠=从不、合盖=不操作。笔记本合盖仍可能被 OEM 现代待机打断。
