# A2A 泄漏修复工单（Hardening / med risk）

> 依据三审汇总：`_review/a2a_system_audit_result.md`（Reasonix 初审）、`_review/a2a_system_audit_atom_review.md`（Atom: pass-with-notes）、`_review/a2a_system_audit_claude_review.md`（Claude: concur-with-additions）。
> 根因裁定：**1GB/28h 元凶未定**（P0-1 量级差 50×，排除；疑似 wrapper 进程 CPython 碎片化 + `_task_store` 大输出窗口 + tasks DB 无界）。本工单只做三审确认的卫生修复 + 轻量遥测，不做根因大改。

## owns
- 代码：`C:/Users/zhugu/.kimi-code/mcp-a2a-bridge/`（a2a_mcp_server.py, shared_a2a_server.py, reasonix_a2a_wrapper.py, shared_a2a_metrics.py, a2a_replay.py）
- **不重启任何进程**（重启由 Kimi 协调）；改完跑 `python -m py_compile <files>`

## 修复项（按序）

1. **F1 task_agent_mapping 剪枝 + 写放大消除**（P0-1 + Claude-G/H）
   - `a2a_mcp_server.py`：task 进入 terminal 状态（completed/failed/canceled）后从 `task_agent_mapping` 删除对应 key（两条路径：send_message、send_message_stream 的终态分支都要覆盖）。
   - `save_to_json`（:900）改为节流写：距上次写盘 <30s 且非终态事件则跳过；或改增量 append。消除每任务 O(n) 全量重写。
2. **F2 metrics temp 顶层兜底**（P0-2 + Claude 修正锚点）
   - `reasonix_a2a_wrapper.py execute_reasonix`：外层 `try/finally` 无条件 `metrics_file.unlink(missing_ok=True)`（覆盖 :753-792 所有 re-raise 逃逸路径）。
3. **F3 kill 后有限 retry-wait**（P0-3 降 P1）
   - `shared_a2a_server.py _kill_proc_graceful`（:1884-1887）：3s wait 超时 `pass` 改为最多 3 次 retry-wait（每次 2s），仍不死则记 warning（孙进程持管道场景可观测）。
4. **F4 _task_store / tasks DB 运行期剪枝**（Claude-B/C/D，量最大面）
   - `_start_ttl_reaper`（:1814）：`asyncio.create_task` 保存强引用到 `app.state`，防 GC；顺手把 `@app.on_event("startup")` 迁到 lifespan（若改动大则仅持引用 + 注释 TODO）。
   - tasks SQLite：reaper 每轮顺带 `DELETE FROM tasks WHERE terminal_at < now-2h`（复用现有 1h 窗口语义即可，放宽到 2h 防爆冲）；启动 `_init_db` 保留原 DELETE，追加一次 `VACUUM`（仅启动时）。
   - 启动 hydrate（:799-817）限量：只载入最近 500 条或 24h 内记录。
5. **F5 metrics JSONL 轮转 + replay 磁盘 TTL**（P1-4 + Atom 补充 P2）
   - `shared_a2a_metrics.py` append 前检查文件 >10MB 则滚动重命名（保 3 代）。
   - `a2a_replay.py write_replay`：写入时概率性（~2%，照抄 prune_audit_log 模式）清理 >48h 的 replay 文件。
6. **F6 SSRF 出站 allowlist**（Claude-F）
   - `a2a_mcp_server.py register_agent`（:564）：agent_url 仅允许 `127.0.0.1/localhost` 且端口在 4900-4999；拒绝其它。
7. **T1 轻量遥测（根因取证）**
   - `shared_a2a_server.py`：加一个周期 task（60s），`logger.info` 一行：`rss_mb`（psutil 或 ctypes GetProcessMemoryInfo，若无 psutil 用 stdlib）、`len(_task_store)`、`len(_running_procs)`、db 文件大小。MCP server 侧加 `len(task_agent_mapping)`、`_health_cache`、`_circuit_state`。
   - 不许引入新依赖；psutil 缺失时 fallback ctypes。

## gates
- `python -m py_compile` 全部改动文件通过
- 逐条对照本工单 F1-F7 列证据（file:line 新锚点）
- 不改任何行为语义（剪枝阈值、allowlist 范围如上写明，勿自创）
- 报告写到 `C:/Users/zhugu/.kimi-code/mcp-a2a-bridge/_review_output/leakfix_report.md`
