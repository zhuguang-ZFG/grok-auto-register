# 本机无值守补强（电源 / 睡眠 / 代理）

目标：在 **不搬 VPS** 的前提下，尽量让号池闭环在插电笔记本上连续跑。

## 1. 电源（一次性）

```powershell
cd D:\Users\grok-auto-register
powershell -ExecutionPolicy Bypass -File .\scripts\ensure_power_awake.ps1
```

效果（AC 插电）：

- 睡眠 = 从不  
- 合盖 = 不采取任何操作  
- 休眠定时 = 从不  
- 允许唤醒定时器  

电池策略默认不动。若也要电池不睡：加 `-AlsoBattery`（费电）。

验证：

```bat
powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
powercfg /q SCHEME_CURRENT SUB_BUTTONS LIDACTION
```

AC 当前值应为 `0x00000000`（睡眠/合盖 do-nothing）。

## 2. 代理稳定

```bat
python proxy_health.py
python proxy_health.py --rotate-if-bad
```

- Clash API 可达  
- 探测出口 IP  
- `GET accounts.x.ai`（TLS）；失败则轮换节点再探  
- 结果写入 `.proxy_health.json`，`pool_status` 会显示  

已挂进：

- `run_health_only.bat`（GrokPoolHealth）  
- `pool_maintain.py` 开头（GrokPoolMaintain）  

## 3. 进程 / 任务

保持：

- Clash 常开  
- `Grok*` 计划任务 Enabled  
- 插电、别进现代待机死睡  

日常：

```bat
python pool_status.py
```

看：代理健康、铸造 protocol_ok、CPA 水位、三进程。

## 4. 边界

- 合盖 + 某些 OEM 仍可能进连接待机 → 任务暂停；真要铁 7×24 用小 VPS。  
- 代理节点全挂时，注册/铸造会失败；health 只能换节点，不能造节点。  
- 本方案 **不** 提高并发、不改 buffer_first 策略。
