# 运行状态快照

> 记录时间：2026-07-12 19:32（本机 Asia/Shanghai 墙钟）  
> 仓库：`zhuguang-ZFG/grok-auto-register`  
> 不含密钥 / 号池 JSON / 订阅 token。

## 1. 进程与入口

| 组件 | 状态 | 备注 |
|------|------|------|
| CLIProxyAPI | 运行中 `:8317` | **7.2.67** (`2075f77c`, Built 2026-07-11) |
| 注册机 `grok_register_ttk.py auto` | 运行中 | 隐藏窗口 / 1 并发 |
| `quota_watch` | 运行中 | soft-disable + 静默 refresh |
| Kimi CLI | `local-cpa/grok-4.5` → `http://127.0.0.1:8317/v1` | 已剔除失效中转模型 |

路由：

```text
strategy=round-robin
session-affinity=true
session-affinity-ttl=4h
profile=cache
```

## 2. 号池水位（约 19:31）

| 指标 | 数值 |
|------|------|
| CPA 文件 | ~4019 |
| access 未过期（粗判） | ~3884 |
| disabled | ~348 |
| 自有域文件 | ~726 / 目标 2000（~36.3%） |
| 缓冲域文件 | ~3293 |
| 策略 | `pool_prefer_mode=own_first` |

自有域名（`defaultDomains`）：

- `zhuguang.ccwu.cc`
- `lima.cc.cd`
- `zhuguang.de5.net`
- `baoxia.top`

域名健康（注册成功率粗算）：四域均 **>0.93**。

## 3. 铸造 / 补号

| 项 | 值 |
|----|-----|
| 并发 | **1** |
| 每批 | **4** |
| 轮间休息 | ~180s |
| 铸造顺序 | Device → **Authcode PKCE** → Browser |
| 近窗铸造 | protocol_ok≈92 / fail≈3；**authcode_ok≈4** / fail=0；browser≈0 |
| 铸造后 probe | **关** |
| mint 默认换出口 | **关**（仅 TLS 失败时 rotate） |

## 4. Clash 出口隔离（本机已落地）

| 项 | 状态 |
|----|------|
| 模式 | **rule** |
| 专用组 | **`注册专用`**（约 20 真实节点） |
| 规则 | `x.ai` / `auth.x.ai` / `accounts.x.ai` / `grok.com` / `cli-chat-proxy` → 注册专用 |
| 验证 | 注册专用换节点时，`悍刀行` / `GLOBAL` 可保持不变 |
| 项目配置 | `clash_selector=注册专用`，`clash_force_global=false`，`clash_close_conns=false`，`clash_rotate_every_n=5` |

增强写入位置（Clash Verge Rev，悍刀行 profile）：

- groups: `profiles/gjI9d9XqFzUo.yaml`
- rules: `profiles/rqnAW40JSaDd.yaml`
- merge: `profiles/muvS3ZaQD9v6.yaml`
- 运行时曾 patch：`clash-verge.yaml`（订阅更新可能覆盖，见 [CLASH_ISOLATE.md](CLASH_ISOLATE.md)）

## 5. 稳定性约定（代码已合入）

详见 [HARDEN.md](HARDEN.md)：

- soft-disable，禁止 live 号池硬删风暴  
- 恢复窗口默认 6h 滚动  
- 终端 `refresh_revoked` purge 跳过  
- 静默 JWT refresh（`quota_watch_pool_refresh_*`）  
- pool_status 输出 sticky reselect / REMOVE / authcode 计数  

近窗 sticky tail 示例（波动正常，额度耗尽会 reselect）：

```text
hit≈53 reselect≈28 rate≈35% REMOVE≈115
```

## 6. 近期运维动作（已执行）

1. 导入社区 CPA 包（第一弹 1000 + 第二弹 2000）→ 缓冲水位拉高  
2. 静默 refresh 一批临期号（例：40 候选 / 28 成功）  
3. 本机性能：1 并发、降频 poll、清理多余调试 Chrome  
4. Kimi `config.toml`：删除 klsf / 未登录 kimi-code 别名 / venlacy / voya；默认 `local-cpa/grok-4.5`  
5. GitHub 已推：authcode fallback、HARDEN、sticky 指标、测试  

## 7. 未入库（有意）

- `config.json`、`cpa_auths/`、代理列表、邮箱密钥  
- Clash 订阅 URL / secret  
- 本机绝对路径运行时状态  

## 8. 建议后续

- 继续 1 并发补自有域至 2000  
- 订阅更新后确认「注册专用」仍在；消失则按 CLASH_ISOLATE 重载增强  
- CLIProxy 跟官方 7.2.x 小版本  
- 观察 sticky reselect 是否随 soft-disable 继续下降  

## 9. 一键自检

```bash
python pool_status.py
python set_cliproxy_routing.py status
python proxy_health.py
```
