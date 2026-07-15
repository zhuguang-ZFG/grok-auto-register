# A2A 工单：cc-switch Claude 渠道轮换按 host 去重 + k40 打通

- risk: low
- owns: `scripts/cc_rotate_claude_provider.py`, `scripts/_run_cc_rotate.cmd`
- 日期: 2026-07-15

## 背景

cc-switch DB（`C:/Users/zhugu/.cc-switch/cc-switch.db`，只读）有 4 个 Claude 渠道：

| idx | 名称 | base_url | 说明 |
|-----|------|----------|------|
| 0 | 「林夕」公益站 | https://k40.shengqainbang.cn | token ...a0a5f6 |
| 1 | 倍佬 | https://sub.100xlabs.space | token ...722663 |
| 2 | 林佬 | https://k40.shengqainbang.cn | token ...c11d7f, model=claude-opus-4-8 |
| 3 | 百佬 | https://sub.100xlabs.space | token ...648f3c, model=claude-opus-4-6 |

**问题 1**：`sub.100xlabs.space` 网关当前 503「No available accounts」/ 502（上游号池抽空，服务端故障）。它挂着 A2A Claude wrapper（4942），egress 工单交叉复核被卡。轮换脚本 `next` 从「百佬」切到「倍佬」——**同一个 host，等于没切**。

**问题 2**：k40 渠道用裸 curl 被拒（`No available accounts: this group only allows Claude Code clients`，网关卡客户端身份）；在 Git Bash 里 `claude -p` 冒烟挂死（可能是 Git Bash + timeout 的测试环境假象，不代表真实 CLI 坏）。A2A wrapper 是 cmd /c 起 claude CLI 的，历史上打到过 sub.100xlabs 的真实 API 错误，说明 CLI 链路本身能工作。

## 任务

1. **`cc_rotate_claude_provider.py` 加 host 感知轮换**：
   - `next` 改为优先切到**不同 base_url host** 的渠道（同 host 不算切换）；全是同 host 时退化为原行为。
   - 保持现有 list/current/switch 命令兼容，不破坏 cc-switch GUI（DB 只读 + 写 `~/.claude/settings.json` env + Windows User env）。
2. **加 `probe` 子命令**：逐渠道做轻量探测（短超时 /v1/messages 最小 payload），输出 `名称 | host | http_code | 耗时 | healthy`；**任何输出不得打印完整 token**（最多尾部 6 位）。k40 渠道被网关拒（非 2xx 的确定性 4xx）要标注 `client-restricted` 而不是简单当 down。
3. **实测 k40 真实可用性**：用 cmd /c（不用 Git Bash、不用 timeout 包裹）起 `claude -p "say OK" --max-turns 1`，工作目录用中性目录（如 `C:/Users/zhugu`，避开项目 .mcp.json）。若 k40 实测可用，把当前渠道切到一个 k40 渠道，并在结果里写明；若不可用，保持 sub.100xlabs 渠道并写明原因。
4. 若命令语法有变化，同步更新计划任务用的 `scripts/_run_cc_rotate.cmd`。

## gates

```gates
python -m py_compile scripts/cc_rotate_claude_provider.py
python scripts/cc_rotate_claude_provider.py list
python scripts/cc_rotate_claude_provider.py current
python scripts/cc_rotate_claude_provider.py probe
（当前为 sub.100xlabs 时）python scripts/cc_rotate_claude_provider.py next → 必须落到 k40 host
切换后 python -c "import json; json.load(open(r'C:/Users/zhugu/.claude/settings.json'))" 合法
上述命令输出无完整 token 泄漏
```

## 注意

- 不要动 cc-switch DB 的写路径（GUI 领地），只读。
- 不要重启 A2A 桥 / wrapper（Kimi 终审统一做）。
- 输出写到 `_review/cc_channel_hostrotate_result.md`。
