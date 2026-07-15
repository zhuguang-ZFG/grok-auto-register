# A2A 工单：Claude 通道故障转移加固（Atom + grok-4.5 双评审合并项）

- risk: med
- owns:
  - `C:/Users/zhugu/.kimi-code/mcp-a2a-bridge/claude_a2a_wrapper.py`
  - `D:/Users/grok-auto-register/scripts/cc_rotate_claude_provider.py`
  - `D:/Users/grok-auto-register/scripts/_claude_review_watchdog.py`
- 日期: 2026-07-15

## 背景

两级故障转移（渠道级轮换 + 执行器级 kimi fallback）已上线并实证。Atom 架构评审
（`D:/Users/grok-auto-register/logs/_atom_arch_review_failover.md`）与 grok-4.5 独立复核
（`D:/Users/grok-auto-register/logs/_grok45_failover_review.md`）双 VERDICT: fail，结论重合。
本工单按 grok-4.5 的修复优先级取前 4 项，全是「误耗/误判」类，不改架构。

## 任务

1. **收紧渠道错误判定**（wrapper）
   - 现状：`timeout|timed out` 正则把任务级 900s 超时（长任务慢，不是渠道坏）也当渠道故障触发连环轮换。
   - 改：区分**连接级故障**（connection refused/reset、HTTP 502/503/5xx、CF 1010、401/403，触发轮换）
     与**任务级超时**（进程跑了很久被超时杀，不触发轮换，直接判任务失败）。
   - 任务级超时不消耗 `CLAUDE_CHANNEL_FAILOVER` attempts。

2. **降级结果显式语义，禁止静默 ok**（wrapper）
   - 现状：executor fallback 里 `except Exception: pass`，结果仍 `ok=True` 进 metrics——舰队无法区分真 Claude 复核和 kimi 兜底。
   - 改：fallback 路径的异常不得吞；metrics/状态记录显式带 `degraded=true`、`executor=kimi-fallback`、`model=<实际模型>`；
     输出前缀 `[executor-fallback: ...]` 已有，保持。真 Claude 路径 `degraded=false`。
   - 修完后自查：任何「失败被吞但记成功」的路径都不能存在。

3. **probe-then-switch**（wrapper + cc_rotate_claude_provider.py）
   - 现状：`next` 盲轮换，会跳到死 host 空耗 attempts。
   - 改：wrapper 轮换前对候选渠道跑 `cc_rotate_claude_provider.py probe`（或等价轻量探测），
     只切到 healthy 候选；全不健康时直接进入执行器级 fallback，不再空转。
   - 探测输出仍不得打印完整 token（最多尾部 6 位）。

4. **settings.json 原子写 + 跨进程锁 + 看门狗 pin 协调**
   - 写者（cc_rotate / 看门狗）统一：先写临时文件再 `os.replace` 原子替换；
     跨进程互斥用 `msvcrt.locking` 锁文件（如 `~/.claude/.settings.json.lock`，仅用标准库）。
     顺序：先 settings.json 后注册表，注释里写明。
   - 看门狗切回真 Claude 后写 pin 标记（如 `~/.claude/.channel_pin.json`，含渠道名+过期时间戳）；
     wrapper 轮换前检查：pin 未过期且当前渠道 == pin 渠道时，要求连续 N 次（建议 3）连接级失败才允许轮换。
   - wrapper 读 settings.json 侧：读到非法 JSON 时短暂重试（如 3 次 × 200ms），仍失败则按当前任务失败处理，不轮换。

## gates

```gates
python -m py_compile C:/Users/zhugu/.kimi-code/mcp-a2a-bridge/claude_a2a_wrapper.py
python -m py_compile D:/Users/grok-auto-register/scripts/cc_rotate_claude_provider.py D:/Users/grok-auto-register/scripts/_claude_review_watchdog.py
python D:/Users/grok-auto-register/scripts/cc_rotate_claude_provider.py list
python D:/Users/grok-auto-register/scripts/cc_rotate_claude_provider.py current
python D:/Users/grok-auto-register/scripts/cc_rotate_claude_provider.py probe
单测：注入任务级 timeout 输出 → 不触发轮换、不消耗 attempts
单测：注入 502/connection refused → 触发轮换
单测：两进程并发写 settings.json 各 50 次 → 最终 JSON 合法
单测：fallback 路径 metrics 含 degraded=true + executor=kimi-fallback
单测：pin 未过期时连续 2 次连接级失败 → 不轮换；连续 3 次 → 轮换
上述输出无完整 token 泄漏
```

## 注意

- **不要重启 A2A 桥 / daemon / wrapper**（Kimi 统一做）；wrapper 每任务现场读 settings.json，改完代码下一轮任务自然生效。
- 不要动 cc-switch DB 写路径（GUI 领地，只读）。
- 保持 `CLAUDE_CHANNEL_FAILOVER` / `CLAUDE_FALLBACK_EXEC` / `CLAUDE_FALLBACK_MODEL` 三个 env 语义不变。
- 结果写到 `D:/Users/grok-auto-register/_review/failover_hardening_result.md`。
