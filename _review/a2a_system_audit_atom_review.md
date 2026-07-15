# Atom 审核结论：Reasonix 内存泄漏+良构审查

**审核日期**: 2026-07-15  
**被审报告**: `a2a_system_audit_result.md`  
**审核范围**: P0/P1 锚点验证 + 完备性 + 分级判定 + 遗漏面

---

## VERDICT: pass-with-notes

---

## 锚点验证结果

### P0 — 全部确认，无一误报

| ID | 状态 | 备注 |
|----|------|------|
| **P0-1** `task_agent_mapping` | ✅ 确认 | `:113` 初始化，`:868`/`:1321` 写入，`:708-714` 唯一删除路径。`send_message` 和 `send_message_stream` 均在 terminal 状态分支后无任何 cleanup。定性准确，P0 合理。 |
| **P0-2** `_try_parse_metrics` 残留 | ✅ 确认，但控制流描述有微瑕 | 逃逸路径真实存在（auto-continue inner `except AgentExecutionError` → `continue` 跳回 `for ci` 循环，以及 outer `except AgentExecutionError` 非 soft-stop 路径 re-raise 前均未调 `_try_parse_metrics`）。但报告中 "`continue` 跳到下一个 attempt" 不准确——实际跳到下一个 `ci` 迭代（auto-continue 内层循环），而非外层 `for attempt in range(3)`。不影响结论。 |
| **P0-3** `run_command` 句柄泄漏 | ✅ 确认 | `:1948-1950` TimeoutError → `_kill_proc_graceful` → `:1884-1887` 3s wait 超时后 `pass`。`finally` 仅 pop dict 不 wait。Windows 上句柄确实泄漏。P0 合理。 |

### P1 — 全部确认

| ID | 状态 | 备注 |
|----|------|------|
| **P1-1** `_agent_clients` | ✅ 确认 | `:117` 无界缓存，`:465-478` `get_agent_client` 只增不删。 |
| **P1-2** `_health_cache` | ✅ 确认 | `:1517` 初始化，`:1538` 写入但从不删除过期条目。 |
| **P1-3** `_circuit_state` | ✅ 确认 | `:127` 初始化，`:160` `setdefault` 创建，`:167-171` 重置但不删 key。 |
| **P1-4** Metrics JSONL 无轮转 | ✅ 确认 | `shared_a2a_metrics.py:211-216` 纯 append 不轮转。 |
| **P1-5** 磁盘 JSON 无界增长 | ✅ 确认 | `:900` 全量持久化，与 P0-1 绑定。 |
| **P1-6** `_deep_health_cache`+`_deep_health_locks` | ✅ 确认 | `shared_a2a_server.py:462,465` 只增不删。 |

---

## 完备性评估

### 已覆盖的好的方面
- `_task_store` TTL reaper（`:1785-1809`）确认有界 ✅
- `_running_procs`/`_running_procs_grace` 确认 `finally` 必 pop ✅
- daemon JS `child.unref()` 影响确认 ✅
- `prune_audit_log` 存在但 metrics JSONL 无类似机制 ✅

### 遗漏面（未在报告中提及）

1. **`data/replay/*.json` 磁盘无界增长 (P2)** — `a2a_replay.py:write_replay` 每个 task_id 写一个文件到 `data/replay/`，永不删除。28h 千级任务 → 数千个小型 JSON 文件累积。虽非内存泄漏，但影响磁盘和 `timeline_by_trace` 扫描性能。建议添加 `prune_replay` 或基于 TTL 清理。

2. **`_kill_proc_graceful` 内 `taskkill` 子进程堆积 (P3)** — Windows 路径中 `:1873-1878` 同步调用 `subprocess.run(["taskkill", ...], timeout=5)`。极端场景下数百个超时任务可能产生同步子进程排队。非泄漏，但应提及。

3. **`dispatch_audit.jsonl` 概率性清理 (P3)** — `prune_audit_log` 仅 ~2% 调用概率（报告中已提及但未评估影响）。若 28h 内调用不足，该文件同样会无界增长。建议注明。

---

## 分级合理性

| 分级 | 评判 |
|------|------|
| **P0** | 合理。`task_agent_mapping` 泄漏（P0-1）与历史 1GB/28h 现象高度吻合，且修复成本低。P0-2 和 P0-3 均有真实残留证据。 |
| **P1** | 合理。`_health_cache` / `_circuit_state` 在静态 5 agent 场景下有限，但动态注册时即成泄漏。分级准确。 |
| **P2** | 多数合理。P2-2 daemon 日志轮转仅在 spawn 时触发，实为低风险（daemon 通常数周不重启）。 |
| **P3** | 无争议，均为建议级。 |

**修复顺序建议**: P0-1 → P0-3 → P0-2 → P1-6 → P1-2 → P1-4 → P1-1/P1-3/P1-5（与 P0-1 绑定）

---

## 总结论

报告质量高，6 个 P0 和 P1 发现全部锚点真实、定性准确。一处控制流描述的微瑕（P0-2 `continue` 目标层级）不影响结论。遗漏了 replay 模块磁盘累积和 `taskkill` 子进程堆积两个次要面，建议补充为 P2/P3。整体可作为修复依据直接使用。

**NOTES**: 建议补充 replay 清理策略；P0-2 的 `continue` 目标描述建议修正为 "auto-continue 内层循环" 而非 "下一个 attempt"。