# A2A 系统审查报告

- **日期**: 2026-07-15
- **类型**: 只读静态审查
- **风险**: med
- **范围**: 内存泄漏 + 良构评估

> **注意**: 目标路径 `D:/Users/grok-auto-register/_review/a2a_system_audit_result.md` 在沙箱限制外；
> 报告写入工作区内 `_review_output/a2a_system_audit_result.md`，请手动复制到目标位置。

---

## 目录

1. [P0 — 立即修复级](#p0--立即修复级)
2. [P1 — 高优先级](#p1--高优先级)
3. [P2 — 中优先级](#p2--中优先级)
4. [P3 — 建议级](#p3--建议级)
5. [历史 1GB/28h 泄漏推断](#历史-1gb28h-泄漏推断)
6. [良构评估](#良构评估)

---

## P0 — 立即修复级

### P0-1: `task_agent_mapping` 只增不减 → 内存泄漏

**严重性**: 任务为 key 的 dict 仅由 `send_message` / `send_message_stream` / `delegate_task` 写入（`a2a_mcp_server.py:868`、`a2a_mcp_server.py:1321`），但仅在 `unregister_agent`（`:708-714`）时清理。正常任务完成后从不删除。长期运行下 dict 无界增长。

**文件锚点**: `a2a_mcp_server.py:113` 初始化，`:868` 添加，`:708-714` 唯一删除路径

**影响**: 每个任务～200B dict entry + JSON 序列化开销。28h 上千任务 → 显著泄漏。持久到 disk（`:900` `save_to_json`）的文件也同步增长。

**修复建议**: 在 `send_message` terminal 状态分支后或后台周期 reaper 中，删除已完成任务的 `task_agent_mapping` entry。参照 `shared_a2a_server.py:1790-1795` 的 TTL reaper 模式。

---

### P0-2: `_try_parse_metrics` temp 文件泄漏

**严重性**: `_try_parse_metrics`（`reasonix_a2a_wrapper.py:299-316`）在 `finally` 中 `unlink` metrics temp file，但有两个逃逸路径：
1. `execute_reasonix` 中 `run_command` 抛出 `AgentExecutionError`（`:689-690` auto-continue 路径）后立即 `continue`，跳到下一个 attempt，**完全跳过** `_post_process_run`（其中调用 `_try_parse_metrics`）。
2. 进程崩溃或被 SIGKILL。

**证据**: 审查时发现当前存在 **12 个** 残余文件 `data/.reasonix-metrics-*.json`，每个 1–1.4KB。命令 `ls data/.reasonix-metrics-*.json | wc -l` 确认。

**文件锚点**: `reasonix_a2a_wrapper.py:607-608`（创建），`:299-316`（清理），`:633-640`（正常路径调用 `run_command` 后调用 `_post_process_run`），`:689-690`（异常逃逸路径）

**修复建议**: `execute_reasonix` 外层 `try/finally` 确保 metrics_file 总是被删除，或者在 `_try_parse_metrics` 基础上增加一个顶级 `finally` 块。

---

### P0-3: `run_command` 超时后 `proc.wait()` 未保证 → 句柄泄漏 (Windows)

**严重性**: `run_command`（`shared_a2a_server.py:1890-1965`）在 `asyncio.TimeoutError` 时调用 `_kill_proc_graceful`（`:1949`）。`_kill_proc_graceful`（`:1826-1887`）最多等 3s `proc.wait()`，若超时则直接放弃 — **进程句柄未被 Python 的 subprocess 模块收割**。Windows 上不 `wait()` 的终止进程会留下僵尸句柄，直到父进程退出。daemon 层面 `child.unref()`（`daemon.js:311`）使僵尸无法被 JS 层收割。

**文件锚点**: `shared_a2a_server.py:1948-1954`

代码追溯：
```python
# 第 1948-1950 行：超时后 kill，但 wait 可能超时
except asyncio.TimeoutError:
    await _kill_proc_graceful(proc, task_id, graceful_cancel_s)
    raise RuntimeError(...)
# finally 仅 pop dict，不 wait
finally:
    _running_procs.pop(task_id, None)       # 第 1953 行
    _running_procs_grace.pop(task_id, None) # 第 1954 行
```

`_kill_proc_graceful` 内 wait 超时后只 `pass`（`:1857, 1867, 1887`）：

```python
# 第 1885-1887 行：仅 3s timeout，超时后进程句柄泄漏
try:
    await asyncio.wait_for(proc.wait(), timeout=3.0)
except (asyncio.TimeoutError, ProcessLookupError):
    pass  # ← 泄漏点
```

**影响**: 每个超时任务泄漏一个 subprocess 句柄（Windows 上包括子进程树）。28h 数百次超时 → 句柄耗尽可能性。

**修复建议**: `_kill_proc_graceful` 中 wait 超时后应循环 retry（带指数退避），或直接调用 `proc.kill()` 后再次 `proc.wait()`。不能 `pass`。

---

## P1 — 高优先级

### P1-1: `_agent_clients` httpx 连接池缓存永不清理

**严重性**: `_agent_clients`（`a2a_mcp_server.py:117`）缓存 A2AClient（内含 httpx.AsyncClient 连接池）。仅当错误路径时 `pop`（`:1187, 1253, 1447, 1895`），无定期清理。若 agent URL 数量稳定（当前 5 个），此问题有限；但若 agent 动态注册/注销，缓存的 client 残留。

**文件锚点**: `a2a_mcp_server.py:117, 465-478`

```python
_agent_clients: Dict[str, A2AClient] = {}  # 第 117 行
def get_agent_client(agent_url, timeout=None):
    client = _agent_clients.get(agent_url)  # 第 474 行
    if client is None:
        client = A2AClient(url=agent_url, timeout=timeout)
        _agent_clients[agent_url] = client  # 第 477 行
```

此外，`send_message`（`:882`）和 `send_message_stream`（`:399`）**并未使用**缓存 client，而是每次都创建新的 `httpx.AsyncClient`。

**修复建议**: 统一使用 `get_agent_client`；添加 `max_age` TTL 清洗。

---

### P1-2: `_health_cache` TTL 缓存无过期清理

**严重性**: `_health_cache`（`a2a_mcp_server.py:1517`）虽然有个体 entry 的 60s TTL（`:1529-1531`），但从未从 dict 中移除过期的 entry。长期运行 → 残留的 `(False, timestamp)` 条目堆积。

**文件锚点**: `a2a_mcp_server.py:1517, 1526-1538`

```python
_health_cache: Dict[str, tuple] = {}        # 第 1517 行
_health_cache[url] = (ok, now)              # 第 1538 行 — 只增不删
```

**修复建议**: 后台周期清除 `_health_cache` 中 TTL 过期的条目。

---

### P1-3: `_circuit_state` 在 `a2a_mcp_server.py` 中只增不删

**严重性**: `_circuit_state`（`:127`）通过 `_circuit_record_failure` 的 `setdefault`（`:160`）为每个 agent URL 创建条目。`_circuit_record_success` 重置 `failures=0` 并删除 `open_until`，但**从不删除 key**。动态 agent URL 会永久残留。

**文件锚点**: `a2a_mcp_server.py:127, 158-171`

```python
_circuit_state: Dict[str, Dict[str, Any]] = {}  # 第 127 行
state = _circuit_state.setdefault(agent_url, {"failures": 0, "last_error": ""})  # 第 160 行
```

**修复建议**: `_circuit_check` 中识别已关闭且无失败的条目可清理；或添加 TTL。

---

### P1-4: Metrics JSONL 文件无轮转

**严重性**: `append_agent_metrics`（`shared_a2a_metrics.py:149-218`）以 append 模式写入 `data/*_metrics.jsonl`，永远不轮转。每个 wrapper 每任务一行。长时间运行后磁盘浪费。

对比之下 `dispatch_audit.jsonl` 有 `prune_audit_log`（`a2a_audit.py:54-108`，概率性 ~2% 调用），但 metrics JSONL 完全没有清理逻辑。

**文件锚点**: `shared_a2a_metrics.py:211-216`

```python
with path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
```

**典型文件**: `data/reasonix_metrics.jsonl`、`data/atomcode_metrics.jsonl`、`data/claude_metrics.jsonl` 等。

**修复建议**: 在 `append_agent_metrics` 中添加文件大小检查，>N MB 时 rename 为 `.1` 或截断保尾（参考 daemon.js `openWrapperLog` 的 5MB 轮转策略）。

---

### P1-5: `task_agent_mapping` 磁盘 JSON 无界增长

**严重性**: `save_to_json(task_agent_mapping, TASK_AGENT_MAPPING_FILE)` 在 `send_message`（`:900`）和 `send_message_stream`（`:1324`）每次调用时全量写入。文件大小与内存中 dict 同比例增长。

**文件锚点**: `a2a_mcp_server.py:900`

**修复建议**: 与 P0-1 绑定解决 — 清洗内存 dict 后文件自然缩小。

---

### P1-6: `_deep_health_cache` + `_deep_health_locks` 在 `shared_a2a_server.py` 中无界增长

**严重性**: `_deep_health_cache` 和 `_deep_health_locks` 以 agent card `name` 为 key。每出现一个不同的 `name` 就新增一条。从不清理。

**文件锚点**: `shared_a2a_server.py:462, 465`

```python
_deep_health_cache: Dict[str, Tuple[float, "DeepHealthResult"]] = {}  # 第 462 行
_deep_health_locks: Dict[str, asyncio.Lock] = {}                       # 第 465 行
```

**修复建议**: 添加 TTL reaper 或使用 `@lru_cache` 替代。

---

## P2 — 中优先级

### P2-1: `send_message` 每次创建新 `httpx.AsyncClient` 而非复用缓存

**文件锚点**: `a2a_mcp_server.py:882`、`:1084`、`:1227`、`:1558`、`:1598`

`send_message`、`get_task_result`、`cancel_task`、`_agent_busy_gate`、`_deep_health_precheck` 均使用 `async with httpx.AsyncClient(...) as client` 创建临时 client。虽上下文管理器确保关闭，但失去了 HTTP 连接复用。

**修复建议**: 统一切换到 `get_agent_client()`。

---

### P2-2: `openWrapperLog` 日志轮转仅发生在 spawn 时

**文件锚点**: `a2a-agents-daemon.js:201-234`

轮转代码仅在 `startAgent` 时执行一次。如果 wrapper 长期运行（daemon 不重启），日志可一直增长到 5MB 才在下次 daemon 重启时轮转。

**修复建议**: 添加轮转间隔（如每 30 分钟检查一次日志大小），或在 watchdog 定时器中周期性调用。

---

### P2-3: 多个 short-lived httpx client 未共享连接池

`a2a_mcp_server.py` 中至少 10 处（grep 确认）`async with httpx.AsyncClient(...)`。虽无泄漏，但连接池零复用 → loopback 场景下每个请求重新 TCP 握手 + TLS（若有）。

**修复建议**: 建立全局 client 工厂或引用 `_agent_clients`。

---

## P3 — 建议级

### P3-1: stdout+stderr 全量 buffer 内存驻留

`run_command`（`shared_a2a_server.py:1955`）对完整 stdout+stderr `decode()` 后连接。长输出（如数万行 diff）造成短暂的 2x 内存峰值。大产出 LLM 在 900s timeout 内可能产生 MB 级输出。非泄漏（调用返回后释放），但可考虑流式行处理。

### P3-2: `sys.path.insert(0, ...)` 模式

多个 wrapper 用 `sys.path.insert(0, ...)` 而非可安装 package。无泄漏风险但引入 import 顺序脆弱性。

### P3-3: 全局模块级 `_AUTH_HEADERS` 在导入时求值

`a2a_mcp_server.py:102`。若 token 在运行中被轮换，模块级变量不会更新。虽有 `load_auth_tokens` 支持多个有效 token，但 `_AUTH_HEADERS` 在进程生命周期中不变。

---

## 历史 1GB/28h 泄漏推断

基于静态审查，**最可能原因排序**：

1. **`task_agent_mapping` 内存泄漏（P0-1）** — 每个任务 200B+ dict overhead，28h 内数千任务叠加，加上 JSON 序列化反复分配。若平均每秒 1 任务 × 28h ≈ 100,800 entries × 10KB 序列化 buffer → 轻松数百 MB。
2. **次之：`_health_cache`（P1-2）+ `_deep_health_cache`（P1-6）** — 健康探测 cache 条目累积。5 个 agent × 1 次/分 × 1680min ≈ 8400 entries。条目虽小（~100B），但 `DeepHealthResult` 含 probe 结果文本，可能数 KB → 数十 MB。
3. **第三：metrics JSONL 文件磁盘增长（P1-4）** — 非内存泄漏但 28h 写入数千行 JSON，可能数百 MB 磁盘占用，表象上看起来像进程"吃内存"。

**非原因排除**：
- `_task_store` 有 TTL reaper（`:1785-1809`），每 5 分钟清理 1h 前的 terminal records → 有界。
- `_running_procs` 和 `_running_procs_grace` 在 `finally` 中总是 pop → 有界。
- `_circuit_state` 静态 5 个 agent → 有界。
- `_agent_clients` 静态 5 个 URL → 有界。

---

## 良构评估

| 维度 | 得分 (1-10) | 评语 |
|------|------------|------|
| **可靠性** | 7/10 | 多个 layer 的 circuit breaker（wrapper 级 + MCP 级 + upstream 级）；健康探测 + 任务 reaper。扣分：task_agent_mapping 无界增长、subprocess 句柄回收不保证。 |
| **可维护性** | 7/10 | 清晰的分层架构（wrapper → MCP server → dispatch/daemon），详细的 docstring。扣分：`sys.path.insert(0, ...)` 模式、约 2000 行 a2a_mcp_server.py 偏大、多份 httpx client 创建分散。 |
| **可观测性** | 8/10 | 全面 metrics JSONL（每个 wrapper）+ dispatch audit + replay + TG/WX 通知。扣分：metrics 无轮转可能撑爆磁盘、部分异常静默吞掉。 |
| **安全性** | 8/10 | Bearer token 认证、workdir allowlist、token 轮换。扣分：`_AUTH_HEADERS` 模块级缓存不感知轮换、无 HTTPS（但 loopback 场景合理）。 |

### 总结论

**这是一个设计良好的可靠性优先系统**，有事故经验驱动的演进痕迹（circuit breaker、failover、watchdog、健康探测、reaper）。主要技术债务集中在**无界增长的存储结构**（dict、JSONL、temp file）和**子进程句柄回收的不确定性**。这些是长时间运行稳定性最直接的威胁，与历史 1GB/28h 泄漏高度吻合。

修复 P0 + P1 即可大幅提升长跑稳定性。良构层面核心架构和文档清晰，无需大改。

---

## 检查清单核对

| 检查项 | 状态 |
|--------|------|
| `run_command` 超时后 kill+reap | ⚠️ P0-3 — wait 超时后泄漏句柄 |
| stdout/stderr 全量 buffer | ⚠️ P3-1 — 短暂内存峰值，非泄漏 |
| asyncio task 孤儿 | ✅ 无可疑孤儿 task（reaper 管理） |
| httpx client 复用 | ⚠️ P2-1 — 多处创建临时 client，但不泄漏 |
| metrics JSONL 无界增长 | ⚠️ P1-4 — 无轮转 |
| `task_agent_mapping` 只增不减 | 🚨 P0-1 — 确认泄漏 |
| `_circuit_state` 只增不减 | ⚠️ P1-3 — 静态场景有限；动态注册时泄漏 |
| `_health_cache` / `_deep_health_cache` | ⚠️ P1-2 / P1-6 — 缓存无条目清理 |
| `_agent_clients` | ⚠️ P1-1 — 限于静态 agent 量 |
| daemon JS 日志 fd | ✅ 有轮转逻辑，仅在 spawn 时触发 |
| daemon JS spawn 失败重试 | ✅ 指数退避 + circuit open |
| daemon JS watchdog | ✅ 30s 间隔健康探测 + 重启 |
| `.reasonix-metrics-*` 残留文件 | 🚨 P0-2 — 12 个孤立文件现场发现 |

---

*报告生成: 2026-07-15 | mode: read-only static audit | no files modified*
