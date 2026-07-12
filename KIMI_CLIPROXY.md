# 把本机 Grok 号池接到 Kimi Code CLI

## 结论（先看这个）

| 问题 | 答案 |
|------|------|
| Kimi CLI 能直接读 `cpa_auths/xai-*.json` 吗？ | **不能**。Kimi 只认 HTTP Provider（OpenAI 兼容等） |
| 社区标准方案是什么？ | **CLIProxyAPI** 吃 OAuth 凭证目录 → 暴露 `/v1` OpenAI 接口 → Kimi 当 provider 用 |
| 必须挂 VPS 上的 New API 吗？ | **不必**。本机 CLIProxy 就能用；VPS 适合 7×24 / 多机共用 |
| New API 有什么用？ | 多 Key、渠道管理、统一入口；上游再指到 CLIProxyAPI |

链路：

```
本机注册机 → cpa_auths / cli_live (xai-*.json)
                 ↓
          CLIProxyAPI (:8317)  ← sticky + 额度 failover
                 ↓
     （可选）VPS NewAPI / 反代
                 ↓
          Kimi Code CLI  provider
```

### CLIProxy 加固（本机 config.yaml）

```yaml
routing:
  strategy: round-robin
  session-affinity: true          # 粘会话，利于长对话
  session-affinity-ttl: "4h"
```

切换：`python set_cliproxy_routing.py cache|pool|status`  
号池侧配套：**soft-disable、禁止硬删 CPA**（见 [docs/HARDEN.md](docs/HARDEN.md)）。  
Kimi 默认模型建议：`local-cpa/grok-4.5` → `http://127.0.0.1:8317/v1`。

社区项目：[router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)  
Kimi Provider 文档：[Providers and Models](https://moonshotai.github.io/kimi-cli/en/configuration/providers.html)

---

## 方案 A：本机直连（最快，推荐先打通）

### A1. 跑 CLIProxyAPI

Windows / Docker 示例（auth 指到本仓库热目录）：

```yaml
# 例：D:/cliproxy/config.yaml
port: 8317
auth-dir: D:/Users/grok-auto-register/cli_live
api-keys:
  - sk-local-grok-pool
remote-management:
  allow-remote: false
```

Docker：

```bash
docker run -d --name cliproxyapi ^
  -p 8317:8317 ^
  -v D:/cliproxy/config.yaml:/CLIProxyAPI/config.yaml ^
  -v D:/Users/grok-auto-register/cli_live:/root/.cli-proxy-api ^
  --restart unless-stopped ^
  eceasy/cli-proxy-api:latest
```

验收：

```bash
curl http://127.0.0.1:8317/v1/models -H "Authorization: Bearer sk-local-grok-pool"
```

> 号池维持任务已会刷新/隔离死号并同步 `cli_live`，CLIProxy 会跟着变 → **无感换号**。

### A2. 写入 Kimi `config.toml`

文件一般在：`C:/Users/zhugu/.kimi-code/config.toml`  
（官方文档也写 `~/.kimi/config.toml`，以你本机实际为准）

追加（不要删你现有的 rainflow/haxcat 等）：

```toml
[providers.local-cpa]
type = "openai_legacy"   # 若你当前文件用 type = "openai" 且已能用，保持与现有一致
base_url = "http://127.0.0.1:8317/v1"
api_key = "sk-local-grok-pool"

[models."local-cpa/grok-4.5"]
provider = "local-cpa"
model = "grok-4.5"       # 以 /v1/models 实际返回的 id 为准，可能是 grok-3 / grok 等
max_context_size = 131072
display_name = "Grok Pool (Local CPA)"
```

改完校验：

```bash
python -c "import tomllib; tomllib.load(open(r'C:/Users/zhugu/.kimi-code/config.toml','rb')); print('toml ok')"
```

使用：

```bash
kimi -m local-cpa/grok-4.5
# 或会话里 /model
```

---

## 方案 B：挂到 VPS（7×24，多机共用）— 推荐生产

**不必须 New API。** VPS 上只跑 CLIProxyAPI 即可。

### B1. VPS 部署 CLIProxyAPI

```bash
mkdir -p /opt/cliproxyapi/auth
cat >/opt/cliproxyapi/config.yaml <<'EOF'
port: 8317
auth-dir: /root/.cli-proxy-api
api-keys:
  - sk-请换成长随机串
remote-management:
  allow-remote: true
  # secret-key: 与注册机 cpa_remote_secret 一致时可远程导入
EOF

docker run -d --name cliproxyapi \
  -p 127.0.0.1:8317:8317 \
  -v /opt/cliproxyapi/config.yaml:/CLIProxyAPI/config.yaml \
  -v /opt/cliproxyapi/auth:/root/.cli-proxy-api \
  --restart unless-stopped \
  eceasy/cli-proxy-api:latest
```

前面用 Caddy/Nginx HTTPS 反代到 `127.0.0.1:8317`，例如 `https://cpa.yourdomain.com`。

### B2. 本机号池推到 VPS

任选其一：

1. **管理 API 推送**（注册机已支持 `cpa_push_*`）  
   - `cpa_remote_base = https://你的域名`  
   - `cpa_remote_secret` 对齐 management secret  
   - 修好 TLS 后 `python grok_register_ttk.py --retry-push`

2. **rsync/scp 同步 `cli_live` → VPS auth-dir`**  
   计划任务每小时同步一次（最稳，不依赖 management API）

3. **本机挂载/对象存储**（少用）

### B3. Kimi 指到 VPS

```toml
[providers.vps-cpa]
type = "openai_legacy"
base_url = "https://cpa.yourdomain.com/v1"
api_key = "sk-请换成长随机串"

[models."vps-cpa/grok-4.5"]
provider = "vps-cpa"
model = "grok-4.5"
max_context_size = 131072
display_name = "Grok Pool (VPS CPA)"
```

---

## 方案 C：VPS 上 New API + CLIProxy（可选）

适用：要统一 Key、多渠道、Web 管理、给多人用。

```
Kimi CLI → NewAPI(公网) → CLIProxyAPI(本机或同 VPS) → xai 号池
```

NewAPI 里新增渠道：

- 类型：OpenAI 兼容  
- Base URL：`http://127.0.0.1:8317/v1`（同机）或内网地址  
- 模型映射：`grok-4.5` 等  

Kimi 只配 NewAPI：

```toml
[providers.my-newapi]
type = "openai_legacy"
base_url = "https://你的-newapi/v1"
api_key = "nk-xxx"

[models."my-newapi/grok-4.5"]
provider = "my-newapi"
model = "grok-4.5"
```

你现在的 `rainflow` / `haxcat` / `wwz8` 就是这种 **别人的 NewAPI/网关**；  
要吃**自己号池**，上游必须是你自己的 CLIProxy + `cli_live`。

---

## 和你现状的对照

| 现有 | 含义 |
|------|------|
| `cpa_auths/xai-*.json` | 自己号池，给 CLIProxy 用 |
| `default_model = wwz8/grok-4.5` 等 | 走第三方网关，不是本机号池 |
| `cpa.baoxia.top` 推送 SSL 失败 | 不影响本机 CLIProxy；VPS 修好 TLS 或改 rsync 即可 |
| 定时 `GrokPoolMaintain` | 已在补号+健康；接上 CLIProxy 即全程无感 |

---

## 建议落地顺序

1. **本机**先起 CLIProxy（方案 A）→ Kimi 加 `local-cpa` → 能对话  
2. 确认 `cli_live` 随 `pool_health` 自动更新、死号会消失  
3. 需要手机/多机/长期挂着再上 **VPS CLIProxy**（方案 B）  
4. 真要多 Key/管理面板再加 **NewAPI**（方案 C）  

---

## 安全注意

- 不要把 VPS 密码、API Key 写进仓库或聊天记录  
- CLIProxy 公网必须 `api-keys` + HTTPS，不要裸奔 `0.0.0.0:8317`  
- `auth-dir` 里是完整 refresh_token，等同账号资产  

---

## 一键检查清单

- [ ] `cli_live` 有 `xai-*.json`（`python pool_health.py`）  
- [ ] `curl 127.0.0.1:8317/v1/models` 有 grok 相关模型  
- [ ] `config.toml` 增加 local-cpa / vps-cpa  
- [ ] `kimi -m local-cpa/grok-4.5` 能回消息  
- [ ] 杀掉一个 auth 再 health，CLI 仍能用其它号（无感切换）  
