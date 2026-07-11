# 同步核对清单（防遗漏）

正式项目：`D:\Users\grok-auto-register`

## 代码
- [x] `mail_backends` 多后端四域名创建/收信路由
- [x] `turnstilePatch` MV3 + script.js
- [x] `pool_health.py` / `pool_maintain.py` / `auto_link_cli.py`（路径已改正式项目）
- [x] 运维 bat/ps1：`run_pool` / `run_maintain` / `enable_autonomy` / `disable_autonomy`
- [x] 文档：AUTONOMY / KIMI_CLIPROXY / POOL / TURNSTILE / BATCH

## 号池
- [x] 压缩包导入 `cpa_auths`（约 320 个）
- [x] CLIProxy `auth-dir` = `D:/Users/grok-auto-register/cpa_auths`
- [x] Kimi `local-cpa` → `http://127.0.0.1:8317/v1`，`default_model=local-cpa/grok-4.5`

## 定时任务（应指向本目录）
- [x] GrokPoolMaintain
- [x] GrokPoolHealth
- [x] GrokPoolBoot
- [x] CLIProxyAPI-Local

## 不要再用
- `D:\Downloads\grok-register-ref\...`（试验目录，仅参考）
