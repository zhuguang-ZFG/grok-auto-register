# Clash / CF / DNS 抗运营商干扰（2026-07-14）

对齐 linux.do 社区讨论：运营商污染 DNS / 干扰 Cloudflare 路径时，用 **干净 DoH + 规则强制 CF 走代理**（优选 IP / AdGuard 为可选全屋方案）。

## 本机已改（Clash Verge Rev）

路径：`%AppData%\io.github.clash-verge-rev.clash-verge-rev\`

| 文件 | 改动 |
|------|------|
| `profiles/Merge.yaml` | prepend：`linux.do` / `cloudflare*` → **PROXY**；DNS fake-ip + 国内外分流 DoH |
| `profiles/m1NrFPjGn92M.yaml` / `mezXFjHWRNfU.yaml` | 同上 CF/linux.do 规则 |
| `profiles/sFuxQbetf8YR.js` / `sCzXQZi7uoW9.js` | **修正** 曾把 `sharedchat` 写成 DIRECT 的错误；改为代理 + CF 规则 |
| `dns_config.yaml` | fake-ip、`respect-rules`、fallback 1.1.1.1/8.8.8.8、CF/linux.do fallback-filter |
| `verge.yaml` | `enable_dns_settings: true` |

备份目录：`backup-dns-cf-*`

## 你需要做的（UI）

1. 打开 **Clash Verge** → 配置/订阅 → **重新加载** 当前配置（或切换一次订阅再切回）。
2. 确认 **TUN** 仍开启（你当前为 true），系统代理可关（TUN 已劫持）。
3. 设置里确认 **DNS 设置** 已启用（已写 verge.yaml）。
4. 面板里 **PROXY** 组选非大陆、对 CF 友好的节点（与 sharedchat 相同逻辑）。

## 验证

```bat
curl -x http://127.0.0.1:7897 https://www.cloudflare.com/cdn-cgi/trace
curl -x http://127.0.0.1:7897 -I https://linux.do
```

期望：`loc` 非 CN 受限区；linux.do 非长时间超时。

## 可选下一步（未自动做）

- 路由器 **AdGuard Home** + CF 优选 IP 重写（全屋直连）
- WLAN 旁路 DNS 从运营商改为 `1.1.1.1`（仅未走 TUN 的设备）

## 回滚

从 `backup-dns-cf-*` 拷回对应文件，Verge 重载配置。
