# A2A 工单：舰队系统级审查（内存泄漏 + 良构评估）

- risk: med
- type: 只读审查（报告，不改代码）
- owns: 无（read-only）
- 日期: 2026-07-15

## 背景

舰队（cursor/atom/claude/reasonix/kimi + node daemon + a2a_mcp_server）长跑 2.6h+。
历史上出现过：28h 运行 1GB 内存的旧进程（已杀，疑泄漏）、daemon 重复 spawn 双份 wrapper（已修）。
当前快照（2026-07-15 22:30，MB/运行h）：

```
a2a_mcp_server   86MB/2.6h (+2MB 伴随进程)
node daemon      48MB/0.7h
kimi wrapper     68MB/0.6h
reasonix wrapper 55MB/2.6h
atom wrapper     52MB/2.6h
claude wrapper   52MB/0h
cursor wrapper   31MB/2.6h
（每个 agent 另有 1-6MB uv pythonw 跳板，正常）
```

## 任务

1. **内存泄漏静态审查**（C:/Users/zhugu/.kimi-code/mcp-a2a-bridge/）：
   - `shared_a2a_server.py` 的 `run_command`：子进程超时后是否 kill+reap（Windows 上
     不 reap 会泄漏句柄/内存）、stdout/stderr 缓冲是否全量驻留、asyncio task 是否有孤儿
   - 各 wrapper：长驻连接（httpx/aiohttp client）是否复用且有关闭路径、metrics
     `append_agent_metrics` 文件句柄与 JSONL 无界增长（有无轮转）
   - `a2a_mcp_server.py`：task store / delivery store / SSE 连接 / upstream circuit
     状态是否随时间无界增长（dict 只增不减 = 典型泄漏）
   - `a2a-agents-daemon.js`：watchdog interval、日志 fd、spawn 失败重试路径
   - 历史 1GB/28h 案例最可能对应上述哪条路径，给出推断
2. **良构评估**：单点、错误分类一致性、config 漂移（docs vs code）、测试覆盖、
   以及「这是一个良好的系统吗」的总结论（分项打分：可靠性/可维护性/可观测性/安全性）。
3. 输出：P0-P3 分级清单（每条必须 file:line 证据）+ 修复建议优先级。

## gates

```gates
报告写到 D:/Users/grok-auto-register/_review/a2a_system_audit_result.md
每个 P0/P1 附 file:line 证据（可引代码行）
不改任何 .py/.js/.json
```

## 注意

- 不重启任何进程、不改代码。
- 历史背景参考：logs/_atom_arch_review_failover.md、docs/channel-failover-design.md（仓库）。
