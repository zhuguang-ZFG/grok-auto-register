# 批量注册

## 配置（config.json）

| 字段 | 当前建议 | 说明 |
|------|----------|------|
| `register_count` | `12` | 单批目标；宁可多批少量 |
| `concurrency` | `1` | 同出口 IP 建议 1；有多住宅 IP 再升 |
| `batch_delay_sec` | `12` | 账号间基础间隔 |
| `batch_delay_jitter_sec` | `10` | 间隔随机抖动 |
| `batch_pause_every` | `6` | 每成功 N 个长休 |
| `batch_pause_sec` | `120` | 长休秒数 |
| `defaultDomains` | 多域名逗号分隔 | 与邮件后台 domains 一致 |
| `proxy` / `browser_proxy` | 本机活代理 | 自动探测备用端口 |
| `stealth_patch` | `true` | Turnstile 必须 |
| `hide_window` | `false` | 过盾更稳 |
| `cpa_export_enabled` | `true` | 写本地 OIDC auth |
| `cpa_push_enabled` | `true` | 推远端；远端 TLS 不通时本地仍成功 |
| `cpa_push_proxy` | 与 proxy 相同 | Clash fake-ip 必须走代理 |

## 社区踩坑对照（CPA / 免费号）

常见两类失效：

1. **额度用尽**（rate limit / quota）— 号还活着，只是免费额度打完  
2. **token 失效**（invalid/expired token）— refresh 失败或会话被踢；不一定等于邮箱域名已死  

域名侧：

- 已污染域名：注册阶段直接拒收  
- 半死域名：仍可收验证码，但号池里旧 token 大量失效  

建议节奏：

- 单批 **8–15**，并发 **1**（同代理）  
- 多域名轮换，不要只砸一个 `baoxia.top`  
- 注册成功后立刻 mint OIDC 写本地 `cpa_auths`，CPA 侧做健康检查踢死号  
- 不要把“分享号池里捡来的旧 token”当稳定产能

## 启动

```bat
run_batch.bat
run_batch.bat 50 3
```

```powershell
.\run_batch.ps1
.\run_batch.ps1 -Count 50 -Concurrency 3
.\run_batch.ps1 -RetryPush
```

```bash
python grok_register_ttk.py -n 20 -c 2 -y
python grok_register_ttk.py --retry-push
python grok_register_ttk.py --no-push -n 5 -c 1 -y
```

## 产物

- `accounts_YYYYMMDD_HHMMSS.txt`：`email----password----sso`
- `cpa_auths/xai-<email>.json`：CLIProxyAPI 认证
- `cpa_auths/cpa_push_pending.txt`：推送失败待重推列表
- `mail_credentials.txt`：临时邮箱凭据

## 已知远端问题

`https://cpa.baoxia.top` 在本机经代理 CONNECT 后仍 TLS EOF（Google/x.ai 正常）。  
属远端/解析链路问题，不影响注册与本地 CPA 文件。修好远端后执行：

```bash
python grok_register_ttk.py --retry-push
```
