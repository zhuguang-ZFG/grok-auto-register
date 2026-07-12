# AtomCode 二次审核报告

**审核时间**: 2026-07-12  
**审核范围**: quota_watch.py / _refresh_token.py / cpa_xai/mint.py / refresh_pool.py / clash_proxy.py / cpa_xai/authcode_mint.py / pool_health.py / tests/test_authcode_and_purge.py  
**审核模式**: 只读源码 + 测试，不运行注册、不操作号池、不改 config

## Handoff after Atom (same day)

Atom 的 **P1 recover_after 格式不一致** 已本地修复并加测：

| Atom finding | Action |
|--------------|--------|
| P1 ISO `recover_after` vs `float()` | `pool_health.soft_disable` → Unix float; `usage.parse_recover_after` 兼容 ISO+float；mark/is/reenable/recover_in_sec 全走 parser |
| P2 Windows `os.replace` lock | `quota_watch.atomic_write_json` / `refresh_pool.atomic_write` / `usage._atomic_write_json` / `pool_health.soft_disable` → 最多 3 次重试 |
| P2 Location consent test | `test_submit_consent_location_header_fallback` |
| Info `recover_in_sec` missing | **反驳**：`usage.recover_in_sec` 存在；Atom 行号可能指旧路径 |

Verify: `pytest tests/test_authcode_and_purge.py -q` → **15 passed**.

未推 GitHub；`_review/` 默认本地。

---

## 1. Executive Summary

**结论：可上线，但有一个 P1 残留风险 + 两个 P2 需要修复后才能上线。**

| 维度 | 评级 |
|------|------|
| 核心逻辑正确性 | ✅ 合格 |
| 并发安全 | ⚠️ 有隐患（见 4.2） |
| 测试覆盖 | ⚠️ 有缺口（见 4.3） |
| Windows 兼容 | ⚠️ 有隐患（见 4.1） |
| 数据格式一致性 | ❌ P1（见 2.1） |

Reasonix 修复的 4 个 P1/P2 加上新落地的 5 个 P2 加固项，**代码逻辑本身是正确的**。但新发现一个**跨模块 `recover_after` 格式不一致**的 P1，以及若干 Windows 平台和测试覆盖缺口需要修复。

---

## 2. Findings 表

| Sev | Location | Issue | Why | Fix |
|-----|----------|-------|-----|-----|
| **P1** | `pool_health.py:250-255` vs `usage.py:208` | `recover_after` 格式不一致：`pool_health.py` 的 `soft_disable()` 写入 ISO 8601 字符串（`"2026-07-12T..."`），但 `usage.py` 的 `reenable_recovered_accounts()` 用 `float()` 解析 | 若 `pool_health.py` 先 soft-disable 了一个账号，`quota_watch.py` 主循环调用 `reenable_recovered_accounts()` 时会在 L208 抛出 `ValueError`，导致该文件被跳过（`continue`），但异常被 `except Exception` 吞掉（L199-202 的 `try/except` 捕获的是 `read_text` 异常，**不是** `float()` 转换异常）。`float("2026-07-12T...")` 会崩溃到 `reenable_recovered_accounts` 的顶层调用者（`quota_watch.py once()` L1537-1539），**若没有外层 try/except 则整个 once() 中断**。 | 统一 `recover_after` 为 Unix 时间戳（`float`），或让 `reenable_recovered_accounts` 兼容两种格式（`float()` 失败则 fallback 解析 ISO）。 |
| **P2** | `quota_watch.py:177-185` `atomic_write_json` + `refresh_pool.py:50-60` `atomic_write` | Windows 上 `os.replace()` 在目标文件被 CLIProxy 打开时抛出 `PermissionError`，被 bare `except Exception` 吞掉，写操作静默丢失 | CLIProxy 的 auth-dir 文件监视器可能在任何时刻拥有文件句柄。`os.replace` 在 Windows 上不等同于 POSIX 的原子 rename——如果目标文件已打开，直接失败。当前代码把 `atomic_write_json` 包在 `try/except Exception` 里，写入失败不报错，导致 refresh 成功但文件未更新，下一轮继续刷新。 | 在 `except Exception` 分支至少 log 一个 WARNING；或重试 1-2 次（`time.sleep(0.1)`）。 |
| **P2** | `tests/test_authcode_and_purge.py` | 缺少 4 个关键路径的测试覆盖 | (1) `purge_dead_pool` 的 `spec_from_file_location` 回退路径（L550-560）无测试；(2) `_submit_consent` 的 `Location` header 回退路径（L322-324, L338-340）无测试；(3) TLS retry 后 `cpa_xai/mint.py` 的 `new_proxy=eg.get("proxy")` 路径无测试；(4) 跨模块 `recover_after` 格式一致性无测试。 | 见 §5。 |
| **Info** | `_refresh_token.py:23-24` | 刷新令牌通过 CLI argv 传递，Windows 下 `wmic process` 可读 | 非漏洞——当前威胁模型为本地单用户，但值得一提的是：若服务器被入侵，`ps` 或 `Get-WmiObject Win32_Process` 可看到明文 RT。 | 可改为 `stdin` 传递，或确认当前威胁模型可接受。 |
| **Info** | `quota_watch.py:781-783` | `try_rotate_from_pool` 中 `from cpa_xai.usage import is_account_recovered, recover_in_sec` 是 module-level import 失败后的延迟加载，但 `recover_in_sec` 这个函数在 `usage.py` 中不存在（应为 `recover_in_sec()` 返回值） | 代码审查中发现 L783 引用 `recover_in_sec` 作为函数导入，但 `usage.py` 中实际是 `_recover_window_sec()` 函数。**不过这行在所有 `try_rotate_from_pool` 调用路径上是否真的执行到？** | 确认 `recover_in_sec` 在 `usage.py` 中是否存在；若不存在则修复 import 或删除无用引用。 |
| **Info** | `quota_watch.py:566` `subprocess` fallback | `_sp.run(cmd, capture_output=True, text=True, timeout=30)` 只捕获 stdout，stderr 默认输出到终端 | 子进程崩溃时的 Python traceback 会出现在终端，可能泄露 refresh_token 或 access_token 的部分信息。 | 添加 `stderr=subprocess.PIPE` 并在失败时 log stderr 前 200 字符。 |

---

## 3. 对已修 P1/P2 的确认或反驳

### Reasonix 修复项逐一确认

| 原 Issue | 状态 | 理由 |
|----------|------|------|
| P1: `OAuthDeviceError = Exception` 在 subprocess fallback 中 | ✅ **已修复** | `quota_watch.py:594` `_terminal_exc = (DeadRefreshError,)` — 子进程 fallback 路径中 `_terminal_exc` **不包含** `Exception`，只包含 `DeadRefreshError`。`_refresh_token.py:40-41` 只在 `OAuthDeviceError` 时设置 `"dead": true`。 |
| P1: mint TLS retry proxy pin 错误 | ✅ **已修复** | `cpa_xai/mint.py:144-146` 使用 `new_proxy = eg.get("proxy")` 局部变量，不再覆盖全局 `resolved` 前先赋值。 |
| P2: purge scan order 任意 | ✅ **已修复** | `quota_watch.py:610-624` 用 `_access_token_exp` 排序，按 JWT exp 升序处理。 |
| P2: `refresh_pool.atomic_write` 无 fsync | ✅ **已修复** | `refresh_pool.py:55-59` 包含 `fh.flush()` + `os.fsync(fh.fileno())` + `os.replace(tmp, path)`。 |

### 新落地 P2 项逐一确认

| 新功能 | 状态 | 理由 |
|--------|------|------|
| `clash_proxy.rotate_node` mass re-enable 优先 `success>0` + WARNING | ✅ **正确** | `clash_proxy.py:310-331` — 优先选 `success>0` 的节点，其次 WARNING 日志全量恢复。 |
| `authcode_mint._session` proxies={} 当 proxy=None | ✅ **正确** | `authcode_mint.py:140-141` — `s.proxies = {}` 清除旧 pin。`test_session_clears_proxy_when_none` 已覆盖。 |
| `pool_health.probe_access` curl_cffi 优先 + urllib fallback | ✅ **正确** | `pool_health.py:82-109` — 优先 `impersonate="chrome"`，401/403/400 直接返回不 fallback，429 视为 alive。 |
| `_refresh_token.py` stdout JSON schema (`ok`/`dead`) | ✅ **正确** | `_refresh_token.py:33-43` — `OAuthDeviceError` → `dead=true`；其他异常 → 无 `dead` 字段。 |
| `purge_dead_pool` 按 JWT exp 排序 | ✅ **正确** | `quota_watch.py:610-624` — 见上。 |

---

## 4. 漏测 / 并发 / Windows 路径风险

### 4.1 Windows 平台风险：`os.replace` 对打开文件的失败

**严重程度**: P2  
**影响文件**: `quota_watch.py:185` `atomic_write_json`、`refresh_pool.py:60` `atomic_write`、`pool_health.py:263` `soft_disable`、`cpa_xai/usage.py:58` `_atomic_write_json`  

整个代码库有 **12 处** `os.replace(tmp, path)` 写模式。在 Windows 上，如果目标文件被 CLIProxy 的 `file watcher`（或任何其他进程）打开，`os.replace` 会抛出 `PermissionError`。当前代码使用 `try/except Exception` 吞掉错误：

```python
# quota_watch.py:647-651
try:
    atomic_write_json(p, payload)
    stats["purged"] += 1
except Exception:
    stats["errors"] += 1
```

**影响**: refresh 成功的 token 写不回文件，下一轮继续尝试刷新，浪费配额。软 disable 标记也可能写不进去。

**缓解**: 当前代码已把 stats 计为 `errors`，因此不会静默丢失——但 `errors` 的日志会被 `purge_dead_pool` 和 `silent_refresh_pool` 记录，运维可以注意到。不过 `pool_health.py` 的 `soft_disable` 和 `quarantine` 没有返回值的调用者不会知道写入失败。

### 4.2 并发风险：`purge_dead_pool` 与 `silent_refresh_pool` 同时写同一文件

**严重程度**: P2—Info  
**影响文件**: 所有 CPA 文件 (xai-*.json)

`purge_dead_pool`（由 `quota_watch.py` 主循环调用）和 `silent_refresh_pool`（由 `refresh_pool.py` 独立调用）都可能对同一 CPA 文件执行 `read-modify-write`。两个线程/进程的 `read → modify → write` 序列存在丢失写覆盖的风险：

1. T1 读文件 A（access_token=A1, refresh_token=R1）
2. T2 读文件 A（access_token=A1, refresh_token=R1）
3. T1 刷新成功，写回（access_token=A2, refresh_token=R2）
4. T2 刷新成功，写回（access_token=A3, refresh_token=R3）—— **覆盖了 T1 的写入，但 T2 的 `last_refresh` 时间戳是新的**

**实际影响很小**：因为 refresh 操作是幂等的——最终的 token 依然有效，只是 T1 的刷新被浪费了。但 `quota_state` 字段可能被覆盖丢失（如 `limited` 标记）。

**缓解**: 当前 `quota_watch.py` 主循环中 `purge_dead_pool` 和 `silent_refresh_pool` 一般不会同时启用（一个用于过期扫描，一个用于预刷新）。但若都启用，建议加文件锁或使用 `os.replace` 的原子性 + 乐观锁。

### 4.3 测试覆盖缺口

| 缺口 | 文件 | 建议 |
|------|------|------|
| `spec_from_file_location` 回退路径 | `quota_watch.py:550-560` | Mock 掉 `cpa_xai.oauth_device` import 失败，测试 subprocess 回退逻辑 |
| `_submit_consent` `Location` header 回退 | `authcode_mint.py:322-324, 338-340` | 构造 `Location` header 含 `code=` 的响应 |
| `mint.py` TLS retry + `new_proxy` 路径 | `mint.py:140-152` | Mock `rotate_mint_egress` 返回 `{"proxy": "http://..."}` 和 `{"ok": true}` 两种 |
| `recover_after` 格式一致性 | `pool_health.py:250` / `usage.py:208` | 写入 ISO 格式后调用 `reenable_recovered_accounts` 验证不崩溃 |
| Windows `os.replace` 失败重试 | `quota_watch.py:185` | Mock 掉 `os.replace` 第一次抛出 `PermissionError`，验证重试/log |

---

## 5. Recommended Next Fixes（有序）

### 优先级 1（必须修复才能上线）

**P1 - `recover_after` 格式统一**

两个方案选一，推荐方案 A：

**方案 A（推荐）**: 修改 `pool_health.py` 的 `soft_disable()`，将 `recover_after` 改为 Unix 时间戳：
```python
# pool_health.py:250-255 的修改
data["quota_state"] = {
    "reason": "probe_or_refresh_fail",
    "detail": str(reason)[:300],
    "recover_after": time.time() + hours * 3600,  # 改为 Unix 时间戳
    "marked_at": time.time(),  # 也改为 Unix 时间戳
}
```

**方案 B**: 修改 `usage.py` 的 `reenable_recovered_accounts()` 兼容 ISO 格式：
```python
raw = qs.get("recover_after") or 0
try:
    recover_after = float(raw)
except (ValueError, TypeError):
    try:
        from datetime import datetime
        recover_after = datetime.fromisoformat(raw).timestamp()
    except Exception:
        recover_after = 0
```

### 优先级 2（建议上线前修复）

**P2 - Windows `os.replace` 失败重试**

修改 `quota_watch.py:177-185` `atomic_write_json` 和 `refresh_pool.py:50-60` `atomic_write`，在 `os.replace` 失败时重试 1 次 + 加 WARNING 日志：

```python
try:
    os.replace(tmp, path)
except OSError:
    import time
    time.sleep(0.1)
    os.replace(tmp, path)  # 再试一次，若仍失败向上抛
```

**P2 - 增加测试覆盖**

建议新增 4 个测试用例：
1. `test_purge_import_fallback_subprocess` — Mock 掉 `cpa_xai.oauth_device` 导入失败
2. `test_submit_consent_location_header` — 构造带 `Location: ...code=xxx` 的响应
3. `test_recover_after_format_iso` — 写入 ISO 格式后调用 `reenable_recovered_accounts`
4. `test_mint_tls_retry_new_proxy` — Mock `rotate_mint_egress` 验证 proxy 切换

### 优先级 3（上线后加固）

**Info - subprocess 泄漏缓解**

`quota_watch.py:566` 添加 `stderr=subprocess.PIPE`：

```python
proc = _sp.run(cmd, capture_output=True, text=True, timeout=30, stderr=subprocess.PIPE)
```

---

## 附录：行号索引（对应当前磁盘文件）

| 符号 | 文件 | 行号 |
|------|------|------|
| `DeadRefreshError` | quota_watch.py | 500-505 |
| `purge_dead_pool` | quota_watch.py | 528-696 |
| `_terminal_exc` (subprocess fallback) | quota_watch.py | 594 |
| `atomic_write_json` | quota_watch.py | 177-185 |
| `atomic_write` | refresh_pool.py | 50-60 |
| `_session` (proxies={}) | cpa_xai/authcode_mint.py | 131-142 |
| `_submit_consent` | cpa_xai/authcode_mint.py | 253-341 |
| `mint_and_export` (TLS retry) | cpa_xai/mint.py | 140-152, 162-172, 215-226 |
| `rotate_node` (mass re-enable) | clash_proxy.py | 300-335 |
| `probe_access` (curl_cffi + urllib) | pool_health.py | 65-133 |
| `soft_disable` (ISO format) | pool_health.py | 238-263 |
| `reenable_recovered_accounts` (float parse) | cpa_xai/usage.py | 181-225 |
| `_refresh_token.py` main | _refresh_token.py | 18-47 |
| 测试用例 11 个 | tests/test_authcode_and_purge.py | 19-309 |