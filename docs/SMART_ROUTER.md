# Smart Router 架构与回滚手册

> 更新：2026-07-16  
> 范围：Task 6 落地后的 Smart Router 层文档与紧急回滚 playbook。

## 架构一句话

CLIProxy Grok 池监听内部端口 8318；Smart Router 监听对外公共端口 8317，按实时评分挑选上游（本地 CLIProxy + remote channels）。Codex/Claude/GLM 仍由 CLIProxy 在公共端口 8327/8337/8347 直服。

## 端口映射

| 服务 | 公共客户端端口 | CLIProxy 内部端口 | 说明 |
|------|---------------|-------------------|------|
| Grok  | 8317（Smart Router） | 8318 | **已接入 Smart Router**；本地 CLIProxy + remote channels |
| Codex | 8327（CLIProxy 直服） | — | 维持原 CLIProxy 路径 |
| Claude| 8337（CLIProxy 直服） | — | 维持原 CLIProxy 路径 |
| GLM   | 8347（CLIProxy 直服） | — | 维持原 CLIProxy 路径 |

**第一阶段限制**：只有 Grok 经过 Smart Router 的动态评分路径；Codex / Claude / GLM 维持原 CLIProxy 路径，避免一次性改动影响多个模型池。后续任务再逐池迁移。

## 启动 / 停止

### 启动整个舰队 + Smart Router（单次）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/cliproxy_fleet_watchdog.ps1 -Once
```

### 安装为登录后常驻计划任务

```powershell
powershell -ExecutionPolicy Bypass -File scripts/cliproxy_fleet_watchdog.ps1 -Install
```

### 停止 Smart Router

```powershell
Stop-Process -Name python -Force
```

> ⚠️ 仅在当前系统没有其他重要 Python 进程时执行；否则会误杀。更安全的方式是用 `Get-Process python` 确认 PID 后再 `Stop-Process -Id <PID>`。

## 日志位置

| 路径 | 用途 | 关键字段 |
|------|------|----------|
| `logs/smart_router.json` | Smart Router `/router/status` 快照 | 上游 alias、实时评分 `score`、EWMA 延迟、最近探测结果、生效权重 |
| `D:/cli-proxy-api/logs/fleet_watchdog.log` | CLIProxy 舰队 watchdog 重启日志 | 实例启动/重启时间、退出码、CLIProxy 配置加载结果 |

> 密钥和 access token **不会**出现在上述日志中。若发现泄露，立即按下方回滚 playbook 处理。

## 回滚 Playbook

当 Smart Router 导致客户端异常、评分抖动或需要快速还原到纯 CLIProxy 路径时执行：

```powershell
# 1. 停止 Smart Router（确认无其他关键 python 进程）
Stop-Process -Name python -Force

# 2. 还原 CLIProxy 配置
Copy-Item D:\cli-proxy-api\config.yaml.before-router D:\cli-proxy-api\config.yaml -Force

# 3. 重新启动 CLIProxy 舰队
powershell -ExecutionPolicy Bypass -File scripts/cliproxy_fleet_watchdog.ps1 -Once

# 4. 验证 Grok 池恢复直连
#    此时 Grok CLIProxy 再次监听公共端口 8317
curl http://127.0.0.1:8317/v1/models
```

回滚后 Smart Router 不再拦截 8317，Grok 客户端直接访问 CLIProxy。Codex/Claude/GLM 端口未变动。

## 监控与验证

### 查看 Smart Router 健康与上游评分

```bash
curl http://127.0.0.1:8317/router/status
```

### 绕过 Smart Router，直接探测 CLIProxy 内部端口

```bash
curl http://127.0.0.1:8318/v1/models
```

### 端到端 Grok 验证

```bash
curl http://127.0.0.1:8317/v1/models
```

## v1.1 限流与熔断（2026-07-17）

社区公益站（如 chuanapi）容易被高并发顶挂。v1.1 在 Smart Router 内落地：

| 机制 | 行为 |
|------|------|
| **per-upstream inflight** | 本地 CLIProxy `max_inflight=16`；远端公益站默认 `3`。满了选次优源。 |
| **chat TTFT 探测** | 探测改用 `POST /v1/chat/completions`（`max_tokens=1`），不再只看 `/models`。 |
| **硬熔断 401/403** | 冷却 **6 小时**，冷却期内**跳过探测**（省流量）。 |
| **软熔断 429/5xx** | 连续失败 >3 次后冷却 30s，指数退避至最多 10 分钟；到期 half-open 试探 1 次。 |
| **负载惩罚** | score 随 `inflight/max_inflight` 下降，流量自动摊开。 |
| **proxy_url 路径一致** | 远端 channel 的 `proxy-url`（通常 Clash `:7897`）在**探测与转发**共用；`local-cliproxy` 直连。避免「探通、请求区 403」。 |

状态字段见 `GET http://127.0.0.1:8317/router/status`：
`inflight` / `max_inflight` / `open_until` / `cooldown_remaining_sec` / `half_open`。

### 号池测活

```powershell
# 全量 enabled（CLIProxy 会用的号）AT 测活 + 软禁用坏号
python scripts/probe_import_batch.py --enabled-only --workers 24 --apply
```

报告：`logs/probe_import_batch.json`。只测 AT，不刷 RT；401/perm_denied 软摘，network 不误杀。

## 安全与约束

- 任何密钥、token 或 bearer 均**不得**硬编码在本文档或 Smart Router 代码中，也不得打印到日志。
- 公共客户端端口保持 **8317 / 8327 / 8337 / 8347** 不变。
- 所有变更通过 `.before-router` 配置备份可回滚；回滚前建议先保存 `logs/smart_router.json` 以便事后复盘。
- 本 playbook 针对 Windows 环境；命令使用 PowerShell / Git Bash 可用语法。
