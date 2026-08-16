# 对标 Ragent 的企业级缺口优化计划（阶段计划）

> 日期：2026-08-16
> 状态：P0 已验证（2026-08-16）；P1-4.1 操作审计已实现；P1-4.2 用户反馈待实施
> 给后续会话：本文件是"本项目后续整体优化计划"的唯一权威入口。新会话先读本文件，
> 再按阶段执行；每阶段完成后把验证记录补进 `docs/开发记录/验证记录/` 并更新本文件状态。
> 所有"参考 ragent"均指向本地源码 `D:\code\reference\ragent`（参考提交 `020e5c3`），
> 路径以该仓库根为相对根。**禁止脱离 ragent 源码自行发明 demo 级设计**；如某能力 ragent
> 未闭环（如 agent 模块），本计划也不提前建设。

---

## 1. 背景：为什么要做

已用 ragent 本地源码（Java 17 + Spring Boot 4，7 个 Maven 模块，rag 512 个 Java 文件）
与本项目做逐能力比对，结论：本项目在 **Trust Layer（引用核验/一致性/时效/风险/蕴含/
Provenance）、知识健康、版本化金标评测、文档级 ACL** 上深度领先 ragent；但在"企业级 RAG
知识库问答"的**面**上存在 8 类缺口。

缺口清单（按本计划处理顺序）：

1. 文档解析/分块：缺 Excel/PPT/图片/CSV 专用解析，分块一刀切（ragent 有 Excel/Tika/
   MinerU 解析 + Code/Table/Image/Heading/List/Paragraph 6 类分块器）
2. 检索智能：缺知识图谱通道、树形意图/歧义检查、查询词映射（ragent 有 GraphSearchChannel、
   IntentTreeService、QueryTermMapping）
3. 入库可编排 + 定时刷新：固定链路、无远程源调度（ragent 有 IngestionEngine 节点引擎 +
   ScheduleRefreshProcessor 租约调度）
4. 运营管理后台：无 KPI 大盘/配置管理（ragent 有 admin/DashboardService + AgentProfile）
5. 系统级操作审计：无"谁改了什么权限"的审计（ragent 有 BizChangeLog）
6. 用户反馈闭环：无点赞/点踩（ragent 有 MessageFeedback MQ 异步闭环）
7. 多节点流量治理：无共享模型并发上限/公平排队（ragent 有 FairDistributedRateLimiter）
8. MCP/工具生态：无（ragent 有 McpToolRegistry + mcp-server）

**明确不做（后置）**：知识图谱通道、树形意图、MCP、Agent 工具调用。原因：工程量大，且与
"可信 RAG"主线弱相关；ragent 自身的 agent 模块目前也只有 `pom.xml`，未闭环，按项目纪律
不提前建设。

---

## 2. 阶段总览

| 阶段 | 名称 | 解决缺口 | 预计 | 产出 |
|---|---|---|---|---|
| P0 | 收尾已知缺口 | 验证未闭环 | 1-2 天 | failover/fallback 验证记录 |
| P1 | 可信合规闭环 | 审计 + 反馈 | 1 周 | OperationAuditLog + MessageFeedback |
| P2 | 企业文档能力 | 解析/分块 + 可编排入库 | 1-2 周 | 多格式 loader + 节点式入库 |
| P3 | v0.6 连接器 | 外部源持续同步 | 2-3 周 | LocalDirectoryConnector + 调度刷新 |
| P4 | 运营管理 | Dashboard | 1-2 周 | 管理大盘 |
| P5 | 流量治理（按需） | 多节点并发控制 | 按需 | Redis 公平排队 |

建议顺序：P0 → P1 → (P2 | P3) → P4，P5 仅在真实出现多节点并发额度需求时启动。

---

## 3. P0 收尾已知缺口（✅ 已验证 2026-08-16）

### 目标
把"脚本已写好但从未执行"的验证关掉，为后续改动提供可信基线。

### 任务
1. E.1 多实例故障切换：`deploy/server/verify-failover.sh|.ps1` 六场景在服务器
   `root@47.96.40.156` 上执行。注意脚本场景 1/4/6 的断言目前是 TODO 注释，需补真实 API
   调用（上传→轮询 ready→查询 citation；重复投递后逻辑 ID 集合不增长；restart 后计数一致）。
2. Chat Fallback 真实双 Provider 切换：`.env` 配双 Provider，制造首包超时/4xx，确认 Trace
   的 `final_model` 与 fallback 原因。

### 验收
- 新增 `docs/开发记录/验证记录/2026-08-XX-failover-verification.md`，六场景全 PASS。
- 新增双 Provider fallback 验证记录。
- 过程中发现的问题就地修复并记录（参考上次 RocketMQ healthcheck 超时修复模式）。

**实现记录（2026-08-16）**：
- `deploy/server/verify-failover.sh` 已补全六场景机器断言（场景 1 上传→轮询 ready→API-B 查询 citation；
  场景 4 事务消息重投后文档/作业/向量逻辑 ID 集合不增长；场景 6 重启前后计数一致），
  并修复 `set -e` 中断、consumer 短 ID 匹配、pymilvus `num_entities`、TRANSACTION topic 重投、
  consumer rebalance 等待、core-api 就绪预检/恢复等问题。
- 服务器 `47.96.40.156` 完整执行：`VERIFY-FAILOVER: PASS`（六场景全 PASS）。
- Chat 双 Provider fallback：core-api 临时启用 `llm.candidates`（DeepSeek 主 + 百炼 `qwen-plus` 备），
  主 Provider 强制不可达后，RoutingLLM 返回 `final_model=qwen-plus`、`fallback_reason=deepseek-v4-flash:APIConnectionError`；
  真实流式消息端到端成功（fully_grounded / 10 citations）。验证后恢复单 Provider 配置。
- 详见 `docs/开发记录/验证记录/2026-08-16-failover-verification.md`。

---

## 4. P1 可信合规闭环（1 周）

### 4.1 操作审计（✅ 已实现 2026-08-16）

**参考 ragent**（禁止自行设计替代品）：
- `system/src/main/java/com/nageoffer/ai/ragent/audit/dao/entity/BizChangeLogDO.java`：
  字段即审计模型——`id/bizType/bizId/operationType/actionDesc/beforeSnapshot/afterSnapshot/
  changeDiff/operatorId/operatorName/operatorRole/success/errorMessage/className/methodName/
  ip/userAgent/createTime`。
- `BizChangeLogRecordService implements ILogRecordService`（bizlog-sdk）：`record(LogRecord)`
  落库。
- `BizChangeLogContext`：ThreadLocal 变量 + SpEL 表达式（`bizChangeBizId/bizChangeSnapshot/
  bizChangeSkip/bizChangeName`），`put(bizId, before, after)` / `skip()`。
- `BizChangeLogServiceImpl.page`：分页查询，支持按 bizType/bizId/operationType/operatorId/
  operatorName/success/时间范围过滤，按 createTime 倒序。

**本项目落地（Python 等价）**：
- 新表 `OperationAuditLog`（`models.py` + 迁移 `20260817_0020_operation_audit_log.py`），
  字段对齐 BizChangeLogDO（去掉 Java 专有 className/methodName，保留 bizType/bizId/
  operationType/actionDesc/before/after JSON/operatorId/operatorName/success/errorMessage/
  request_id/ip/user_agent/create_time）。
- 新增 `frontend/product/services/audit.py`：`record_operation(...)` + `page_audit_logs(...)`
  （复用现有分页模式，等价 BizChangeLogServiceImpl.page）。
- 埋点（Python 无 bizlog 注解，用装饰器 `@audit_operation(biz_type=..., before=..., after=...)`
  包住服务函数，事务内同库落审计）：
  - `services/access.py`：`add_workspace_member / set_member_role / remove_workspace_member /
    add_kb_member / set_kb_member_role / remove_kb_member / create_group / add_group_member /
    delete_group / promote_member_to_owner`
  - `routes.py` 的 `admin/users` 创建/删除、`knowledge-bases` 删除、健康 `actions/run`
- API：`GET /api/admin/audit-logs`（admin-only，分页 + 过滤，等价 BizChangeLogController）。
- 前端：`App.tsx` 加 `/admin/audit` 页。

**验收**：改一个成员角色 → 审计出现 before/after 完整快照；非 admin 访问 403；
`frontend/tests/test_product_api.py` 审计 e2e 通过；全量 pytest + 前端 build 绿。

**实现记录（2026-08-16）**：
- 新表 `operation_audit_logs`（`models.py::OperationAuditLog`，字段对齐 BizChangeLogDO 去 className/methodName）+ 迁移 `20260817_0020_operation_audit_log.py`。
- `frontend/product/services/audit.py`：`record_operation`（等价 BizChangeLogRecordService.record，含字段截断与 REQUIRES_NEW 语义）、
  `page_audit_logs`（分页 + bizType/bizId/operationType/operatorId/operatorName/success/时间范围过滤 + createTime 倒序，等价 BizChangeLogServiceImpl.page）、
  `audit_operation` 装饰器（等价 @LogRecord + BizChangeLogContext，生成 before/after 快照与 JSON-pointer diff）、
  `AuditContext` contextvar（操作者/IP/UA/request_id，由 `auth.optional_user` 绑定，等价 UserContext + HttpServletRequest）。
- 埋点：`services/access.py` 10 个 ACL 函数（add/set/remove_workspace_member、add/set/remove_kb_member、create_group、add_group_member、delete_group、promote_member_to_owner）；
  `routes.py` 的 `admin/users` 创建、`knowledge-bases` 删除、`health/actions/run`。
- API `GET /api/admin/audit-logs`（admin-only，分页 + 过滤，等价 BizChangeLogController）+ 前端 `/admin/audit` 页（分页/过滤/快照与 diff 展开）。
- 验证：`frontend/tests/test_product_api.py` 新增 4 个审计 e2e 全过；全量 pytest 1064 通过；前端 typecheck/build/单测 35 通过；Alembic 空库升级至 `20260817_0020` 成功。
  详见 `docs/开发记录/验证记录/2026-08-16-operation-audit-verification.md`。

### 4.2 用户反馈闭环

**参考 ragent**：
- `rag/.../rag/service/impl/MessageFeedbackServiceImpl.java`：`submitFeedbackAsync` 校验
  userId/messageId/vote(1/-1)/reason/comment → 构造 `MessageFeedbackEvent` → MQ 异步发送
  （keys=`userId:messageId`）；另有 `cancelFeedbackAsync` 支持取消。
- `rag/.../rag/mq/MessageFeedbackConsumer.java`：RocketMQ 监听 → `submitFeedbackByEvent`
  异步落库（不阻塞主链路）。

**本项目落地**：
- 新表 `MessageFeedback`（迁移 `0021_message_feedback.py`）：message_id/user_id/vote/reason/
  comment/cancelled/created_at，`(user_id, message_id)` 唯一（等价 keys 幂等）。
- API：`POST /conversations/{id}/messages/{message_id}/feedback`（body: vote=1|-1,
  reason?, comment?）直接落库；重复提交更新原记录（幂等）。同步落库即可，不引入 MQ——
  除非要跨节点，再参考 ragent 走 RocketMQ。
- 消费进健康：`services/knowledge_health.py` 的 `compute_retrieval_health` 把最近负面反馈
  并入样本（新增 flag 聚合）。
- 前端：回答尾部 👍/👎 按钮。

**验收**：点 👍/👎 落库且幂等；健康报告含反馈样本；`tests/test_knowledge_health.py` 扩展
反馈用例通过。

---

## 5. P2 企业文档能力（1-2 周）

### 5.1 多格式解析与分块

**参考 ragent**：
- `rag/.../core/parser/excel/ExcelDocumentParser.java`：`OPT_SOURCE_FILE/OPT_HEADER_ROWS`
  选项，`parseStructured` 把 Sheet 转结构化 Block（行级）。
- `rag/.../core/parser/mime/MimeTypeDetector.java`、`core/parser/registry/ParserRegistry.java`：
  MIME 识别 + 解析器注册表分发。
- `rag/.../core/chunk/blockaware/TableChunker.java`：表格**整体成块**，渲染为 Markdown 表格
  或 key-value 行（`renderKeyValueRows`/`renderMarkdownTable`）。
- `CodeChunker.java` / `ImageChunker.java` / `HeadingChunker.java` / `ListChunker.java` /
  `ParagraphChunker.java` + `BlockAwareChunkerDispatcher`。

**本项目落地**：
- `deepsearcher/loader/file_loader/` 新增 `excel_loader.py`、`pptx_loader.py`、
  `image_loader.py`：优先复用现有 docling/unstructured 包装，Excel 走结构化行级块
  （对齐 ExcelDocumentParser 的 headerRows 选项）。
- `loader/splitter.py` 新增 `TableAwareSplitter`（表格整体成块）与 `CodeBlockSplitter`。
- 分发：`services/documents.py` 的 `_dispatch_ingest_or_mark_failed` 按 MIME 选 loader
  （对齐 ParserRegistry）。
- 测试：`tests/loader/file_loader/test_excel_loader.py`、`test_pptx_loader.py`、
  `test_image_loader.py` + `test_splitter.py` 扩展。

**验收**：上传 xlsx/pptx/图片能进 KB 且可检索；表格不被拦腰截断；全量 pytest + 前端绿。

### 5.2 入库可编排（对齐 IngestionEngine，不做完整 DAG）

**参考 ragent**：
- `rag/.../ingestion/engine/IngestionEngine.java`：`nodeMap` + `ConditionEvaluator` +
  `NodeOutputExtractor`；`execute(PipelineDefinition, context)` 流程 =
  `validatePipeline → findStartNodes → executeChain → executeNode`。
- `ingestion/node/IngestionNode.java` 接口 + `ParserNode/ChunkerNode/EnhancerNode/
  EnricherNode/FetcherNode/IndexerNode`。
- `ingestion/domain/pipeline/IngestionPipelineNodeDO`（节点配置持久化）。

**本项目落地（Python 节点抽象，不引入完整 DAG 引擎）**：
- 新 `frontend/product/services/ingestion_pipeline.py`：`IngestionNode` 协议 + 具体节点
  `ParseNode/ChunkNode/EmbedNode/IndexNode`，每节点 `execute(context)` 返回 NodeResult，
  `execute_chain(pipeline_config, context)` 顺序执行（对齐 executeChain）。
- `models.py` `IngestJob` 加 `pipeline_steps`（JSON：节点配置，含 enabled）；
  `services/documents.py` 的 `_finish_ingest_job` 改由引擎驱动，单节点失败可单独 retry
  （复用现有 `claim_next_ingest_job` 状态机）。
- 测试：`tests/test_ingest_lifecycle.py` 加"配置不同 steps / 跳过某步 / 单步重试"用例。

**验收**：入库链路按配置执行节点、单步可重试、跳过某步不影响其余；旧配置（无
pipeline_steps）兼容默认链路。

---

## 6. P3 v0.6 本地目录连接器 + 定时刷新（2-3 周）

### 参考 ragent
- 源类型抽象：`rag/.../rag/core/retrieval/channel/SourceType.java` 枚举
  `FILE("file")/URL("url")/FEISHU("feishu")`；`rag/core/source` 的
  `DocumentFetcher` 接口（`FetchResult fetch(DocumentSource source)`）+ 实现
  `HttpUrlFetcher/RemoteFileFetcher/FeishuFetcher`（FeishuFetcher 走
  `tenant_access_token` 认证 → 拉 docx → 解析）。
- 定时刷新：`rag/.../knowledge/schedule/ScheduleRefreshProcessor.java`——读
  `document.scheduleCron` + `sourceType=URL` → `CronScheduleHelper.nextRunTime(cron, now)`
  → 执行刷新（fetch→替换→重索引），阶段推进前检查租约是否丢失。
- 调度锁：`ScheduleLockManager.java`——`tryAcquire/renew/release` + 心跳线程 +
  `instancePrefix`（多实例防重） + TTL；`ScheduleStateManager/ScheduleStateContext` 状态机。
- 调度实体：`KnowledgeDocumentScheduleDO`、`KnowledgeDocumentScheduleExecDO`。

### 本项目落地
- 新 `frontend/product/connectors/`：`Connector` 协议（对齐 DocumentFetcher + 四条同步）：
  `list_items() / fetch_item(id) / detect_changes(cursor) / fetch_permissions(path)`；
  实现 `LocalDirectoryConnector`（本地目录/SMB 挂载，用 mtime/大小做增量，对齐
  `RemoteFileFetcher` 的语义）。
- `models.py`：`Document` 加 `external_id/connector_source`，`IngestJob` 加
  `source/cursor`；新增 `ConnectorSync` 调度表 + `ConnectorSyncRun` 执行表
  （对齐 KnowledgeDocumentScheduleDO/ExecDO），迁移 `0022_connector_sync.py`。
- 调度：`services/connector_sync.py` 内 DB 租约锁（复用现有 WorkerHeartbeat/任务抢占模式，
  语义对齐 ScheduleLockManager：acquire/renew/release + 心跳）+ cron 解析（可引入
  `croniter`，对齐 CronScheduleHelper）。
- 删除同步：源删除 → 墓碑 → 对象/向量联动清理（复用现有删除链路）。
- 权限同步：目录 ACL 快照 → 映射 `services/access.py` 的 `add_kb_member`（复用 v0.5 ACL）。
- 测试：临时目录 fake 源集成测试（添加/修改/删除 → 断言 KB 文档/向量/ACL 跟随）+
  API e2e。

**验收**：本地目录四条同步（initial/incremental/delete/permission）各有一条集成测试 +
API e2e；调度幂等（锁丢失不重复处理）；完成即 v0.6 纵切闭环。

---

## 7. P4 运营管理后台（1-2 周）

### 参考 ragent
- `rag/.../admin/controller/DashboardController.java` + `admin/service/DashboardService/
  DashboardServiceImpl.java`。
- VO：`DashboardOverviewVO`（含 `DashboardOverviewGroupVO`、`DashboardOverviewKpiVO`）、
  `DashboardPerformanceVO`、`DashboardTrendsVO`（含 `DashboardTrendPointVO`、
  `DashboardTrendSeriesVO`）。

### 本项目落地
- 新 `frontend/product/services/dashboard.py`：
  - `overview()`：文档总数/ready 率/入库失败数/知识库数/会话数/问答消息数/用户数/健康分布
    （对齐 DashboardOverviewVO + KPI）。
  - `trends(days)`：近 7/30 天按日聚合入库量/问答量/负反馈量（对齐 DashboardTrendsVO）。
- API：`GET /api/admin/dashboard/overview`、`GET /api/admin/dashboard/trends`（admin-only）。
- 前端：`App.tsx` 加 `/admin` 概览页（健康分布 bar + 趋势线，沿用 workspace.css 风格）。
- 测试：`frontend/tests/test_product_api.py` dashboard e2e（数字与 DB 一致）。

**验收**：admin 可见大盘且数字与 DB 一致；viewer/非管理员 403。

---

## 8. P5 流量治理（按需，不做不算缺口）

### 参考 ragent
- `rag/.../rag/service/ratelimit/FairDistributedRateLimiter.java`（494 行）：Redisson
  信号量（semaphoreKey）+ ZSet 公平队列（queueKey/queueSeqKey）+ Lua 脚本
  `rag/src/main/resources/lua/queue_claim_atomic.lua` 原子认领；租约/心跳 poll/entry
  marker 清理僵尸队头；`State {PENDING, GRANTED, TIMED_OUT, CANCELLED}` 票状态机。

### 本项目落地（触发条件）
- 仅在真实出现"多 API 实例共享模型额度"需求时实施。
- 位置：`deepsearcher/llm/routing.py` 旁新增 `redis_semaphore.py`（对齐信号量+ZSet 队列，
  Python 用 Redis Lua 脚本等价 queue_claim_atomic.lua）；`services/conversations.py` 流式
  入口接入。

**验收**：N 实例共享同一限流，突发时后到请求排队而非拒绝；有 Lua 原子性测试。

---

## 9. 给后续会话的执行须知

1. 每阶段开工前：先打开对应 ragent 参考类读实现，再对照本文件落地清单做 Python 等价改写；
   禁止跳过参考直接写。
2. 每阶段完成：跑 `scripts/quality_gate.py` 全量门禁（Python/前端/迁移/门禁），
   写 `docs/开发记录/验证记录/2026-XX-...md`，并把本文件该阶段状态改为"已实现"。
3. 新增迁移必须走 Alembic 编号递增（当前 `20260816_0019`）。
4. 若 ragent 某能力在参考提交中未闭环（如 agent 模块只有 pom），不自行补强。
5. 服务器相关操作默认目标 `root@47.96.40.156`（公网 IP 已更新，旧 118.178.234.18 已失效）。
