# A2A 泄漏修复工单 — Atom 审核报告

> 审核员：Atom  
> 审核日：2026-07-15  
> 基线：三审汇总（Reasonix 初审 + Atom pass-with-notes + Claude concur-with-additions）  
> 执行报告：`_review_output/leakfix_report.md`  
> 核对范围：a2a_mcp_server.py, shared_a2a_server.py, reasonix_a2a_wrapper.py, shared_a2a_metrics.py, a2a_replay.py

---

## 逐项核对

### F1 — task_agent_mapping 剪枝 + 写放大消除 `a2a_mcp_server.py`

| # | 报告锚点 | 实际行号 | 状态 |
|---|----------|----------|------|
| 1 | `:117` | ✅ `:117` | 模块级 `_last_task_mapping_save` 正确 |
| 2 | `:497-503` | ✅ `:497-503` | `_maybe_save_task_mapping()` 节流逻辑正确 |
| 3 | `:491` | ✅ `:491` | `periodic_save` 传 `force=True` |
| 4 | `:807` | ✅ `:807` | `unregister_agent` 删除后 force 保存 |
| 5 | `:988` | ✅ `:988` | `send_message` 初始存选用节流写 |
| 6 | `:1017-1019` | ✅ `:1017-1019` | `send_message` 终态 pop + force 保存，在 `unify_send_response` 之前 |
| 7 | `:1416` | ✅ `:1416` | `send_message_stream` 初始存选用节流写 |
| 8 | `:1520-1522` | ✅ `:1520-1522` | 流成功返回前 prune |
| 9 | `:1537-1539` | ✅ `:1537-1539` | 内层 `except Exception` 失败路径 prune |
| 10 | `:1547-1549` | ✅ `:1547-1549` | 外层 `except Exception` 设置异常 prune |

**结论**：终态 pruning 覆盖 `send_message`（1 条路径）和 `send_message_stream`（3 条路径：成功、内层异常、外层异常）。节流写中 `force=True` 保证终态立即刷盘，30s 延迟仅作用于非终态中间事件，无数据丢失窗口。✅

### F2 — metrics temp 顶层兜底 `reasonix_a2a_wrapper.py`

| 报告锚点 | 实际行号 | 状态 |
|----------|----------|------|
| `:810-815` | ✅ `:810-815` | `finally: metrics_file.unlink(missing_ok=True)` |

- `metrics_file` 定义在 `:607`（`for attempt` 循环外），**始终定义**，无 `NameError` 风险。
- `finally` 在 `for attempt` 循环内，每次迭代（包括 `continue` 重试）都会执行，`missing_ok=True` 容错删除已不存在的文件。
- 覆盖 `:753-792` 所有 `raise` 逃逸路径（`AgentExecutionError` re-raise 和泛 `Exception` raise）。

**结论**：正确。✅

### F3 — kill 后有限 retry-wait `shared_a2a_server.py`

| 报告锚点 | 实际行号 | 状态 |
|----------|----------|------|
| `:1984-2000` | ✅ `:1984-2000` | 3 次 retry × 2s timeout |

- `for _retry in range(3)` 循环，硬上限 3 次。
- `ProcessLookupError` 视为已退出立即 `break`。
- 3 次超时后记 `warning` 不崩溃。

**结论**：正确。✅

### F4 — _task_store / tasks DB 运行期剪枝 `shared_a2a_server.py`

| 子项 | 报告锚点 | 实际行号 | 状态 |
|------|----------|----------|------|
| F4a 强引用 | `:1836` | ✅ `:1836` | `app.state._reaper_task = asyncio.create_task(...)` |
| F4b SQLite 剪枝 | `:1805-1816` | ✅ `:1805-1814` | `DELETE FROM tasks WHERE terminal_at IS NOT NULL AND terminal_at < ?` (2h) |
| F4c VACUUM | `:788-792` | ✅ `:788-792` | 启动时 `VACUUM`，`try/except` 容错 |
| F4d hydrate LIMIT | `:804-807` | ✅ `:804-807` | `SELECT ... WHERE created_at > ? ... LIMIT 500` (24h) |

- SQLite 剪枝条件 `terminal_at IS NOT NULL AND terminal_at < now-2h` 语义正确，`IS NOT NULL` 是冗余安全约束（`NULL < ?` 在 SQLite 中本就是假）。
- hydrate LIMIT 500 作用于 24h 窗口，匹配工单要求。

**结论**：正确。✅

### F5 — metrics JSONL 轮转 + replay 磁盘 TTL

| 子项 | 文件 | 报告锚点 | 实际行号 | 状态 |
|------|------|----------|----------|------|
| F5a 轮转 | `shared_a2a_metrics.py` | `:215-224` | ✅ `:215-224` | 10MB 阈值 → `.1`→`.2`→`.3` 三带轮转 |
| F5b 清理 | `a2a_replay.py` | `:66-75` | ✅ `:66-75` | ~2% 概率随机清理 >48h |

- 轮转逻辑：`range(2,0,-1)` 正确将 `.2`→`.3`、`.1`→`.2`、`base`→`.1`，保 3 代。
- `path.suffix` 正确（`.jsonl` 或 `.json`），`with_suffix` 行为正确。
- 轮转不原子，多进程并发时可能有短暂不一致，但属预先存在的最佳努力模式，非本工单引入。
- 清理概率 2%，匹配 `prune_audit_log` 模式。

**结论**：正确。✅

### F6 — SSRF 出站 allowlist `a2a_mcp_server.py`

| 报告锚点 | 实际行号 | 状态 |
|----------|----------|------|
| `:19` | ✅ `:19` | `import urllib.parse` |
| `:647-660` | ✅ `:647-662` | `urlparse` 提取 host/port 做 allowlist 校验 |

**逻辑检查**：
- `_host in ("127.0.0.1", "localhost")` — 无绕过。
- `_port is None or not (4900 <= _port <= 4999)` — 无绕过。
- `127.0.0.1.xip.io` → hostname = `"127.0.0.1.xip.io"`，不在列表中 → 拒绝 ✅
- IPv6 `[::1]` → hostname = `"::1"`，不在列表中 → 拒绝（但实为合法回环地址，此属假阳性非安全绕过）⚠️
- `0.0.0.0` → 拒绝 ✅
- URL 编码/credentials/尾点号 → 均被正确拒绝 ✅

**轻微问题**：IPv6 `::1` 被拒绝。这不是安全绕过（假阳性而非假阴性），但若未来有 agent 绑在 IPv6 上会失败。当前工单范围仅要求 `127.0.0.1/localhost`，实现符合 spec。

### T1 — 轻量遥测

| # | 文件 | 报告锚点 | 实际行号 | 状态 |
|---|------|----------|----------|------|
| T1a | `shared_a2a_server.py` | `:1846-1885` | ✅ | `_get_rss_mb()` psutil/ctypes 双路径 |
| T1b | `shared_a2a_server.py` | `:1890-1913` | ✅ | `_telemetry_logger` 60s 周期 |
| T1c | `shared_a2a_server.py` | `:1837-1840` | ✅ | 在 `_start_ttl_reaper` 中启动 |
| T1d | `a2a_mcp_server.py` | `:507-543` | ✅ | `_get_rss_mb()` 等同实现 |
| T1e | `a2a_mcp_server.py` | `:546-559` | ✅ | `_mcp_telemetry()` 60s 周期 |
| T1f | `a2a_mcp_server.py` | `:2102-2103` | ✅ | `main_async` 中 `asyncio.create_task` |

- 所有 telemetry 函数有 `try/except Exception: pass`，不会因异常逃逸崩溃进程。
- `_running_procs` 在 `shared_a2a_server.py` 模块级定义（`:1918`），telemetry 引用正确。
- `_health_cache`（`:1619`）和 `_circuit_state`（`:131`）在 `a2a_mcp_server.py` 模块级定义，MCP telemetry 引用正确。

**结论**：正确。✅

---

## 编译验证

```
$ python -m py_compile a2a_mcp_server.py           → OK
$ python -m py_compile shared_a2a_server.py          → OK
$ python -m py_compile reasonix_a2a_wrapper.py        → OK
$ python -m py_compile shared_a2a_metrics.py          → OK
$ python -m py_compile a2a_replay.py                  → OK
```

---

## 新引入 bug 排查

| 怀疑点 | 结果 |
|--------|------|
| F1 节流写导致终态数据丢失 | 无——终态全部 `force=True` |
| F2 finally 里 `metrics_file.unlink` 误删正在使用的文件 | 无——`finally` 在 `await run_command` 返回后执行，子进程已结束 |
| T1 异常逃逸 | 无——全部 `try/except Exception: pass` |
| F3 无限 retry | 无——`range(3)` 硬上限 |
| F5a 轮转竞态 | 存在但属预先存在的最佳努力模式，非本工单引入 |
| F5b 清理误删 | `missing_ok=True` + `try/except` 容错 |
| F6 IPv6 拒绝 | 假阳性（拒绝合法回环地址），非安全绕过 |

---

## VERDICT

**pass-with-notes**

## BLOCKERS

- (none)

## NOTES

F6 allowlist 拒绝 IPv6 `::1`（假阳性，非安全绕过）。若未来 agent 绑在 IPv6 上需添加 `"::1"` 到允许列表。其余 F1-F6 + T1 所有锚点确认正确，逻辑无漏洞，未引入新 bug，5 个文件全部通过 `py_compile`。