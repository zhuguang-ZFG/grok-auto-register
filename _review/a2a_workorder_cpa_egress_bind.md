# A2A 工单：CPA 网关加固两项（P0 版本漂移监控 + P1 账号哈希出口绑定）

risk: med
owns:
  - scripts/cpa_client_version_watch.py        # 新建
  - scripts/cpa_egress_bind.py                 # 新建（回填 + 监听 YAML 生成）
  - cpa_xai/protocol_mint.py                   # 仅加 per-auth proxy 固定写入（默认关）
  - scripts/clean_cpa_proxy.py                 # 白名单放行绑定端口
  - docs/HARDEN.md                             # §7 追加一段（短）
reviewers: [atom, claude]
xreview: claude

## 背景（已取证，直接采信）

- 本机 grok 网关 = CLIProxyAPI v7.2.67（D:/cli-proxy-api/cli-proxy-api.exe），其 grok provider 走 OAuth，
  注入头含 `x-grok-client-version: 0.2.93`（上游源码 internal/runtime/executor/xai_executor.go，
  常量 xaiClientVersionValue）。xAI 若上调所需 CLI 版本，写死的旧值会整池被拒——这是 OAuth 路径唯一强指纹。
- Claude 审核裁定：x-statsig-id 是 grok.com 网页路径风控点，与本 OAuth 链路无关（不做）。
- Clash = Clash Verge + verge-mihomo，API http://127.0.0.1:9097（secret `set-your-secret`），
  mixed-port 7897；当前节点 10 个 Vless：DOGEGG流量站-zhuguang-zfg(剩余:144GB)-cdn-1 .. cdn-10。
- 风险：同一 OAuth token 的 refresh/chat 出口 IP 长期漂移（7897 轮换）= 多号滥用信号。
  方案：账号哈希固定到「listener→节点」，token 长期走同一出口。
- 既有约束（docs/HARDEN.md）：per-auth proxy 死端口曾是坑；`clean_cpa_proxy.py` 会删非 7897 的 proxy 字段。
- 全局 proxy-url 必须保持 7897 不动；注册机行为不改（注册期轮换 clash_rotate_per_account 已存在）。

## 任务 A（P0）：scripts/cpa_client_version_watch.py

- 功能：漂移监控 `x-grok-client-version`。
  1. 首次运行写基线 `logs/_cpa_client_version_baseline.json`：
     {cliproxy_version:"7.2.67", commit:"2075f77c", client_version:"0.2.93", checked_at}。
     本机版本用 `D:/cli-proxy-api/cli-proxy-api.exe --version` 实测填入（勿硬编到代码里，基线是数据文件）。
  2. 每次运行：`GET https://raw.githubusercontent.com/router-for-me/CLIProxyAPI/main/internal/runtime/executor/xai_executor.go`
     （若 main 404 则试 master），正则提取 `xaiClientVersionValue\s*=\s*"([^"]+)"`；
     再 `GET https://api.github.com/repos/router-for-me/CLIProxyAPI/releases/latest` 取 tag_name。
  3. 判定：upstream client_version != baseline.client_version 或 latest release tag != baseline.cliproxy_version
     → 打 ALERT（说明上游已动，需升级二进制并 --update-baseline），exit 1；一致 exit 0；网络错误 exit 2。
  4. `--update-baseline`：把当前实测版本/常量写进基线（升级后人工确认用）。
  5. `--quiet` 仅 exit code。纯标准库；超时 15s；全部走 HTTP 直连（此脚本是 GitHub 情报，不经 Clash）。
- 接线：pool_maintain 里每日一次（若 pool_maintain 有现成每日段则挂入；没有则脚本自带说明，不强行接线）。

## 任务 B（P1）：账号哈希出口绑定（网关侧，默认关）

1. `scripts/cpa_egress_bind.py`
   - `--ports 7911,7912,7913,7914`（默认 4 条）；`--apply` / 默认 dry-run。
   - 对 `cpa_auths/*.json`：取 email（或文件名去 .json）做 **稳定哈希**（sha1(email) % N），
     写/更新 `proxy: http://127.0.0.1:791X`；已带正确绑定端口的跳过；带 7897 的改为绑定端口；
     带其它死端口的改绑并记 warn。输出统计（bind_counts per port）。
   - `--emit-listeners`：打印 mihomo `listeners:` YAML 片段到 stdout（不写 Clash 任何文件）：
     每个端口一条，type: http，listen: 127.0.0.1，`proxy:` 固定到一个具体节点名
     （节点名从 Clash API GET /proxies 实取，优先把 4 个端口分散到 4 个不同 cdn 节点；
     API secret 读 config.json 的 clash_secret）。
   - `--verify`：GET http://127.0.0.1:791X（经 http 代理）https://ifconfig.me 看 4 条出口 IP 是否互异；
     listener 未上线时明确报「端口不通」而不是乱写。
2. `cpa_xai/protocol_mint.py`：mint 写 CPA JSON 时，若 `config.json` 新增键
   `cpa_egress_bind_enabled=true`（默认 false），按同一哈希规则写 `proxy` 字段；
   默认关时行为与现状完全一致（不写 proxy）。
3. `scripts/clean_cpa_proxy.py`：白名单从「仅 7897」扩为「7897 + 7911-7914」（端口集合做成顶部常量）。
4. `docs/HARDEN.md` §7 追加 ≤15 行：绑定原理、开关、回填命令、listener YAML 应用方式（人工 Clash Verge）、
   节点死时处理（重跑 cpa_egress_bind --apply 会哈希重排——文档注明这点）。

## gates

```gates
- python -m py_compile scripts/cpa_client_version_watch.py scripts/cpa_egress_bind.py cpa_xai/protocol_mint.py scripts/clean_cpa_proxy.py
- python scripts/cpa_client_version_watch.py        # 实跑，exit 0 或 1 都算通（exit 2 不行）
- python scripts/cpa_egress_bind.py                 # dry-run 能跑通并打印统计，不写任何文件
- python scripts/cpa_egress_bind.py --emit-listeners # 打印 4 条 listener，节点名非空
- python scripts/clean_cpa_proxy.py --dry-run 2>&1 | head   # 若该脚本无 dry-run 则跳过此条并在报告说明
- 默认配置下 mint 行为不变：grep protocol_mint.py 确认 cpa_egress_bind_enabled 缺省 false 分支不写 proxy
```

## 非目标

- 不改 CLIProxy 全局 proxy-url（7897）；不改注册机注册期轮换逻辑；
- 不直接写 Clash Verge 任何配置文件（listener YAML 只打印，由 Kimi/用户人工应用）；
- 不引入第三方依赖（纯标准库）；不做 statsig 任何事。
