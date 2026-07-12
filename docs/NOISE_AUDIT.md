# 注册伪装 / 降噪只读审计

日期：2026-07-12  
范围：代理轮换、CPA 铸造路径、进程是否吃到新代码。  
**未改运行策略，未提速冲量。**

## 1. 每号换代理 — 代码在，日志有

| 项 | 证据 |
|----|------|
| 入口 | `grok_register_ttk._register_one_account` 每号先 `rotate_egress_proxy()` |
| 配置 | `clash_rotate_per_account=true`；`http_proxy_prefer_over_clash=false` → **先 Clash** |
| 成功日志 | `register_auto.out.log` 尾部可见「出口节点 / rotate」多次 |
| HTTP 列表 | 当前路径几乎未用（Clash 优先且可用时不走 `all_proxies`） |

结论：**注册出口按号轮换在工作。**  
缺口：铸造（mint）线程/浏览器 **不保证** 再换一次节点；常复用 mint 浏览器 + 当前系统代理。

## 2. 为何日志仍是浏览器 Allow，不是协议铸造

代码侧（磁盘已具备）：

- `cpa_xai/protocol_mint.py` + `mint.py` `prefer_protocol=True`
- `cpa_export` 传 `sso=`
- `cpa_mint_pool` 传 `sso=job.sso`、`page=None`

运行侧：

| 项 | 值 |
|----|-----|
| 注册进程 | `python -u grok_register_ttk.py auto` **PID 44592** |
| 进程启动 | **2026-07-12 10:33:54** |
| 协议铸造落地 | **~11:05**（文件 mtime） |
| 日志特征 | `mint browser reused` / `injected cookies` / `clicked REAL exact 'Allow'` |
| 日志中 `protocol mint` | **0 次** |

结论：**不是协议铸造写坏了，是长驻注册进程仍跑旧模块（未重启）。**  
手工冒烟（新进程 + SSO）已验证协议路径约 4s、含 grok-4.5。

`MintPool.ensure_started` 只在进程内启动一次 worker；**重启整个 `grok_register_ttk.py auto` 后**才会加载新 `mint_and_export`。

## 3. 当前噪声画像（非“已被针对”）

- 域健康高，单轮注册成功率高 → 不象全站 ban 画像  
- 失败样本含 **TLS/curl 35** → 代理链路毛刺  
- mint 历史 fail 比例存在 → 铸造慢/浏览器路径成本高  
- 缓冲域批量导入 + 自有域持续补号 → **账号图谱仍可关联**（与 UA 伪装无关）

## 4. 最小降噪清单（按优先级）

### P0 — 立刻（不改策略）

1. **重启注册机**（停 `grok_register_ttk.py auto` 再起），让协议铸造生效。  
2. 重启后看日志应出现：`prefer_protocol=True sso=yes` → `protocol mint ok`；不应再默认 `clicked Allow`。  
3. 保持 **并发 1–2**、`buffer_first`、UA 池关闭。

### P1 — 低成本（已部分落地）

4. 代理 TLS 报错多时：修 Clash 节点质量 / 降并发，而不是加指纹。  
5. 确认 mint 日志带 `mint_method=protocol`（或 `protocol mint ok` / `mint done method=`）。  
6. **已做**：请求级 TLS 短重试 + 整次协议再试（`cpa_protocol_attempts`，默认 2）。  
7. **已做**：mint 队列 delay 加 0–1.5s 抖动，避免双 worker 齐打 OIDC。  
8. 可选：`cpa_protocol_only=true` 做一轮观察（协议失败不弹铸造浏览器）— 仅调试用。  
9. 可选：`clash_verify_ip=true`（换节点后验 IP，更慢更稳）。  
10. 可选：铸造前再 `rotate_egress_proxy` 一次（代码未接；协议失败率高再做）。

### P2 — 刻意不做（除非你要求）

7. 拉高并发冲 2000 自有域  
8. 打开 `anti_detect_ua_pool` 乱换 UA  
9. 主路径改 TempMail 公域  
10. headless 注册幻想  

## 5. 验收命令

```bat
:: 重启 auto 注册后
findstr /i "protocol mint ok mint start prefer_protocol clicked REAL" logs\register_auto.out.log
python pool_status.py
```

期望：新成功号以 `protocol mint ok` 为主；`Allow` 仅作回退偶发。
