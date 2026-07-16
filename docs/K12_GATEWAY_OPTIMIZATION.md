# K12 Gateway 性能优化记录

> 2026-07-14：针对 80k 账号 SQLite 网关的性能加固

## 根因

`DatabaseStorageBackend._save_rows()` 每次 API 请求都执行 **DELETE ALL + ORM 逐行 INSERT 80k 行**。
`mark_text_used()` / `mark_image_result()` 在每个请求结束时调用 `_save_accounts()`，
导致 `/health` 端点首次 30s 超时、watchdog 频繁误报。

## 改动清单

### 1. SQLite UPSERT + PRAGMA (`services/storage/database_storage.py`)

- **DELETE-all → UPSERT**：用 `sqlite_insert().on_conflict_do_update()` 批量 UPSERT，
  只更新变化的行，不再全表重建。
- **增量删除**：用临时表 + `NOT IN` 删除已移除的账号，避免变量数限制。
- **PRAGMA 优化**（通过 SQLAlchemy `connect` event listener）：
  - `journal_mode=WAL`（并发读写不阻塞）
  - `synchronous=NORMAL`（WAL 下安全且快）
  - `busy_timeout=10000`（10s 锁等待）
  - `cache_size=-131072`（128MB 缓存）
  - `temp_store=MEMORY`
- **check_same_thread=False**：让后台 flush 线程和请求线程共用引擎。

### 2. 保存防抖 + 后台 flush (`services/account_service.py`)

- `_save_accounts()` 改为只设 `_save_dirty = True`（标记脏），不再立即 I/O。
- 后台线程 `_save_flush_loop` 每 4s 检查脏标，锁内取快照、锁外写盘。
- `_save_accounts_now()` 用于关键操作（删除账号等）强制立即落盘。
- `shutdown()` 在 FastAPI lifespan 结束时做最后一次 flush。

### 3. 账户评分 + 加权选号 (`get_text_access_token`)

- 新增 `text_success` / `text_fail` 字段，持久化到 DB。
- 选号分两层：healthy（`text_fail < 3` 或失败不多于成功）→ degraded（失败较多但仍可选）。
- 优先从 healthy 池 round-robin，degraded 池兜底。
- `mark_text_fail()` 在空响应/超时/token 失效时记录。
- **2026-07-16（社区/官方）**：healthy 内再按 tier 优先  
  `0=plus/go/pro+RT → 1=any+RT → 2=other no-RT → 3=k12 snapshot no-RT`，  
  避免无 RT 的 k12 快照占满 round-robin 导致 Codex 首跳 401。

### 4. 请求重试扩展 (`services/protocol/conversation.py`)

- 异常分支从仅 `is_token_invalid_error` 扩展到：
  - 429 / rate limit
  - timeout / timed out
  - connection reset / aborted / RemoteDisconnected
- 所有可重试异常都会 `mark_text_fail` + rotate 到下一号。

### 5. 每日 SQLite 快照备份 (`scripts/k12_daily_backup.py`)

- 用 SQLite online backup API 做 WAL-safe 运行时快照。
- 可配为计划任务，默认保留 14 天。

### 6. authconv 集成 (`_tools/authconv/`)

- 安装了 [ltxgit/authconv](https://github.com/ltxgit/authconv) CLI。
- 支持凭据互转：CPA ↔ sub2api ↔ codex2api ↔ Codex auth.json。
- 全局命令 `authconv`。

## 性能对比

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| `/health` 首次 | 30s 超时 | 2-3s |
| `/health` 并发 | 21s | 0.5-2s |
| 每请求 DB I/O | 80k 行 DELETE+INSERT | 标脏（0 I/O） |
| 持久化频率 | 每请求 | 每 4s |
| journal_mode | delete | WAL |

## 回滚

如果 UPSERT 或防抖出现问题，回滚方法：
1. `_save_rows` 回退到 DELETE-all + ORM INSERT（`_orm_replace_all` 分支）。
2. `_save_accounts` 改回直接调用 `self.storage.save_accounts()`。
3. `STORAGE_BACKEND=json` 切回 JSON 后端（`.env` 或环境变量）。
