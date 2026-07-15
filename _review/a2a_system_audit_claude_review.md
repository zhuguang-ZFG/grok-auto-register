# A2A 系统审查 — Claude 交叉审查意见 (xreview)

- **日期**: 2026-07-15
- **审查人**: Claude (senior system architect, xreview 角色)
- **被审报告**: `a2a_system_audit_result.md` (Reasonix, read-only static audit)
- **模式**: 只读，未修改任何代码
- **结论 (verdict)**: **concur-with-additions**
  - 报告列出的 P0/P1/P2/P3 缺陷**逐条属实**（我独立读码核对，file:line 锚点准确，偏差 ≤2 行）。
  - 但报告的**历史 1GB/28h 根因排序不可靠**：把 P0-1 列为 #1 主因既无幅值支撑，又**归到了错误的进程**。报告还遗漏了若干**量级更大**的内存面（CPython 堆碎片化、`_task_store`、SQLite、reaper 脆弱性）。
  - 单凭静态审查**无法指认任何单一结构为 1GB 元凶**；必须上运行时遥测（tracemalloc/objgraph/按进程 RSS 时间线）。报告应如此声明，而不是给 P0-1 戴帽子。

---

## 0. 一句话总评

报告的**缺陷发现质量高、锚点可信**；报告的**根因推断是全文最弱的一环**，需要降级并补齐进程归属与运行时验证。两者并不矛盾：发现真缺陷 ≠ 该缺陷就是历史症状的元凶。

---

## 1. 任务一 — P0-1 是否真是 1GB/28h 的主因？有没有更可疑的遗漏面？

### 1.1 结论：P0-1 是真泄漏，但**不可能是 1GB 的主因**（幅值差 ~50–70×）

`task_agent_mapping` 是 `Dict[task_id(uuid) -> agent_url(str)]`。单条持久占用 ≈ key(36B) + value(40–60B) + CPython dict entry 开销 ≈ **150–200B/条**。

- 报告自设的速率假设「平均每秒 1 任务 × 28h ≈ 100,800 条」→ 持久占用 ≈ **15–20MB**。
- 要撑到 1GB 需要 **~500–700 万条**，即 28h 内每秒 ~50–80 个任务持续提交 —— 与该系统（IDE 触发、5 个 agent、bursty）的实际负载不符。
- 报告写道「× 10KB 序列化 buffer → 数百 MB」——**这一步算错了**。`save_to_json` 每次把整个 dict 序列化成一个临时字符串，写完即释放，**不累积**。它是 I/O 放大（见 §4-G），不是持久内存。

因此按幅值，P0-1 对 1GB 的贡献是**个位数 % 以内**，把它排为 #1 主因**站不住脚**。

### 1.2 更可疑的遗漏面（按量级排序，静态推断）

> 前提澄清：报告通篇把代码当成**一个进程**，但实际上存在**两类长跑进程**，泄漏面分布在不同进程里：
> - **MCP server 进程** (`a2a_mcp_server.py`)：持有 `task_agent_mapping` / `_agent_clients` / `_health_cache` / `_circuit_state` / `_deep_health_cache`；**不调用 `run_command`**（grep 确认只 `from shared_a2a_server import BearerAuthMiddleware, auth_headers`）。
> - **Wrapper 进程** (`reasonix/atomcode/claude/kimi_a2a_wrapper.py`，基于 `shared_a2a_server.py`)：持有 `_task_store` / `_running_procs` / `run_command` / `_kill_proc_graceful`，**在内存里 buffer 整段 CLI 的 stdout+stderr**。
>
> 「28h/1GB 的进程」**最可能是 wrapper**（因为它 buffer 大输出），而报告的 #1 元凶 P0-1 却住在 **MCP server**。**报告从未说明是哪个进程撑到 1GB** —— 这是根因推断最致命的空白。在不知道进程归属的前提下，任何归因都是悬空的。

我的候选排序（静态、量级优先；**均无法静态证实**，需运行时确认）：

1. **CPython 堆碎片化 / arena 不归还 OS**（报告**完全没提**）。28h 内反复 `stdout.decode()+stderr.decode()`（`shared_a2a_server.py:1955`，2–3× 峰值）+ `sanitize_agent_output` + `extract_path_artifacts` 正则全量扫描，每次分配 MB 级临时字符串。CPython/pymalloc 与 Windows CRT 对这种「突发大对象」的 arena 几乎不归还 OS → RSS 爬升后**平台化**。这是「Python 进程长跑到 N GB 然后稳住」的经典画像，与 28h/1GB 高度吻合。**这是头号嫌疑**，但必须 tracemalloc/objgraph 才能证实。
2. **`_task_store` 持有大输出**（报告**没提**）。每条 `TaskRecord.result` 含**整段** `result_text` + `artifacts`（`shared_a2a_server.py:439`、`:1096`、`:1405`）。reaper 只清「1h 前的 terminal 记录」（`:1785-1795`）→ 稳态下窗口内 = **最近 1h 所有任务的全量输出**。若单任务输出 MB 级、并发若干，轻松数百 MB。
3. **SQLite 无界增长 + 页缓存**（报告**没提**）。`DELETE FROM tasks … terminal_at < ?` **只在启动 `_init_db` 跑一次**（`:784-785`），运行期**没有任何 DELETE**，全仓 grep 也**没有 VACUUM**。28h 内每任务一行 INSERT，DB 文件单调增长；SQLite 页缓存随表增大而驻留内存。
4. **reaper 脆弱性**（报告**没提**，见 §4-E）：`_start_ttl_reaper` 里 `asyncio.create_task(_ttl_reaper())`（`:1814`）**没有保留强引用** —— asyncio 官方明确警告 task 可能被 GC。一旦 reaper 静默死亡，`_task_store` 变**无界**，wrapper 内存线性增长。这是少数能在 wrapper 里造成「硬无界」的路径。
5. **才轮到 P0-1**（MCP server，数十 MB 量级）+ 各 P1 缓存。

**结论**：报告把第 5 名当成了第 1 名，且没区分进程。

---

## 2. 任务二 — P0-1 / P0-2 / P0-3 逐条核对（我独立读码）

| 编号 | 报告主张 | 我的核对 | 裁定 |
|------|----------|----------|------|
| **P0-1** | `task_agent_mapping` 只在 `unregister_agent`(`:708-714`) 删除，正常完成不删 → 无界增长 | 读 `:113` 初始化、`:868` 添加、`:900`/`:1324` 全量写盘、`:708-714` 唯一删除路径。全仓 grep 确认**无其它删除**。MCP server 有 `periodic_save()`(`:1998`) **却无 periodic prune** —— 不对称证实了「只存不删」。 | **CONFIRM（缺陷属实）**。但作为「1GB 主因」**refute**（量级不符、且在 MCP server 而非疑似撑爆的 wrapper）。 |
| **P0-2** | `_try_parse_metrics` 的 `unlink` 仅在其被调用时执行；`execute_reasonix` 的 `AgentExecutionError` 逃逸路径**跳过** `_post_process_run`/`_try_parse_metrics` → temp 文件残留 | `_try_parse_metrics`(`:299-316`) 的 `unlink` 在 `finally` 里，**确实只在被调用时**清理。逃逸路径不止报告点的 `:689-690`（auto-continue 内层），**更普遍的是外层 `:753-792`**：`if in_auto_continue or e.text.startswith("[reasonix:"): raise`（`:756-757`）与终态 `raise`(`:790`) **都不碰 metrics_file**。我现场 `ls data/.reasonix-metrics-*.json \| wc -l` = **12**，佐证。 | **CONFIRM**。但性质是**磁盘泄漏（~KB/文件）**，对 1GB RSS **零贡献**；P0 严重性按「卫生」可成立，按「内存影响」应是 P1。建议补一个 `execute_reasonix` 顶层 `try/finally` 无条件 `metrics_file.unlink(missing_ok=True)`。 |
| **P0-3** | `run_command` 超时后 `_kill_proc_graceful` 的最终 `proc.wait()` 仅 3s、超时即 `pass` → Windows 句柄泄漏 | 代码与报告一致（`:1884-1887` `pass`，`:1951-1954` finally 只 pop dict 不 wait）。**但「必然句柄泄漏」被高估**：Windows asyncio 的 subprocess transport 在进程退出后会**异步回收**句柄/管道，即使显式 `proc.wait()` 超时；真正的句柄/管道泄漏**只在进程扛过 `taskkill /F /T` 不死时**才发生（代码注释 `:1922-1924` 自己点名的「孙进程持管道」才是真风险，属小概率）。 | **CONFIRM（robustness smell 属实）**，但 **P0 严重性 OVERSTATED → 建议 P1**。修法（retry-wait / kill 后再 wait）合理。 |

> 补充：P0-2 的逃逸锚点报告只给了 `:689-690`，建议修正为「外层 `:753-792` 的 re-raise 路径」更准确。

---

## 3. 任务三 — 良构打分是否公允（架构视角）

| 维度 | 报告分 | 我的评估 | 备注 |
|------|--------|----------|------|
| 可靠性 | 7 | **6–7（可接受）** | 多层 circuit breaker + watchdog + reaper 是真功底。但 `task_agent_mapping` 无界、SQLite 运行期不 prune、reaper 无强引用、`_task_store` 1h 窗口持大对象，都是长跑稳定债。 |
| 可维护性 | 7 | **6–7（略高估）** | `a2a_mcp_server.py` **2093 行**、`sys.path.insert(0,…)` 黑魔法、10+ 处临时 `httpx.AsyncClient`、`send_message` 与 `send_message_stream` 逻辑重复。 |
| 可观测性 | 8 | **7–8（略高估）** | 覆盖面广（metrics JSONL + audit + replay + TG/WX）。但 metrics 无轮转 → **可观测性会自己把磁盘打爆**；多处 `except: pass`/`except Exception: logger.debug` 静默吞错。 |
| 安全性 | 8 | **7（偏高估）** | Bearer 认证 + workdir allowlist + token 轮换不错。但：(1) `_AUTH_HEADERS` 模块级缓存不感知轮换（报告已点）；(2) **出站无 agent_url allowlist** —— `register_agent`(`:564`) 可注册任意 URL，`send_message` 随后带 `_AUTH_HEADERS` POST 过去 → **SSRF + token 外泄面未被评估**；(3) 仅 loopback HTTP（可接受）。 |

**净评**：四项都在「可辩护」区间，但**系统性偏高 ~0.5–1 分**，未达「明显不公」的程度，不构成 dissent。若由我打：可靠 6 / 维护 6 / 可观测 7 / 安全 7。

---

## 4. 任务四 — 报告未覆盖的风险（我的补充）

- **[A] 进程归属缺失（最关键空白）**：报告从未声明哪个进程撑到 1GB。MCP server 与 wrapper 是**不同地址空间**，把住在 MCP server 的 P0-1 当作（疑似 wrapper 的）1GB 元凶，归因悬空。
- **[B] CPython 堆碎片化 / arena 不归还**：头号嫌疑，报告只字未提。建议先抓 `tracemalloc` 快照对比 + RSS 曲线，再下根因结论。
- **[C] `_task_store` 启动全量 hydrate + 大对象 1h 窗口**：`shared_a2a_server.py:799-817` 启动时把 SQLite 全部行灌进内存（重启即尖峰）；运行期 `result.result_text`/`artifacts` 整段驻留至 reaper 清理。报告漏掉。
- **[D] SQLite 运行期不 prune + 无 VACUUM**：`:784-785` 的 DELETE 仅启动跑一次；28h 内 DB 单调增长，页缓存吃内存。报告漏掉（P1-4 只谈了 metrics JSONL，没谈 tasks DB）。
- **[E] reaper 无强引用 + `on_event` 弃用**：`asyncio.create_task(_ttl_reaper())`（`:1814`）未保存引用 → 官方 footgun，task 可能被 GC → `_task_store` 无界。且 `@app.on_event("startup")` 在新版 FastAPI/Starlette 弃用，存在 lifespan 模式下不触发的风险。建议改成 lifespan handler 并 `tasks.add_done_callback` / 持引用到 `app.state`。
- **[F] SSRF / token 外泄面**：`register_agent` + 出站 POST 携 `_AUTH_HEADERS`，无 URL allowlist。即使认证在身，注册环节被滥用即可把 bearer 投到攻击者 URL。建议加出站 URL allowlist（host:port）。
- **[G] `save_to_json` 写放大 O(n²)**：每次 `send_message` 全量重写 `task_agent_mapping.json`（`:900`）。到 10 万条时每任务一次多 MB 临时串 + 全量磁盘写 —— 喂养碎片化 + I/O 风暴。P1-5 只说了「文件增长」，没说「每写 O(n)」成本。
- **[H] periodic_save vs 无 periodic_prune 的不对称**：MCP server 有 `periodic_save()`(`:1998`) 持久化 mapping，却**没有对应的周期 prune** —— 这从架构上坐实了 P0-1 是「设计漏项」而非「偶发 bug」。

---

## 5. 任务五 — Verdict 与理由

**Verdict: `concur-with-additions`**

** concur 的部分（报告对的地方）：**
1. 所有 P0/P1/P2/P3 缺陷**逐条属实**，file:line 锚点**准确**（我读码核对，偏差 ≤2 行）。
2. 方法论（grep 无界增长结构）合理，「无界存储 + 句柄回收不确定」是长跑系统最直接的威胁。
3. 修复方向（reaper/TTL/统一 client/轮转/顶层 finally）**技术正确**，应执行。

** with-additions 的部分（报告需补强的地方）：**
1. **根因排序降级**：P0-1 **不是** 1GB/28h 的主因（幅值差 ~50×，且住在 MCP server 而疑似撑爆的是 wrapper）。报告「最可能原因排序 #1」应改为「**未定**，需运行时验证」。
2. **必须指明进程归属**：在拿到「哪个进程、RSS 时间线、gc/tracemalloc」之前，任何单点归因都不可发表。
3. **补齐遗漏面**：CPython 碎片化（B）、`_task_store`（C）、SQLite（D）、reaper 脆弱性（E）、SSRF（F）、写放大（G）—— 其中 B/C/D/E 量级均大于 P0-1。
4. **P0-3 严重性下调 P1**：Windows asyncio 在进程真死后会异步回收句柄，「必然泄漏」被高估；真风险是孙进程持管道（小概率）。
5. **良构分系统性偏高 ~0.5–1**：建议可靠 6 / 维护 6 / 可观测 7 / 安全 7（仍属「设计良好的可靠性优先系统」，无需大改）。

**不构成 dissent 的理由**：报告没有陈述**伪事实**，缺陷也都真实存在；问题出在「症状归因」与「覆盖完整度」，属于审查深度不足而非结论错误。按 xreview 职责（security/architecture/silent-failure 优先、不橡皮图章），我选择 `concur-with-additions` 而非 `concur`，核心就是把报告从「P0-1 是元凶」拉回到「先上遥测、再看量级」的工程正道。

---

## 6. 给下一步的可执行建议（仅建议，不改代码）

1. **先观测，再修**：在疑似撑爆的 wrapper 上挂 `tracemalloc`（启动 25 行快照 + 周期 top-10），同时记录每进程 RSS 时间线与 gc 统计，跑一个 28h 复现。**没有这条数据，根因永远是猜测。**
2. 修 P0-1 时**顺带消除写放大**：mapping 改为「周期 prune terminal 映射 + 增量/节流写盘」，而非简单加删除。
3. P0-2 改 `execute_reasonix` 顶层 `try/finally` 无条件 `unlink`；P0-3 加 kill 后的有限 retry-wait。
4. 把 `_ttl_reaper` 迁到 lifespan 并持强引用；给 tasks DB 加运行期周期 prune + 定期 VACUUM；给 metrics JSONL 加尺寸轮转。
5. 出站加 agent_url allowlist，消除 SSRF/token 外泄面。

---

*xreview by Claude | 2026-07-15 | read-only | 被审报告: a2a_system_audit_result.md (Reasonix)*
