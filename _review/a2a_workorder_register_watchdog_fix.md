# A2A Workorder — 注册机 worker 挂死修复（join 无界等待 + 按号硬看门狗）

risk: med
owns: grok_register_ttk.py
implementer: Reasonix
review: Atom → Claude（med 需 xreview）

## 背景（三方终审结论，已确认，勿再争论）

`grok_register_ttk.py`（约 5923 行，DrissionPage 多 worker 注册机，daemon 线程）偶发「卡死」表象的根因裁决：

1. **唯一真根因**：`_join_threads_interruptible`（L2961 附近）调用处 `timeout=None` → 内部 `deadline=None` → `while any(t.is_alive())` 纯轮询 join，唯一出口是 `should_stop`。worker 若卡在底层 C 层 socket recv（浏览器进程冻结 / CDP 不应答），Python 层超时拦不住，`is_alive()` 恒 True，主控**永久等待**。
2. **非根因（勿改）**：`wait_for_sso_cookie`（L4568）和 `fill_email_and_submit`（L3677）已有 `while time.time() < deadline` 硬时钟循环，不是纯异常驱动——不要动它们的超时逻辑。
3. **非根因但可加固**：`options.set_timeouts(base=1)`（L1411）未显式设 page_load，DrissionPage 默认 30s 兜底。死代理/CF 下表现为 ~30s×3 轮慢重试，像卡死但不是死锁。

## 实现要求（最小 diff，遵守现有代码风格）

### P0 — join 硬 deadline + 超时强杀（必做）

- 找到 `_join_threads_interruptible` 所有调用处，`timeout=None` 改为具体值：从 `config` 读 `join_timeout_sec`，默认 **1800**（秒）。
- deadline 到期仍有 worker 存活时：
  1. `log_callback` 记录哪些 worker 还活着（线程名）；
  2. 置 stop（走现有 `should_stop` / cancel 通道），给 3s 宽限（现有逻辑已有 grace 模式，可复用）；
  3. 仍不退出的 worker：**强制结束其浏览器子进程**（用项目现有的浏览器清理函数，如 `stop_browser` / `restart_browser` 里实际 kill 进程的那条路径；注意浏览器句柄是 thread-local（`_tls.browser`），主线程杀不了别人的句柄时，退而求其次：按 PID 记录/进程名 kill 该 worker 的 chromium 子进程，或至少 log 明显告警并 return，绝不能永久等）；
  4. **必须 return**，不得继续 `join`。

### P1 — 按号硬看门狗（必做）

- worker 的按号处理循环加单号总时长上限：`config` 读 `account_hard_timeout_sec`，默认 **600**（秒）。
- 单号超过上限：log 告警（含邮箱、已耗时），中断当前号（走现有取消/重启浏览器路径），**继续下一个号**，不得卡住整个 worker。
- 实现方式优先用「循环内每次迭代检查 deadline」的非侵入式；若现有结构是长阻塞调用链无法插桩，则在关键点（open_signup_page 前、wait_for_sso_cookie 前、CPA 导出前）检查。

### P2 — page_load 显式化（可选，小改）

- L1411 `options.set_timeouts(base=1)` 改为 `options.set_timeouts(base=1, page_load=int(config.get("page_load_timeout_sec", 30) or 30))`。默认保持 30 不改行为。

## 硬约束

1. **只许改 `grok_register_ttk.py`**（config 键走 `config.get` 带默认值，不改 config.json 本体）。
2. **注册机正在运行（PID 40300）**：禁止 kill/restart 任何进程，禁止动 logs/、cpa_auths/、data/ 下任何文件。改 .py 对运行中进程无影响（已加载进内存），热替换由主控另行决定。
3. 改完必须自跑 gates（见下）并全绿。
4. 最小 diff：不顺手重构、不改无关格式、不动 L4568/L3677 的超时逻辑。

## gates

```gates
cd /d/Users/grok-auto-register && python -m py_compile grok_register_ttk.py && python -c "import io,re; s=io.open('grok_register_ttk.py',encoding='utf-8',errors='replace').read(); print('join_timeout_cfg', 'join_timeout_sec' in s); print('account_watchdog_cfg', 'account_hard_timeout_sec' in s); print('page_load_cfg', 'page_load_timeout_sec' in s); import re; m=re.findall(r'_join_threads_interruptible\([^)]*\)', s); print('join_calls', len(m)); print('none_left', any('timeout=None' in x for x in m))"
```

通过标准：编译过；join_timeout_sec / account_hard_timeout_sec 为 True；page_load_timeout_sec 为 True（P2 做了的话）；调用处不再出现 timeout=None（除非函数签名默认值）。

## 交付

返回：改动摘要（函数级）+ 每个 P 项落点行号 + gates 输出原文。≤500 字。
