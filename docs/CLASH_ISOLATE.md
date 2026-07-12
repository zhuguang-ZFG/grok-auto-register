# Clash 注册出口隔离（社区常见做法）

目标：**注册机换节点不影响本机日常上网**，CLIProxy 仍可走系统代理访问 xAI。

## 原理

| 模式 | 行为 | 隔离效果 |
|------|------|----------|
| Clash `global` | 所有流量走 `GLOBAL` 组 | 注册机切 GLOBAL = 本机一起换出口 |
| Clash `rule` + 专用组 | 仅匹配规则的域名走 `注册专用` | 本机其它流量走默认组，不受影响 |

本仓库代码侧已支持：

```json
{
  "clash_selector": "注册专用",
  "clash_force_global": false,
  "clash_close_conns": false,
  "clash_rotate_every_n": 5
}
```

`rotate_node(selector=...)` 只切换指定 Selector，**不再**强制 global、**不再** `DELETE /connections`。

## 操作步骤（Clash Verge / Mihomo）

### 1. 订阅/配置里增加 proxy-group

在配置的 `proxy-groups:` 中增加（节点名按你订阅实际修改，可与 GLOBAL 相同节点列表）：

```yaml
proxy-groups:
  - name: "注册专用"
    type: select
    proxies:
      - 🇹🇼 [三网]台湾2
      - 🇭🇰 [三网]香港3
      - 🇸🇬 [三网]新加坡2
      - 🇩🇪 [三网]德国
      # ...其余给你注册用的节点
```

也可用 `use: [订阅providers名]` 引用整包节点（视你的配置写法而定）。

### 2. 规则：xAI / Grok 走专用组

在 `rules:` **尽量靠前**加入：

```yaml
rules:
  - DOMAIN-SUFFIX,x.ai,注册专用
  - DOMAIN-SUFFIX,grok.com,注册专用
  - DOMAIN-SUFFIX,accounts.x.ai,注册专用
  - DOMAIN-SUFFIX,auth.x.ai,注册专用
  - DOMAIN-SUFFIX,cli-chat-proxy.grok.com,注册专用
  # 其余保持你原来的规则…
  # - MATCH,你的默认组
```

### 3. 模式改为 rule

- 面板：模式选 **规则 / Rule**（不要用全局 Global）
- 或 API：`PATCH /configs` `{"mode":"rule"}`（勿在注册机里 force_global）

### 4. 写入本项目 config.json

```json
{
  "clash_selector": "注册专用",
  "clash_force_global": false,
  "clash_close_conns": false,
  "clash_rotate_every_n": 5,
  "clash_api": "http://127.0.0.1:9097",
  "clash_secret": "你的secret"
}
```

重启注册机（或等 auto 轮次 `load_config`）后日志应出现切 `注册专用` 节点，而本机浏览器出口不变。

### 5. 验证

```bash
# 看 Selector 是否存在
# Clash API /proxies 中应有 type=Selector 且 name=注册专用

python -c "from clash_proxy import rotate_node; print(rotate_node(selector='注册专用', force_global=False, close_conns=False, log=print))"
```

本机浏览器打开 `https://api.ipify.org`，注册换节点前后 IP **应不变**；  
注册流量访问 accounts.x.ai 时走专用组节点。

## 注意

1. **CLIProxy 访问 grok/x.ai 也会走「注册专用」**（按域名分流，分不清进程）。这通常可接受。
2. 若仍用 `global` 模式，填 `clash_selector` **无法**隔离本机——必须 `rule`。
3. 不改 Clash 时：保持 `clash_close_conns=false` 已能避免「换节点掐死本机连接」。

## 相关

- `clash_proxy.py` · `grok_register_ttk.rotate_egress_proxy`
- `docs/HARDEN.md` 第 3 节
