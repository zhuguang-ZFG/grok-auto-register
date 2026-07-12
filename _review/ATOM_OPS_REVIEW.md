# AtomCode 代码审查：Ops Heartbeat + 关联修改

**审查日期**：2026-07-12  
**审查范围**：磁盘当前版本（未提交）  
**已本地验证**：`pytest tests/test_ops_heartbeat.py tests/test_authcode_and_purge.py` → 20 passed  

---

## 执行摘要

整体质量良好。`ops_heartbeat.py` 结构清晰（纯函数 + I/O 分离），`proc_rows` 注入使测试可 mock 掉 PowerShell，测试覆盖了核心逻辑。`pool_status.py` 的 heartbeat 展示和 sticky 警告已在既有代码中正确扩展。`egress_rotate.py` 的 `report_fail()` 调用设计合理。文档 §3.1 准确。

发现了 **1 个 P1 缺陷**（`_alive` 中 PowerShell 过滤区分大小写，但 `name` 已 `.lower()`，此处无问题；**P1 是 `list_matching` 的 PowerShell 转义未覆盖 `"`**）和 **2 个 P2 可改进点**，以及少量 Info 建议。

---

## 发现汇总

| ID | 级别 | 文件 | 行号 | 简述 |
|----|------|------|------|------|
| P1 | **P1** | `ops_heartbeat.py` | 40 | `list_matching` 单引号转义未处理 `"`，若 pattern 含双引号会 PS 语法错误 |
| P2 | **P2** | `ops_heartbeat.py` | 111–118 | `_alive` 最后 fallback `return bool(rows)` 在全部 name 为空时宽松 |
| P2 | **P2** | `tests/test_ops_heartbeat.py` | — | 缺失 `count_live_pool` 空目录、`build_heartbeat` 空 cfg 的测试 |
| I1 | Info | `pool_status.py` | 264–266 | clips proxy log 路径硬编码 `D:/cli-proxy-api/logs/main.log`（既有，非本次引入） |
| I2 | Info | `cpa_xai/egress_rotate.py` | 49–56 | `report_fail()` 裸 `except Exception: pass` — 有意为之，但建议加 log |
| I3 | Info | `docs/UNATTENDED.md` | 63–77 | 文档准确，建议补充 `--write` 默认路径 |
| I4 | Info | `pool_status.py` | 561–570 | heartbeat 展示正确，但 level 为 `"ok"` 时条件 `hb.get("level")` 仍 truthy，无实际影响 |

---

## 逐条确认

### 1. `ops_heartbeat.py` — 新建文件

**list_matching (L37–63)**
- 使用 PowerShell CIM 避免 psutil 依赖，正确。
- 单引号转义 `pattern.replace("'", "''")`（L40）对 PowerShell 单引号字符串是安全的，但 pattern 中的 `"` 未经转义，若 pattern 含双引号会破坏 PS 字符串语法。**见 P1**。
- `subprocess.check_output` 的超时 30s 合理。
- 结果解析兼容单对象（dict）和数组。

**count_live_pool (L66–81)**
- 正确：遍历 `xai-*.json`，跳过 `disabled`。无 JWT 网络探测 — 符合设计目标。
- 空目录返回 `(0, 0)`。

**min_live_from_cfg (L84–92)**
- 按优先级 `pool_min_live` → `quota_watch_min_pool` → 默认 100。
- `int(cfg.get(key) or 0)` 安全处理了 None/空字符串/非数字。
- 默认 100 是硬编码，若号池较小（<100）可能过早 warn。但属配置范畴，非代码 bug。

**build_heartbeat (L95–169)**
- 纯函数，proc_rows 注入良好。
- 等级逻辑：register/cliproxy 死亡 → critical；quota_watch 死亡 → warn（若已是 critical 不降级）；pool 低于阈值 → warn（若已是 critical 不降级）。**正确**。
- `_alive` 内部函数（L111–118）：见 **P2**。

**_alive 函数分析**：
```
L113: name = str(r.get("Name") or r.get("name") or "").lower()
L114: if "powershell" in name: continue      # 区分大小写已处理 ✅
L116: if name.endswith(".exe") or name:      # 若 name="" 不 return
L117:     return True
L118: return bool(rows)                      # fallback
```
- ✅ PowerShell 过滤：`name` 已 `.lower()`，`"powershell"` 全小写匹配，对 `"powershell.exe"` 正确。
- ⚠️ L116 `or name`：当 name 为空字符串时 `""` 为 falsy，不 return；继续循环。
- ⚠️ L118 若循环完毕未 return，则 `return bool(rows)`。若 rows 全部为空 name，仍返回 True。实践中不会发生（CIM 返回的 Name 总是有值），但逻辑上偏宽松。

**main (L180–208)**
- CLI 设计合理：`--json` 纯 JSON 输出、`--write` 支持 `"default"` 快捷路径。
- 退出码 0/1/2 与文档一致。
- `exit_code_for(str(hb.get("level") or "ok"))` 中 `str(...)` 安全转换。

### 2. `tests/test_ops_heartbeat.py` — 单元测试

**已覆盖**：
- `count_live_pool`：disabled 过滤、noise.txt 忽略
- `build_heartbeat`：critical（register 死亡）、warn（pool 不足）、ok（全部正常）
- `min_live_from_cfg`：两个 key 优先级、默认值
- `exit_code_for`：隐式验证（通过 assert hb["level"] 后调用）

**缺失**（见 P2）：
- `count_live_pool` 空目录 → `(0, 0)`
- `build_heartbeat` 空 cfg 无 proc_rows（纯默认）
- `_alive` 的 PowerShell 过滤行为
- `min_live_from_cfg` 非法值（如 `"abc"`、`-1`、`0`）
- `build_heartbeat` 二者同时死亡（register + cliproxy）

### 3. `pool_status.py` — 既有文件修改

**_power_ac_sleep_status (L93–147)**
- 解析 powercfg 输出，`_ac_index` 含正则 fallback（`0x[0-9a-fA-F]{8}`），但对 `0x0`（1 位十六进制）的 fallback 会 miss。不过第一遍行内解析已覆盖常见格式。
- `out["warn"]` 逻辑：`not (ac_sleep_never is True and ac_lid_do_nothing is True)` — 若任一为 None（解析失败），warn=True。**正确**。

**_load_heartbeat_file (L150–157)**
- 简单正确：读取 `logs/heartbeat.json`，错误时返回 `{"ok": False, "error": ...}`。
- 缺失文件返回 `"missing"`，`print_human` 中若 error="missing" 则静默跳过（L569）。**正确**。

**print_human 的 heartbeat 展示 (L561–570)**
```python
hb = snap.get("heartbeat") or {}
if hb.get("level") or hb.get("ok") is True:
    ...
elif hb.get("error") and hb.get("error") != "missing":
    ...
```
- `hb.get("level")` 对 `"ok"` 字符串 truthy，对 `None`（错误时）falsy。**正确**。
- 仅当 `error != "missing"` 时打印错误行。**正确**。

**sticky reselect 警告 (L542–546)**
- 阈值 15% 是社区经验值，可配置性建议已记录。警告信息清晰（"soft-disable 勿硬删 live 池"）。
- `reselect_rate` 计算（L191–192）：`denom = hit + miss + reselect`，`rate = reselect / denom`。**正确**。

**collect_snapshot 的 heartbeat 收集 (L446)**
- `snap["heartbeat"] = _load_heartbeat_file()` — 在函数末尾，不依赖其他数据，**正确**。

### 4. `cpa_xai/egress_rotate.py` — rotate 前 report_fail

**report_fail 调用 (L47–56)**
- 设计意图：在切换节点前，对上次失败的 Clash 出口做 soft-disable 计分（社区需求：fail streak → 丢弃坏节点）。
- 独立 `try/except` 包裹，不影响主旋转流程。**正确**。
- 裸 `except Exception: pass` 无 log（见 **I2**）。

**主旋转逻辑 (L58–83)**
- `rotate_egress_proxy` 从 `grok_register_ttk` 延迟导入。
- `proxy` 解析：优先 `cfg._runtime_http_proxy` → `cfg.proxy` → `egress.http_proxy`。
- `out["ok"]` = bool(clash_node or http_proxy or proxy)。**正确**。

### 5. `docs/UNATTENDED.md` §3.1

- 退出码表（0/1/2）与代码一致。
- 建议 Task Scheduler 每 10–15 分钟 + `--write`。**准确**。
- 注明 "不发网络 probe，不碰号池文件内容以外的读"。**准确**。
- 建议补充 `--write` 默认路径为 `logs/heartbeat.json`（见 **I3**）。

---

## 建议修复

### P1 — `list_matching` 应一并转义双引号

**文件**：`ops_heartbeat.py` L40  
**问题**：`pattern.replace("'", "''")` 只转义了单引号。PowerShell 单引号字符串中唯一需要转义的字符是 `'`（写为 `''`），但实际上 `"` 在单引号字符串中不需要转义。**当前代码在 PowerShell 单引号字符串中是正确的**，但若 future 有人改为双引号字符串或 pattern 本身含 `"` 在拼接处可能出问题。

**建议**：保持当前行为，或者为确保最大兼容性，在 pattern 中去除 `"`。

```python
# 当前 L40 已是安全的 — 单引号字符串中只需转义 '
pat = pattern.replace("'", "''")
```

**结论**：经重新确认，PowerShell 单引号字符串中 `"` 无需转义。**P1 降级为 Info** — 当前代码安全，无需修改。

### P2 — `_alive` 最后 fallback 可更严格

**文件**：`ops_heartbeat.py` L111–118  
**问题**：若所有行 `name` 为空，`return bool(rows)` 返回 True，但实际未识别到有效进程。  
**建议**：将 fallback 改为 `return False`，使逻辑更严谨：

```python
def _alive(rows: list[dict[str, Any]]) -> bool:
    for r in rows or []:
        name = str(r.get("Name") or r.get("name") or "").lower()
        if "powershell" in name:
            continue
        if name.endswith(".exe") or name:
            return True
    return False  # 而非 bool(rows)
```

### P2 — 补充测试用例

**文件**：`tests/test_ops_heartbeat.py`  
**建议添加**：

```python
def test_count_live_pool_empty_dir(self):
    with tempfile.TemporaryDirectory() as td:
        live, total = count_live_pool(Path(td))
        self.assertEqual((live, total), (0, 0))

def test_build_heartbeat_empty_cfg(self):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        hb = build_heartbeat(root=root, cfg={}, proc_rows={})
        self.assertEqual(hb["level"], "critical")  # 无进程→全死
        self.assertIn("register", hb["alerts"][0])

def test_min_live_from_cfg_invalid(self):
    self.assertEqual(min_live_from_cfg({"pool_min_live": "abc"}), 100)
    self.assertEqual(min_live_from_cfg({"pool_min_live": None}), 100)
```

### I2 — `report_fail` 异常建议加 log

**文件**：`cpa_xai/egress_rotate.py` L55  
**建议**：将 `pass` 改为 `log(...)`，便于排查 `clash_proxy` 缺失或 `report_fail` 异常：

```python
except Exception as exc:
    log(f"mint egress report_fail skipped: {exc}")
```

### I3 — 文档补充默认路径

**文件**：`docs/UNATTENDED.md` §3.1  
**建议**：在 `--write` 示例下方补充一行，说明不指定路径时默认写入 `logs/heartbeat.json`。

---

## 总结

| 维度 | 评分 |
|------|------|
| 正确性 | ✅ 核心逻辑正确，等级判定与文档一致 |
| 可测试性 | ✅ `proc_rows` 注入设计良好 |
| 测试覆盖 | ⚠️ 覆盖了主要路径，缺边界用例（见 P2） |
| 文档一致性 | ✅ 文档与代码吻合 |
| 安全性 | ✅ 无 token 泄露、无网络探测、无文件写入越界 |
| 可维护性 | ✅ 结构清晰，注释充分 |

**无 blocking 缺陷**，P2 建议可选修复。