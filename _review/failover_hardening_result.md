## 返修 round2

**日期**: 2026-07-15
**类型**: 首次实现被 Atom 初审 fail 后返修
**Atom 报告**: `logs/_atom_hardening_review.md`

### 修复清单

| 严重度 | 问题 | 文件 | 修复 |
|--------|------|------|------|
| **P0** | 锁泄漏：`_acquire_lock()` 超时返回 `None` 时 `apply_provider` 仍无锁写 settings.json | `cc_rotate_claude_provider.py` | 锁获取失败时 `raise RuntimeError("锁获取失败，放弃写入 settings.json")`，绝不无锁写 |
| **P1** | Pin 渠道未校验：只判活没比对渠道名，跨渠道轮换被错误抑制 | `claude_a2a_wrapper.py` | `_check_pin()` 增加 `token_suffix` 比对：当前 settings 的 token 后缀必须匹配 pin 的 `token_suffix` 才认为 pin 活跃 |
| **P1** | 同上，看门狗 pin 不含标识 | `_claude_review_watchdog.py` | `switch_to()` 写入 pin 时从 settings.json 读取当前 token 后缀写入 `token_suffix` 字段 |
| **P1** | 同上，Provider `write_pin()` 不含标识 | `cc_rotate_claude_provider.py` | `write_pin()` 增加 `token_suffix` 字段 |
| **P1** | `FileNotFoundError` 重试 3 次浪费 600ms | `claude_a2a_wrapper.py` | `_read_settings_with_retry()` 将 `FileNotFoundError` 单独处理，直接 `return {}` 不重试 |
| **P2** | 502/503 正则无边界，误伤端口/版本号 | `claude_a2a_wrapper.py` | `502|503` → `\b50[23]\b`，验证：`port 15025` / `1503-beta` 等不再匹配 |
| **P2** | `_read_settings_with_retry` 尾行 `return {}` 不可达 | `claude_a2a_wrapper.py` | 函数体重构，仅有意义的 `return {}`（JSONDecodeError 重试耗尽后）保留 |

### 设计说明

**token_suffix 机制**（P1）：因 wrapper 无法直接访问题库获取渠道名 → token 映射，改用 token 末 12 位作为 pin 与当前渠道的指纹。pin 写入方（看门狗/provider）读取 settings.json 的 `ANTHROPIC_AUTH_TOKEN` 取末 12 位写入；`_check_pin()` 读当前 settings.json 比对 token 末 12 位。不同渠道 token 不同，末 12 位碰撞概率可忽略。

### Gates 结果

| Gate | 结果 |
|------|------|
| `py_compile claude_a2a_wrapper.py` | PASS |
| `py_compile cc_rotate_claude_provider.py` | PASS |
| `py_compile _claude_review_watchdog.py` | PASS |
| `provider list` | PASS（7 渠道） |
| `provider current` | PASS |
| `provider probe` | PASS |
| 正则 \b50[23]\b 防误伤（15025/1503/25023 不匹配，502/503 匹配） | PASS |
| 锁守卫代码路径验证 | PASS |
| pin token_suffix 比对逻辑验证 | PASS |
| FileNotFoundError 0 重试验证 | PASS |

### 遗留

- 锁泄漏的跨进程集成测试需在两进程互斥场景下人工验证（10s 超时阻塞，不适合自动化）
- Pin 的跨场景（匹配/不匹配/过期）集成测试需 mock pin 文件

---

## 返修 round3（Claude xreview fail 项，2026-07-15，实现员 Kimi）

对照 `logs/_claude_hardening_xreview.md`（VERDICT: fail，BLOCKER=B1）逐项修复。

### 修复项

| 项 | 问题 | 文件 | 修复 |
|----|------|------|------|
| **B1 (P1)** | `if attempt >= attempts: break` 排在 pin/轮换之前，默认 FAILOVER=3 + 阈值=3 时 pin 窗口第 3 次连接级失败先 break，「连续 3 次→轮换」不可达 | `claude_a2a_wrapper.py` | `for attempt in range(...)` 改为 `while attempt < attempts` 显式计数；pin 窗口内连接级失败用 `consecutive_conn_fails` 独立计数与 `attempt` 解耦：pin 活跃未达 3 次→`attempt -= 1` 重试不消耗预算；达 3 次→try-next 轮换，成功→`attempt -= 1` 不消耗继续，失败→break 进兜底。非 pin 路径预算语义不变（回归测试锁定） |
| **B2** | `apply_provider` 的 `read_settings()` 在取锁之前，RMW 未被锁覆盖 | `cc_rotate_claude_provider.py` | 先 `_acquire_lock()`，锁内 `read_settings()` → 改 → `_write_settings_atomic()`，`finally` 释放；消除非 ANTHROPIC 键 lost-update 窗口 |
| **B3** | pin 文件损坏时 `_check_pin` 静默吞异常 | `claude_a2a_wrapper.py` | `except` 打一行 WARNING 日志（含异常类型/消息），仍按 pin 不活跃处理，行为不变（fail-safe 方向不动） |
| **B4** | doc/code 漂移（`executor_fallback` vs 代码键 `executor`）；degraded 运行无法被消费方区分 | `docs/ops_quick.md`、`a2a_metrics_report.py` | 文档统一为代码键名 `executor`（并补注 `degraded=true`/`model`）；`summarize_metrics` 新增 `degraded_runs`（degraded=true 单列计数）与 `by_executor`（如 `{"kimi-fallback": N}`），空结果也带两键保 schema 稳定 |

### Gates 结果（全部实跑）

| Gate | 结果 |
|------|------|
| `py_compile claude_a2a_wrapper.py / cc_rotate_claude_provider.py / _claude_review_watchdog.py / a2a_metrics_report.py` | PASS |
| 单测 `test_pin_two_fails_no_rotation_third_rotates`：pin 活跃 + 连续 2 次连接级失败→不轮换（rotate=0）；第 3 次→轮换（mock try-next 成功）→健康渠道成功，fallback=0 | PASS |
| 单测 `test_pin_rotation_failure_goes_fallback`：pin 活跃达阈值 + 轮换失败→执行器兜底 | PASS |
| 单测 `test_no_pin_attempt_budget_unchanged`：无 pin 时 run=3 / rotate=2 / fallback=1，预算语义不回归 | PASS |
| 单测 `test_concurrent_apply_provider_no_lost_update`：双进程各 25 次并发 `apply_provider`→settings.json 合法、`KEEP_ME`/`permissions` 非 ANTHROPIC 键不丢、ANTHROPIC 三元组来自同一次一致写入 | PASS |
| 单测 `test_fallback_metrics_row_on_disk`：fallback 路径 metrics 落盘含 `degraded=true`/`executor=kimi-fallback`/`model` 三键；且 `summarize_metrics` 输出 `degraded_runs=1`、`by_executor={"kimi-fallback":1}` | PASS |
| `pytest tests/test_claude_failover_round3.py + test_shared_metrics.py + test_cost_budget.py + test_empty_run_gate.py` | 35 passed |
| `python scripts/cc_rotate_claude_provider.py current` | PASS（当前 GLM5.2 渠道，token 仅末 6 位） |
| `python scripts/cc_rotate_claude_provider.py list` | PASS（7 渠道，token 仅末 6 位） |

### 备注

- 未重启任何 A2A 进程；改动均为最小 diff。
- xreview P2 残留中本工单未覆盖项（`_TASK_TIMEOUT_PAT` 收紧、兜底失败补 `ok=False` metrics 行、下游 JS 消费点）未动，留后续工单。
