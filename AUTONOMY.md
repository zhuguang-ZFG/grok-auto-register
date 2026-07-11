# 全程无感自动化

## 开启（一次）

```powershell
cd D:/Users/grok-auto-register
Set-ExecutionPolicy -Scope Process Bypass
.\enable_autonomy.ps1
```

会安装 3 个计划任务：

| 任务 | 频率 | 做什么 |
|------|------|--------|
| `GrokPoolMaintain` | 每 2 小时 | 健康检查 → 不足自动补号 → 同步 CLI |
| `GrokPoolHealth` | 每 45 分钟 | 刷新 token / 踢死号 / 同步 CLI（不注册） |
| `GrokPoolBoot` | 登录时 | 开机跑一轮 maintain |

## 闭环流程

```
[定时/开机]
    ↓
pool_health  刷新临期token、/models探测、隔离死号
    ↓
live < min ? ──是──→ grok_register_ttk 自动补号（三域名轮换+伪装）
    ↓否
auto_link_cli  把健康号同步/junction 到 CLIProxy 的 auth-dir
    ↓
cpa_auths/ 始终可用 → Grok CLI / CLIProxyAPI 无感切换
```

## CLI 对接（只需一次）

1. 看指针文件：
   ```
   D:/Users/grok-auto-register\CLI_AUTH_DIR.txt
   ```
2. CLIProxyAPI 的 `auth-dir` 指到该路径（默认即 `cli_live`）
3. 或在 `config.json` 写死你的目录：
   ```json
   "cli_proxy_auth_dirs": ["C:/path/to/your/auth"]
   ```
   之后 `auto_link_cli.py` 会自动同步过去。

## 日常你不用管

- 号少了：maintain 自动补  
- token 快过期：health 自动 refresh  
- 死号：自动进 `cpa_auths/dead/`，CLI 目录里消失  
- 新号：health 通过后进 `cli_live`  

## 关闭

```powershell
.\disable_autonomy.ps1
```

## 手动

```bat
run_pool.bat
run_pool.bat health
run_pool.bat refill 6
```
