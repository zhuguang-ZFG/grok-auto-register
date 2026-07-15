# A2A 工单：agentic 深度探测 + 断流检测自动轮换

- risk: med
- owns:
  - `D:/Users/grok-auto-register/scripts/cc_rotate_claude_provider.py`
  - `C:/Users/zhugu/.kimi-code/mcp-a2a-bridge/claude_a2a_wrapper.py`
- 日期: 2026-07-15

## 背景

四渠道（倍佬/百佬/林夕/林佬）上游都是 kiro 反代。社区确认的典型毛病不是号死，而是
**agentic 断流**：模型说一句话就 end_turn，工具调用环断掉（根因：tool_use/stop_reason
映射错、agentContinuationId 不保持、identity 占位符未替换、工具 schema 截断）。
现有 probe 只打 `/v1/messages` max_tokens=1 —— **测得出活、测不出工具断流**。

## 任务

1. **agentic 深度探测**（cc_rotate_claude_provider.py）
   - 新增 `agentic-probe` 子命令：对每个渠道发带 dummy tool 的 `/v1/messages`
     （工具 schema 简单，如 `get_weather(location: string)`，prompt 引导其必须调工具，
     max_tokens 控制在小值），判定标准：
     - `agentic-ok`：响应含 `tool_use` block 且 `stop_reason == "tool_use"`
     - `agentic-degraded`：能连但无 tool_use / stop_reason==end_turn / 其它异常（附原因）
     - `down`：连接级失败（沿用现有分类）
   - `try-next --agentic`：只切到 `agentic-ok` 的渠道；没有则拒绝轮换（沿用现有语义）。
   - 探测输出仍不得打印完整 token（最多尾部 6 位）。

2. **断流检测 → 自动轮换**（claude_a2a_wrapper.py）
   - 新增运行期特征判定：任务**异常快速完成**（如 <20s）**且输出异常短**（如 <500 字符）
     **且无任何工具调用痕迹**，而 prompt 明显要求代码/审查动作（prompt 长度 > 阈值）——
     满足全部条件才视为 `suspect_truncated`，按渠道故障处理走轮换。
   - 必须保守：任一条件不满足就不触发（宁可漏判，不可误判正常短任务）。
   - env 开关 `CLAUDE_TRUNC_DETECT`（默认 1，0 关闭）；阈值用 env 可调。
   - 触发时 metrics 记录 `truncated=true` + 渠道名，落 data/ 供后续分析 kiro-go 补/换决策。

3. **metrics/日志**：agentic probe 结果与 truncated 事件都带渠道名 + 时间戳落盘。

## gates

```gates
两文件 py_compile
python scripts/cc_rotate_claude_provider.py agentic-probe
  → GLM 三渠道 agentic-ok；k40/100xlabs down 或 degraded（当前它们 503）
python scripts/cc_rotate_claude_provider.py try-next --agentic → 当前 GLM 健康应不切
单测：构造强断流特征输出 → 触发轮换；构造正常短任务输出（如"回复OK"任务）→ 不触发
单测：CLAUDE_TRUNC_DETECT=0 时任何输出都不触发
输出无完整 token 泄漏
```

## 注意

- 不动 cc-switch DB 写路径；不重启 A2A 桥/daemon/wrapper（Kimi 统一做）。
- 不破坏既有：CLAUDE_CHANNEL_FAILOVER / CLAUDE_FALLBACK_* / try-next 原语义、pin 协调、
  锁+原子写、任务级超时不轮换（上一工单成果）。
- 结果写到 `D:/Users/grok-auto-register/_review/agentic_probe_result.md`。
