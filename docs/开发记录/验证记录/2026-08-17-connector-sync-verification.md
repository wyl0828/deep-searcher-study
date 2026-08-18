# P3 v0.6 本地目录连接器 + 定时刷新验证记录

> 日期：2026-08-17
> 阶段：对标 Ragent 企业级缺口优化计划 P3（v0.6 本地目录连接器 + 定时刷新）
> 参考：`D:\code\reference\ragent`（提交 `020e5c3`）的
> `rag/core/source/DocumentFetcher` / `knowledge/handler/RemoteFileFetcher` /
> `knowledge/schedule/`（ScheduleRefreshProcessor / ScheduleLockManager / CronScheduleHelper）/
> `knowledge/enums/SourceType`
> 结论：✅ 全部通过

---

## 1. 目标

本地目录/SMB 挂载作为外部源持续同步：initial/incremental/delete/permission 四条同步 +
cron 定时刷新 + DB 租约锁调度。复用 P2 入库链路（`LoaderRegistry` + `create_ingest_job`）与
v0.5 ACL；仅 `LocalDirectoryConnector`，URL/Feishu 后置。

## 2. 契约（吸收开工前评审的 6 项修正）

1. **`external_id ≠ content_hash`**：external_id = root 相对规范化路径（稳定源身份）；content_hash = sha256（内容版本）；更新用 `replace_document_id=existing.id`（经 `IngestJob.source_metadata → request_params` 覆盖 `_load_document_into_backend` 默认的 `document.sha256`）。
2. **唯一约束按 connector 实例隔离**：`UNIQUE(connector_sync_id, external_id)`；`Document.connector_sync_id/external_id/connector_source/content_hash`；上传去重改为 partial unique（`WHERE connector_sync_id IS NULL`）。
3. **mtime+size 快速筛选、sha256 最终判定**：`detect_changes` 返回 added/modified_candidates/removed（mtime+size），sync 层 `fetch_item` 的 sha256 与已存 content_hash 比较决定 changed/skipped。
4. **ACL additive-only**：只 `add_kb_member`/`set_kb_member_role`（角色变更），**不承诺撤销**（避免扩 ACL provenance）；经 `AuditContext`（operator=kb.owner）走既有审计。
5. **调度幂等不靠提前推进 next_run_at**：`claim → 建 ConnectorSyncRun(running, scheduled_for) → 执行 → 成功才推进 next_run_at`；lease loss/failure 写 run=failed/aborted、不跨周期；靠文件级幂等（content_hash）+ cursor checkpoint。
6. **SyncRun 成功 ≠ 文件已索引**：字段 `added/updated/deleted/skipped/enqueue_failed`；`status=success` 仅表示源扫描+入队提交成功。
   - 附加：cursor 属 `ConnectorSync`（非 IngestJob）；`IngestJob.source="connector"` + `source_metadata`（connector_sync_id/external_id/content_hash/replace_document_id）。

## 3. 实现清单

| 项 | 文件 | 说明 |
|---|---|---|
| Connector | `frontend/product/connectors/`（base/local_directory/registry） | `Connector` 协议（list_items/fetch_item/detect_changes/fetch_permissions）；`LocalDirectoryConnector`（root.resolve()+相对路径防逃逸、symlink 默认不 follow、mtime+size 候选筛选）；`create_connector` 注册表 |
| 模型 | `models.py` | `Document` +4 字段 + `UNIQUE(connector_sync_id, external_id)` + partial upload 去重；`IngestJob` +source/source_metadata；新表 `ConnectorSync`（含 cursor/next_run_at/lock_owner/lock_until）+ `ConnectorSyncRun`；迁移 `20260817_0023`；`db.py::ensure_connector_sync_columns` |
| 调度 | `services/connector_sync.py` | `SyncLease`（CAS acquire/renew/release，`synchronize_session=False`）；`validate_cron`（5-field）/`compute_next_run`（Asia/Shanghai 解释、UTC 存储）；`claim_due_sync`；`process_due_sync`（changes→enqueue→delete→permissions→run 状态机，lease-loss 逐阶段中止）；`_stage_copy`→`put_staged`；`_delete_removed`（DOCUMENT_BUSY 加回 cursor 重试）；`_apply_permissions`（additive+审计） |
| 接入 | `worker.py` | `run_worker` 增加 connector sync 轮询（claim_due_sync→process_due_sync） |
| 竞态 | `documents.py` | `process_*` 校验 `source_metadata.content_hash` 与 `document.content_hash`（旧 job 丢弃为 dead_letter）；`request_params` 覆盖 replace 语义 |
| API | `routes.py` | 最小管理 API：创建/列表/trigger/runs |
| 依赖 | `pyproject.toml` | 新增 `croniter` |

## 4. 验证矩阵

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | LocalDirectory：枚举/扩展名过滤/symlink 跳过、sha256 fetch、path escape 拒绝、detect_changes 增量 | ✅ | `tests/test_connector_local_directory.py` 5 用例（`ba7816...` sha256、`../outside.txt` 逃逸拒绝、mtime+size 不变但内容变 → modified_candidates） |
| 2 | 调度：cron 5-field、compute_next_run UTC、lease CAS（过期可抢/owner 才续/释放） | ✅ | `tests/test_connector_sync_schedule.py` 3 用例 |
| 3 | 集成：process 入库（Document/IngestJob/run）、幂等不重复、cursor 推进、next_run_at 成功才推、lease 释放 | ✅ | `test_process_due_sync_ingests_and_records_run`、`test_process_due_sync_idempotent_does_not_reingest` |
| 4 | 全量 pytest | ✅ | 1131 passed，11 skipped |
| 5 | 完整 quality_gate | ✅ | `scripts/quality_gate.py` 全 18 步 PASS（alembic 空库升级至 `20260817_0023`） |

## 5. 过程中发现并就地修复

- **`utcnow` 归属**：connector_sync 误从 `db` 导入（实际在 `models`）；修正。
- **`ConnectorSync.runs` relationship 缺失**：`ConnectorSyncRun.sync` back_populates 引用的属性未定义；补上。
- **SQLAlchemy UPDATE 后 tz 评估报错**：lease 的 `update(...).where(lock_until < now)` 在 `synchronize_session` 评估时 naive/aware 比较报错；加 `execution_options(synchronize_session=False)`。
- **`ruff format tests` 误改既有 CRLF 测试**：全量 format 把 `tests/**` 既有 CRLF 文件转 LF 导致 git-diff 报 trailing whitespace；白名单还原无关文件，`git diff --check` 干净。
- **head revision 断言**：`test_required_alembic_revision_matches_repository_head` 更新为 `20260817_0023`。

## 6. 结论

P3 v0.6 已闭环：`LocalDirectoryConnector` 提供四条同步（initial/incremental/delete/permission），
`connector_sync.py` 以 DB 租约锁（CAS）+ cron（5-field/时区）+ run 状态机编排，`next_run_at` 成功才推进、
lease 防并发、content_hash 防重复副作用；`Document`/`IngestJob` 身份模型（external_id≠hash、
connector_sync_id 实例隔离）已固化；ACL additive-only、SyncRun 与 IngestJob 状态解耦；全量门禁 18 步全绿。

## 7. 遗留

- URL/Feishu 连接器后置（ragent 有实现，非 P3 主线）。
- ACL 撤销同步未做（additive-only 已写入契约）；如需全量 reconciliation 需扩 KnowledgeBaseMember provenance。
- 连接器管理前端页面留给 P4 运营后台；本批提供最小后端 API。
- 分布式锁（Redisson 级）非本批（DB 租约 CAS 已覆盖多实例防重）。