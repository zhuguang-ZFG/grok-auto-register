# Kiro 抬 live 率：sticky 住宅出口（不改 Clash 全局）

目标：Playwright / Kiro 产号时，**浏览器与 httpx 都走「住宅 sticky 出口」**，同时本机日常上网仍走 Clash **rule**，**禁止** `mode: global`。

## 为什么现在 100% 秒封

| 层 | 现状（2026-07-14 实测） |
|----|------------------------|
| 注册流程 | Playwright 可稳定出 token |
| 当前 Clash 出口 | `35.186.158.151`（机房/云，非住宅） |
| 历史 active_node | 宝可梦「美国04原生」→ `23.184.88.81` AS46829 Lamhosting（仍偏 hosting） |
| NovProxy 短效 SOCKS | 直连：大陆 IP 不可用；经 Clash 链式：曾 auth/数据异常 |
| **2026-07-14 再探** | 旧短效 SOCKS **认证返回 `\x01\x01`（失败）**——凭据过期/余额/格式不对，**链路未就绪** |

账号级 `temporarily is suspended` 与「流程 bug」分层：流程已通，**出口信誉**未过。

## 总体架构（推荐）

```
本机日常浏览器/软件
    └─ Clash rule + TUN（不动）

Kiro 产号专用：
  httpx OIDC  ──HTTP/SOCKS──► 住宅 sticky
  Playwright  ──同上或仅 TUN 规则命中 AWS/Kiro 域名──► 住宅 sticky
```

**不要**把住宅塞进 GLOBAL，否则一切流量跟注册机一起飘，用户已明确会断网。

---

## 方案 A — 买/续「动态住宅 + sticky session」（首选）

### 1. 控制台里要什么

在 [NovProxy](https://dash.novproxy.com/home/socks5) 或同类（IPRoyal / Bright Data / Oxylabs / 922 / 代理猫等）：

1. **产品类型**：Dynamic residential / 动态住宅（不是纯机房 static）
2. **地区**：US 优先（与 Builder ID / Kiro 常见一致）
3. **Sticky / 会话保持**：同一 session id 固定出口 **≥10–30 分钟**（一号一出口）
4. **协议**：优先 **HTTP(S) proxy**（Playwright/httpx 最好接）；SOCKS5 次之
5. **大陆是否可直连**：文档写 *Chinese Mainland IP not accessible* 时，必须先有 **海外跳板**（见方案 B）

### 2. 账号密码格式（动态常见）

很多家不是「固定 IP:port + 固定密码」，而是：

```text
host:port
user = 主账号-zone-xxx-region-us-session-<随机串>-sessTime-15
pass = 主密码
```

- `session-xxx` 相同 → 同一出口（sticky）  
- 每个 Kiro 号换一个新 session 串 → 换 IP，避免一 IP 多号连坐  

面板里旧短效 IP 列表探测若是 **auth fail**，请：

1. 重新 **Extract / 生成** 一行  
2. 确认余额、白名单、是否点了「使用」  
3. 看清是 **static IP 列表** 还是 **gateway 主机名 + 动态 user**（文档：[账密教程](https://help.novproxy.com/dynamic-traffic-usage-tutorial/zhangmi)）

### 3. 接到本机（不改全局）— 仅产号进程用

```bash
# 示例：住宅 HTTP（换成你新提取的）
export KIRO_RESI_PROXY="http://user:pass@gateway.example:port"

# httpx 阶段（OIDC）走住宅
python scripts/kiro_pw_register.py --n 1 --proxy "$KIRO_RESI_PROXY"
```

**Playwright 注意**：之前 **Clash 显式 proxy 会搞挂 kiro.dev SPA**。住宅若是干净 HTTP：

- 可试：给 browser context 挂 **同一住宅**（不是 7897）  
- 若 SPA 再炸：只让 httpx 走住宅，浏览器靠「Clash 规则只把 AWS/Kiro 指到住宅组」（方案 C）

一号一 session 伪代码：

```text
for each account:
  session = random_hex(8)
  proxy = f"http://{user}-session-{session}-sessTime-15:{pass}@{host}:{port}"
  register_one(..., proxy=proxy)
```

### 4. 验收（先于 n≥5）

```bash
# 出口必须是 residential / isp，而不是 cloud / hosting
curl -x "$KIRO_RESI_PROXY" -sS --max-time 20 https://ipinfo.io/json
# 看 org / asn：Amazon/Google/DigitalOcean/Leaseweb/Lamhosting 等 → 不合格
# 家庭宽带 ISP 名 + asn 常见 residential → 可进入 n=5
```

再跑：

```bash
python scripts/kiro_pw_register.py --n 5
# 每个 success → import-only → recheck
```

指标：`live_chat / token_success` > 0 才算出口有效。

---

## 方案 B — 大陆上不了住宅网关：海外跳板链式（不改全局）

现状：本机在国内 → NovProxy 曾报 *Chinese Mainland IP not accessible*。

```
本机 :10808 (chain_socks5)
  → Clash :7897（仅 CONNECT 到住宅网关，仍是 rule）
  → 住宅 SOCKS/HTTP
  → 目标站
```

仓库已有：`scripts/chain_socks5.py`（本地监听 `127.0.0.1:10808`）。

步骤：

1. 面板拿到 **仍有效** 的 user/pass/host/port，写入或改 `chain_socks5.py` 顶部常量  
2. 起链：`python scripts/chain_socks5.py`  
3. 测：`curl -x socks5h://127.0.0.1:10808 https://ipinfo.io/json`  
4. 产号：`--proxy socks5://127.0.0.1:10808`（httpx）；Playwright 是否挂同一 SOCKS **需实测 SPA**

**当前阻塞**：NovProxy **认证失败**，链式也过不了——必须先换新凭据。

可选替代跳板：任意一台 **境外 VPS** 上跑 `gost`/`sing-box`：

```text
VPS 公网:1080 ← 本机 Clash 规则只代理 VPS:1080
VPS 再出站到 NovProxy 住宅
```

本机仍然 **rule**，只把 `VPS_IP` 或 `gateway.novproxy...` 指到「境外节点」组。

---

## 方案 C — Clash 内「Kiro 专用组」（推荐中长期，仍不 global）

在 Clash Verge **订阅/覆写**里加（名字随意）：

```yaml
proxy-groups:
  - name: "Kiro住宅"
    type: select
    proxies:
      - 你的住宅节点或落地链   # 若订阅里没有住宅，用 external-controller 挂 proxy provider

rules:
  # 尽量靠前
  - DOMAIN-SUFFIX,amazonaws.com,Kiro住宅
  - DOMAIN-SUFFIX,aws.amazon.com,Kiro住宅
  - DOMAIN-SUFFIX,awsapps.com,Kiro住宅
  - DOMAIN-SUFFIX,signin.aws,Kiro住宅
  - DOMAIN-SUFFIX,profile.aws.amazon.com,Kiro住宅
  - DOMAIN-SUFFIX,kiro.dev,Kiro住宅
  - DOMAIN-SUFFIX,oidc.us-east-1.amazonaws.com,Kiro住宅
```

- 模式保持 **rule**  
- 日常网站仍走默认组  
- Playwright **可不设** `proxy=`，靠 TUN + 规则进住宅（这是之前「无显式 proxy 才能开 SPA」的兼容做法）  
- 注册机只 `PATCH` 切换 **Kiro住宅** 组内节点，**绝不**改 `mode` / `GLOBAL`

与 Grok 的「注册专用」组同构，见 `docs/CLASH_ISOLATE.md`。

---

## 方案 D — 没有境外 VPS / 住宅预算时

| 选项 | 期望 |
|------|------|
| 只换宝可梦里更「原生」节点 | 可能仍 hosting，live 率可能仍 ≈0 |
| 降频 + 少并发 | 降低连坐，难单独解秒封 |
| Google/GitHub Social 注册 | 社区另一条线，未接产线 |
| 官方申诉 | 有 token 的 suspended 号可试，不自动化 |

**没有 sticky 住宅时，不要指望 n≥5 突然 live。**

---

## 本机检查清单（按顺序）

1. [ ] Clash：`mode=rule`，未 global  
2. [ ] 面板重新提取住宅；确认 **auth 成功**（SOCKS `\x01\x00` 或 HTTP 407 消失）  
3. [ ] `curl -x ... ipinfo` → org **非** cloud/hosting  
4. [ ] sticky：同一 session 两次 curl IP 相同；换 session IP 变  
5. [ ] `python scripts/kiro_pw_register.py --n 1 --proxy <住宅>` happy path  
6. [ ] import + 首聊：若仍 403，换 **ISP 住宅** 或区（US 家庭）再试 n=5  
7. [ ] 记录：`docs/KIRO_PW_STATUS.md` 追加「住宅实验」表

## 明确不做

- 不改 Clash **global** / 不 `DELETE /connections` 清全机  
- 不用 CF-WARP/VLESS 当「住宅」指望解秒封  
- 不把失败 NovProxy 凭据当可用出口硬跑 n≥5（浪费 hotmail）

## 你现在立刻能做的 3 件事

1. 打开住宅面板，**重新生成** 动态住宅账密（旧短效行已 auth fail）  
2. 只写本机 env：`NOVP_HOST` / `NOVP_PORT` / `NOVP_USER` / `NOVP_PASS`（**勿提交 git**）  
3. 确认 **HTTP 还是 SOCKS**、是否 **大陆可直连** → 再跑 `chain_socks5` + 验收 curl  

在 **ipinfo 显示住宅** 之前，跑 n≥5 只会重复 100% 秒封。
