# 社区方案 → 本机加厚对照

参考：站内 grok 注册机血缘（AaronL725 / maxucheng0 / grok--main 协议版）、CLIProxy 号池运维实践。

## 已对齐（你这边已有或本轮已合）

| 社区能力 | 本机 |
|----------|------|
| 有头注册 + CF 邮箱 | `grok_register_ttk.py` auto |
| 协议优先 CPA mint | `cpa_prefer_protocol` + `authcode` fallback |
| 异步 mint | `cpa_mint_async` |
| Clash 出口 / 注册专用组 | `clash_proxy` + config |
| 号池换号 / 额度 | `quota_watch` |
| 死号 vs 额度冷却 | `hard_purge` + `rescue_quota_holds` |
| 导入抽检熔断 + 只收存活 | `import_cpa_with_probe --refresh-all` |
| 水位只计自有 | `pool_watermark_own_only` |
| Turnstile 补丁 | `turnstilePatch/script.js`（screenXY + webdriver 合并） |
| Chromium 轻量 flag | `chromium_mute_audio` 默认；`chromium_slim` 可选 |
| TabPool / 多线程 CLI | `tab_pool.py` + `register_cli.py`（可选，不改默认 auto） |
| 缓冲抽检 | `scripts/buffer_health_sample.py` |

## 社区有、仍可选（未默认打开）

| 项 | 原因 | 何时开 |
|----|------|--------|
| `register_cli.py --threads N` | 多浏览器吃内存/代理 | 自有水位低且代理稳 |
| `chromium_slim: true` | 可能影响页面脚本 | 内存紧时试 |
| Hotmail XOAUTH2 | 要凭证池 | 有 Outlook 再上 |
| HTTP 代理池文件 | 与 Clash 注册组二选一为主 | 节点池更稳时 |
| 无头注册 | CF 常拦 | 协议+打码足够时再碰 |

## 推荐日常命令

```bat
python ops_heartbeat.py
python pool_status.py
python scripts/buffer_health_sample.py --sample 30
python scripts/import_cpa_with_probe.py D:\Downloads\pack.zip
python scripts/hard_purge_pool.py --scope buffer --max 500
python register_cli.py --help
```

## 原则

1. **缓冲当弹药，自有当基本盘**  
2. **共享包必 probe，禁止盲导**  
3. **加吞吐先稳代理，再加线程**  
4. **不覆盖本机 ops 去追 upstream 全文**
