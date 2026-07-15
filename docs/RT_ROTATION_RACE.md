# RT 轮换竞态（refresh-token rotation race）——判死陷阱与正确探测法

> 2026-07-15 用 7580 个号换来的教训。任何 agent 在判定「号死/RT 失效」前**必须**先读本文。

## 现象（曾误判）

一次维护把 live 从 ~5600 塌到 ~90，dead 涨到 7580。当时的错误结论是
「dead 7000+ 多数是 RT 被 xAI 吊销，救不回」。**这是错的**——实测用正确方法
抽查，这些号的 access_token 大量仍有效，是**假死**。

## 根因：RT 是一次性的，多个刷新端自相残杀

xAI 的 refresh_token（RT）**每刷新一次就轮换**：旧 RT 在刷新成功的瞬间即作废，
服务端返回新 RT。

本系统有 **6 个独立的 refresh 消费端**，全是各自独立、互不协调、无按号加锁的
计划任务：

- `scripts/cpa_keepalive.py`
- `quota_watch.py`
- `pool_health.py`
- `refresh_pool.py`
- `scripts/hard_purge_pool.py`
- `local_grok_auth.py`

竞态时序：

1. A 进程读到号的 RT_0。
2. B 进程先刷新成功：RT_0 作废，拿到 RT_1 写回文件。
3. A 用过期的 RT_0 去刷新 → 服务端 `invalid_grant`。
4. **A 若不重读文件，就以为号死了**，把它标 disabled / 搬进 `cpa_auths_dead`。

被搬走的号 AT 可能还有数小时寿命（能直接用），且文件里存的是已作废的 RT_0——
于是「号死、RT 失效」的假象就形成了。

## 三条血泪规则

1. **`invalid_grant` ≠ 号死。** 它只说明「你手里这条 RT 被消费过了」，
   很可能是别的进程刚刷过。判死前**必须重读文件**：RT 变了 = 别的进程刷过 = 号活着。
2. **探测账号死活，先测 AT（access_token），别只测 RT。** 测 RT 是破坏性操作
   （轮换会消耗它）；测 AT 是只读的。AT 能用 = 号现在就能用。
3. **AT 探测别把 URL 拼错。** 文件里 `base_url` 已是 `https://cli-chat-proxy.grok.com/v1`，
   再拼 `/v1/models` 会变成 `.../v1/v1/models` 全 404。正确地址：

   ```
   GET https://cli-chat-proxy.grok.com/v1/models
   Authorization: Bearer <access_token>
   ```
   用 `cpa_xai.probe.probe_models(at, base_url="https://cli-chat-proxy.grok.com/v1", proxy=...)`，
   它内部只做 `base.rstrip('/') + '/models'`。遇到 HTTPError 要把候选地址都试完再判死。

## 已落地的加固：`cpa_xai/raceguard.py`

```python
from cpa_xai.raceguard import rt_rotated_by_other

# 在 refresh 拿到 invalid_grant、准备 disable/move 之前：
if rt_rotated_by_other(path, tried_rt):
    # 文件里的 RT 已被别的进程轮换 -> 号活着 -> 不判死、不搬动
    skip()
```

`rt_rotated_by_other(path, tried_rt)` 重读文件比对 RT：
RT 不同 / 文件丢失 / 读不出 → 返回 True（保守：不杀）；
RT 相同 → 返回 False（真被吊销，可判死）。

已接入：`refresh_pool.refresh_one`、`cpa_keepalive.process_one`、
`quota_watch`（pool-purge）、`pool_health`（refresh 失败）、`hard_purge_pool.one`。
**新增任何 refresh 消费端，判死前都必须调它。**

## 配套闸口（config.json）

- `pool_hard_purge_move_dead = false`：默认不搬 dead，只就地软禁。
- `pool_hard_purge_scope = buffer`、`pool_hard_purge_max = 500`：限制单次面。
- `pool_maintain_hard_purge_every_hours = 24`：降低频率。

## 救回脚本（已被这次事故验证）

- `scripts/_probe_dead_at.py`：用正确 URL 按 AT 抽查 dead 号的存活率（只读、安全）。
- `scripts/_restore_dead_own.py`：把 AT 仍有效的 own 域号搬回 live（清假死标记）。
- `scripts/_recover_dead_rt.py`：RT 单次干净刷新救回；**成功必立刻落盘新 RT**
  （旧 RT 已被消耗，不落盘就丢号）。RT 真死才留 dead。
