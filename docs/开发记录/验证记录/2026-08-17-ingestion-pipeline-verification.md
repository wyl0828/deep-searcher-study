# P2-B 入库可编排验证记录

> 日期：2026-08-17
> 阶段：对标 Ragent 企业级缺口优化计划 P2-B（入库可编排，对齐 IngestionEngine，不做完整 DAG）
> 参考：`D:\code\reference\ragent`（提交 `020e5c3`）的
> `ingestion/engine/IngestionEngine.java` / `ingestion/node/`（IngestionNode/ParserNode/ChunkerNode/IndexerNode）/
> `ingestion/domain/`（NodeResult/NodeConfig/PipelineDefinition）
> 结论：✅ 全部通过

---

## 1. 目标

把"固定链路"（上传 → load-files → 索引）改为**可编排的节点链**：节点配置持久化、
链式顺序执行、节点可独立启停/失败定位、重试保留配置。明确本项目"后端服务模型"边界：
**Parse/Chunk/Embed 节点只做本地校验与参数归一化，真正入库仍是原子 `load-files` 调用**。

## 2. 契约（吸收开工前评审的 5 项修正）

1. **IndexNode 为强制终结节点**：`validate_pipeline` 约束恰好一个 Index、`enabled=true`、`next_node_id=None`、所有节点最终可达。
2. **`enabled=false` 真实含义**：仅跳过本地校验/参数归一化；后端 `load-files` 对应阶段仍按默认执行；"不分块"语义由 `chunk_size=-1` 表达（哨兵合法、归一化记录）。
3. **`_finish_ingest_job` 所有权唯一**：节点从不改 job/document 终态；`process_*` 通过 `_finalize_ingest` 统一调用一次。
4. **节点异常统一归一化**：`execute_node` 捕获全部异常 → `NodeResult.fail`，`failure` 保留 `{node_id, node_type, code, message, retryable}`。
5. **`terminate()` 与成功分离**：链可 terminate；生命周期成功以 `context.manifest` 已产生为准，否则视为未形成有效入库结果。

## 3. 实现清单

| 项 | 文件 | 说明 |
|---|---|---|
| 引擎 | `frontend/product/services/ingestion_pipeline.py` | `NodeResult`（ok/skip/fail/terminate）、`NodeConfig`、`IngestionContext`、`validate_pipeline`（非空/唯一 id/已注册类型/settings 对象/唯一 start/next 存在/无环/全可达/恰一 Index/Index terminal 且不可禁用）、`execute_chain`/`execute_node`（异常归一化）、`ParseNode`（LoaderRegistry 路由校验）/`ChunkNode`（chunk_size 含 `-1` 哨兵、overlap 校验）/`EmbedNode`（batch_size 校验）/`IndexNode`（唯一一次 `_load_document_into_backend` + 置 manifest）、`default_pipeline_steps`（parse→chunk→embed→index） |
| 模型 | `models.py::IngestJob` | `pipeline_steps`（JSON）+ `pipeline_version`（String）；迁移 `20260817_0022_ingest_pipeline_steps.py` |
| 状态机 | `documents.py` | `_pipeline_config_for_document`（复用最近 job 配置，默认四节点）；`create_ingest_job` 三处调用带配置；`process_claimed_ingest_job`/`process_rocketmq_ingest_job` 改为 `execute_chain` + `_finalize_ingest`（唯一 finish 所有权）；`_finish_ingest_job` 支持 `failure_detail`（节点级诊断入 error_message）；`_load_document_into_backend` 增 `request_params` 扩展点（不透传未确认字段） |
| SQLite 兼容 | `db.py` | `ensure_ingest_pipeline_columns`（旧本地库 ALTER 补列，项目 ensure_* 惯例） |

## 4. 验证矩阵

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | validate：默认链通过；空/重复 id/未知类型/settings 非对象/无或双 Index/Index 禁用/Index 有 next/多起点/next 缺失/环/不可达 全部拒绝 | ✅ | `tests/test_ingest_pipeline.py` 8 个 validate 用例 |
| 2 | execute_chain：四节点顺序执行 + manifest；`enabled=false` 跳过不断链；节点失败链停止 + `failed_node` 定位；异常归一化（`NODE_INDEX_FAILED`）；`terminate` 停止且不产生 manifest | ✅ | `tests/test_ingest_pipeline.py` 5 个执行用例 |
| 3 | Chunk 设置：`-1` 哨兵合法、`0` 非法（`NODE_CHUNK_INVALID_SETTINGS`） | ✅ | `test_chunk_settings_validation` |
| 4 | 生命周期：上传 job 持久化默认四节点 + version；`retry_document` 保留自定义 steps/version | ✅ | `frontend/tests/test_ingest_pipeline_steps.py` 2 用例 |
| 5 | 全量 pytest | ✅ | 1122 passed，11 skipped |
| 6 | 完整 quality_gate | ✅ | `scripts/quality_gate.py` 全 18 步 PASS（含 alembic 空库升级至 `20260817_0022`） |

## 5. 过程中发现并就地修复

- **本地 SQLite 旧库缺新列**：`Base.metadata.create_all` 不补既有表列，`recover_interrupted_work` 查询报 `no such column: ingest_jobs.pipeline_steps`；按项目 `ensure_*` 惯例加 `ensure_ingest_pipeline_columns`。
- **默认 steps 缺连线**：`default_pipeline_steps` 的 parse/chunk/embed 初版未设 `next_node_id`，validate 判定 4 个起点；补 parse→chunk→embed→index 连线。
- **head revision 断言**：`test_required_alembic_revision_matches_repository_head` 更新为 `20260817_0022`。
- **terminate 测试误改连线**：初版测试把 embed 的 `next_node_id` 置 None 导致多起点；移除该误改（terminate 注入在节点 execute 层）。

## 6. 结论

P2-B 已闭环：`IngestJob` 持久化可编排的 `pipeline_steps`（四节点链），`validate_pipeline` 全量结构校验，
`execute_chain` 链式执行并支持跳过/失败定位/异常归一化；`_finish_ingest_job` 所有权唯一，`terminate` 无 manifest
不视为成功；重试保留步骤配置；旧文档无配置时默认四节点、行为与现状一致；全量门禁 18 步全绿。

## 7. 遗留

- **参数透传**：`chunk_size/chunk_overlap` 的归一化当前仅本地记录（`request_params` 扩展点已就绪），因 `load-files` 端点契约未确认，未透传；端点确认后可在 `ChunkNode` 写入 `request_params`。
- **P3 v0.6 本地目录连接器 + 定时刷新** 未开始，属下一阶段（连接器拉取文件将复用本批 `LoaderRegistry` 与入库链路）。
- 管道 CRUD API / 独立 Task 表留给 P4 运营后台需要时；不做完整 DAG/条件表达式/后端断点协议。