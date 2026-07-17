# 批量探测与清理工具

## 问题背景

当号池规模达到数百个账号时，会出现以下问题：

1. **隐藏的死号**：大量账号已经 429 exhausted，但仍然标记为"未 disabled"
2. **quota_watch 被动探测**：只有当 grok CLI 实际使用账号时才会发现死号
3. **轮询浪费**：grok CLI 会轮询到大量死号，增加延迟和失败率

## 解决方案

使用 `batch_probe_accounts.py` 主动批量探测所有账号，将死号标记为 disabled。

## 使用方法

### 基础用法

```bash
cd D:/Users/grok-auto-register
python batch_probe_accounts.py
```

### 预期输出

```
总账号数: 648

进度: 50/648 | OK=0 429=10 401=0 其他=0 错误=0
进度: 100/648 | OK=1 429=28 401=0 其他=0 错误=0
...

=== 探测完成 ===
可用:   52 (8%)
429:    209 (32%)
401:    0
其他:   0
错误:   0
已跳过: 387

待禁用账号数: 209

开始标记死号为 disabled...
已将 209 个账号标记为 disabled

最终可用账号数: 52
```

## 工作原理

1. **扫描所有账号**：遍历 `cpa_auths/xai-*.json`
2. **探测可用性**：向每个账号发送最小 test 请求（1 token）
3. **分类结果**：
   - **200 OK** → 可用
   - **429** → quota exhausted → 标记 disabled
   - **401** → 认证失败 → 标记 disabled
   - **其他错误** → 标记 disabled
4. **更新文件**：将死号的 JSON 文件标记为 `"disabled": true`

## 探测成本

- **单账号耗时**：约 0.5-1 秒
- **648 账号总耗时**：约 5-10 分钟
- **Quota 消耗**：每个可用账号消耗 1 token（忽略不计）

## 建议使用场景

### 1. 定期清理（推荐）

每 24-48 小时运行一次，清理到期的死号：

```powershell
# 添加到计划任务
schtasks /create /tn "GrokBatchProbe" /tr "python D:\Users\grok-auto-register\batch_probe_accounts.py" /sc daily /st 03:00
```

### 2. 导入外部 CPA 包后

从社区导入大量 CPA 包后，立即探测剔除死号：

```bash
python import_cpa_batch.py D:/Downloads/cpa_pack.zip
python batch_probe_accounts.py
```

### 3. 发现可用率异常低时

当 grok CLI 频繁报 429 时，手动运行清理：

```bash
python batch_probe_accounts.py
```

## 与其他工具的配合

| 工具 | 作用 | 配合方式 |
|------|------|---------|
| `pool_maintain.py` | 健康检查 + 自动补号 | batch_probe 清理后，pool_maintain 会发现可用账号不足，自动补号 |
| `quota_watch` | 运行时换号 | batch_probe 清理后，quota_watch 只会轮询真正可用的账号 |
| `sync_cli_live.py` | 同步到 CLI | batch_probe 清理后，运行此脚本将健康账号同步到 `cli_live/` |

## 配置建议

### 启用轻量探活

在 `config.json` 中启用 `quota_watch_sample_probe_n`：

```json
{
  "quota_watch_sample_probe_n": 5,
  "quota_watch_sample_probe_interval_sec": 300
}
```

这样 quota_watch 会每 5 分钟随机抽 5 个账号探测，主动发现死号。

### 探测端点选择

- **`/chat/completions`**（当前）：真实模拟使用场景，但消耗 1 token
- **`/models`**（未来优化）：0 quota 消耗，但部分死号可能漏检

## 限制

1. **不支持并发**：顺序探测，避免触发 rate limit
2. **超时固定 8 秒**：部分慢响应可能被误判为 error
3. **不恢复死号**：只标记 disabled，不会取消 disabled

## 故障排除

### 探测速度太慢

可以临时降低超时：

```python
# batch_probe_accounts.py:34
resp = requests.post(url, json=payload, headers=headers, timeout=5)  # 改为 5 秒
```

### 误判可用账号为死号

检查网络代理是否正常：

```bash
python proxy_health.py
```

### 死号没有被标记

检查 JSON 文件权限，确保可写。

## 总结

`batch_probe_accounts.py` 是号池维护的核心工具，建议：

1. **首次运行**：清理存量死号
2. **定期运行**：每 24-48 小时清理一次
3. **配合 pool_maintain**：清理后自动触发补号
4. **启用轻量探活**：让 quota_watch 主动发现死号

---

**最后更新**: 2026-07-18  
**相关文档**: [POOL.md](../POOL.md), [docs/HARDEN.md](HARDEN.md)
