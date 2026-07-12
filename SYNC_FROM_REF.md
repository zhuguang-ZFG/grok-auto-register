# 已从 Downloads 试验目录同步到本项目的内容

来源：`D:/Users/grok-auto-register`（试验）  
正式：`D:\Users\grok-auto-register`（本仓库）

## 已同步
1. **四域名多后端邮件负载**（`mail_backends` + 创建/收信路由）
2. **turnstilePatch** 改为 MV3 + `script.js`（MAIN world）
3. **CLIProxy** `auth-dir` 指向本项目 `cpa_auths`
4. **Kimi** `local-cpa` provider → `http://127.0.0.1:8317/v1`
5. 压缩包 auth 已导入本项目 `cpa_auths`（与原有合并后约 320 个文件）

## 本项目原有且保留
- `quota_watch.py` / `local_grok_auth.py`（Grok CLI 无感换证）
- Clash 节点轮换、anti_detect、异步 CPA mint 等

## 使用
```powershell
cd D:\Users\grok-auto-register
python grok_register_ttk.py start
python quota_watch.py
# Kimi
kimi -m local-cpa/grok-4.5
```
