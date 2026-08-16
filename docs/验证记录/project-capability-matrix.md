# 项目能力验收矩阵

日期：2026-08-16
基线：`v0.3.0-rc.2`（提交 c1a76df，Git 工作区干净）
来源证据：

- `docs/验证记录/2026-08-15-server-full-deployment.md`：服务器完整拓扑部署与验收
- `docs/验证记录/2026-08-13-compose-engineering-baseline.md`：Compose 工程环境基线
- 仓库测试集（`tests/`、`frontend/tests/`）与 `scripts/quality_gate.py` Fast Gate 定义

用途：对项目已有能力做一次完整盘点，防止"服务器跑通了但本地模式或边缘行为回退"；
为第三阶段五个核心优化领域最终回归提供待复核清单。本矩阵只记录**当前证据能支持**的状态，
未验证项一律标"待复核"，不因文档描述而宣称通过。

## 统一矩阵

| 领域 | 本地模式 | 工程模式 | 单元测试 | 集成测试 | 服务器验证 |
|---|---:|---:|---:|---:|---:|
| SQLite | 是 | 不适用 | 通过 | 通过 | 不要求 |
| PostgreSQL | 不要求 | 是 | 通过 | 通过 | 通过 |
| 本地存储 | 是 | 可选 | 通过 | 通过 | 不要求 |
| MinIO/S3 | 可选 | 是 | 通过 | 通过 | 通过 |
| 本地 Worker | 是 | 可选 | 通过 | 通过 | 不要求 |
| RocketMQ Consumer | 可选 | 是 | 通过 | 通过 | 通过 |
| Milvus | 可选 | 是 | 通过 | 通过 | 通过 |
| Chat 模型 Fallback | 是 | 是 | 通过 | 通过 | 通过 |
| 会话摘要 | 是 | 是 | 通过 | 通过 | 通过 |
| Citation/Trust | 是 | 是 | 通过 | 通过 | 通过 |

## 逐项证据

### 1. SQLite（本地模式默认存储）

- 单元测试：`frontend/tests/test_product_data.py`、`test_conversation_summaries.py`、
  `test_ingest_lifecycle.py` 等均以 `sqlite:///` 内存/临时库为运行环境，覆盖数据层与业务层。
- 集成测试：Fast Gate 的 `alembic-upgrade` 步骤使用 SQLite 全量迁移至 head（8/13 记录）；
  本地 SQLite 冒烟属第九阶段本地回归项。
- 服务器验证：不要求（服务器使用 PostgreSQL）。

### 2. PostgreSQL（工程模式默认存储）

- 单元测试：`frontend/tests/test_product_data.py` 含 `sqlalchemy.dialects.postgresql` 相关
  校验（任务抢占、Schema 校验、`SKIP LOCKED` 语义）。
- 集成测试：`tests/integration/test_postgresql_product_live.py`（需
  `DEEPSEARCHER_TEST_POSTGRES_URL`，8/13 基线已用真实 PostgreSQL 运行：空库 Alembic 全量迁移、
  真实任务抢占、落后 Schema 拒绝启动）。
- 服务器验证：通过（8/15 记录：12 张表、`alembic_version=20260814_0016`、migrate 一次性任务
  Exited(0)、双 API 共享状态）。

### 3. 本地存储（本地文件对象）

- 单元测试：`frontend/tests/test_object_storage.py` 覆盖 `LocalObjectStorage` 上传/读取/删除。
- 集成测试：本地文件冒烟属第九阶段本地回归项；S3 改造后本地模式回归为重点复核项（见下）。
- 服务器验证：不要求。

### 4. MinIO/S3（工程模式对象存储）

- 单元测试：`frontend/tests/test_object_storage.py` 覆盖 `S3ObjectStorage` 与失败路径
  （对象上传失败不创建 Document 等由该文件相关用例覆盖）。
- 集成测试：通过（8/13 基线：MinIO 上传/读取/重启后读取/删除通过；`deepsearcher-documents`
  与 `milvus-bucket` 创建成功）。
- 服务器验证：通过（8/15 记录：上传 WhatisMilvus.pdf → MinIO 对象 `knowledge-bases/<kb>/<hash>.pdf`
  → 预览 57338 B 完全匹配 → 删除 KB 后对象联动删除）。

### 5. 本地 Worker（租约 Worker）

- 单元测试：`frontend/tests/test_product_data.py` 覆盖 `claim_next_ingest_job`、
  `recover_interrupted_work`、`worker_heartbeats`。
- 集成测试：通过（8/13 基线：PostgreSQL 真实任务抢占集成测试通过；SQLite 条件更新与
  PostgreSQL 行锁语义一致性由第三阶段 3.1 复核）。
- 服务器验证：不要求（服务器使用 RocketMQ Consumer）。

### 6. RocketMQ Consumer（工程模式消息处理）

- 单元测试：`frontend/tests/test_product_messaging.py` 覆盖事务消息接口与失败路径。
- 集成测试：通过（8/13 基线 RocketMQ Compose 生命周期；8/15 服务器验证事务语义）。
- 服务器验证：通过（8/15 记录：Half Commit/Rollback 可见性、Broker 回查、ACK 不重投、
  未 ACK `delivery_attempt` 1→2、超限进 `%DLQ%`；Consumer 复用解析/Embedding/Manifest/
  索引切换代码）。

### 7. Milvus（向量库）

- 单元测试：`tests/vector_db/test_milvus.py`、`tests/embedding/test_milvus_embedding.py`。
- 集成测试：`tests/integration/test_p0_milvus_live.py`、`test_d02_milvus_versioning_live.py`
  （8/13 基线：建集/写入/查询/删除通过；重启后持久化通过）。
- 服务器验证：通过（8/15 记录：写入 2 条向量、最近邻查询 id=101、删除 id=1 后不再返回；
  重启 etcd+Milvus 后 id=101 仍可检索）。

### 8. Chat 模型 Fallback（模型路由）

- 单元测试：`tests/llm/test_routing.py` 覆盖 `RoutingLLM` 顺序 Fallback、有效首包判定、
  首包超时切换、`CircuitBreaker` CLOSED/OPEN/HALF_OPEN、半开单探测、Trace 记录。
- 集成测试：通过（2026-08-16 P0 在 core-api 容器内以真实 DeepSeek + 百炼 `qwen-plus` 双候选
  RoutingLLM 直调验证：`final_model=qwen-plus`、`fallback_reason=deepseek-v4-flash:APIConnectionError`）。
- 服务器验证：通过（2026-08-16 P0：core-api 临时启用双 Provider，主 Provider 强制不可达时真实流式
  问答端到端成功；验证后恢复单 Provider。记录见 `docs/开发记录/验证记录/2026-08-16-failover-verification.md`）。

### 9. 会话摘要（持久化摘要）

- 单元测试：`frontend/tests/test_conversation_summaries.py` 覆盖摘要滚动合并、弱依据不进入
  可信历史、摘要失败不阻塞问答等。
- 集成测试：通过（8/15 记录：Redis 摘要锁互斥 NX 二次获取为 nil、TTL 5s 后恢复；双 API
  `DEEPSEARCHER_SUMMARY_LOCK=redis`、`API_INSTANCES=2` 配置一致）。
- 服务器验证：通过（8/15 记录配置级验证；并发单摘要行为在本地基线已验证）。

### 10. Citation/Trust（引用与可信层）

- 单元测试：`tests/test_trust.py`、`test_grounding.py`、`tests/evaluation/test_citation_span.py`、
  `test_trust_consistency.py`、`test_risk_profile.py`、`test_provenance.py` 等。
- 集成测试：通过（Fast Gate 内含 trust-consistency / citation-span / entailment /
  risk-profile / trust-provenance 门禁，8/13 记录 Fast Gate 全绿）。
- 服务器验证：通过（8/15 记录：真实链路答案 3599 字、10 条 Citation、状态 succeeded；
  Manifest 与索引切换一致）。

## Fast Gate 基线（8/13 记录）

- Python 测试 991 项通过；前端测试 35 项通过；浏览器 E2E 2 项通过。
- Alembic 全量升级通过；Trust/风险/Provenance 报告门禁通过；MkDocs 构建通过。
- 11 项依赖外部服务的测试按既有条件跳过（跳过原因见各测试文件）。

## 第三阶段待复核清单（对应计划 3.1-3.5）

以下各项在矩阵中"通过"来自现有测试/记录，阶段三需逐一回归确认未回退：

1. **PostgreSQL/Alembic**：空库从零迁移、Schema 落后拒绝启动、启动不改 Schema、
   SQLite 兼容升级、行锁 vs 条件更新语义一致、两个 Worker 不并发抢占同一任务。
2. **Local/S3 存储**：统一存储接口、S3 保存 Bucket/Object Key、兼容旧 `storage_path`、
   失败路径无孤儿对象、删除 KB 时 DB/对象/向量联动清理、SHA-256/大小/配额未回退。
3. **RocketMQ 事务消息**：Half 发送、本地事务 Commit/Rollback、Broker 回查、ACK/未 ACK 重试、
   超限死信、重复消息不破坏完成状态、900s 不可见期自愈。
4. **Chat 模型路由**：单模型旧配置兼容、多候选顺序 Fallback、有效首包判定、
   首包超时切换、熔断状态机、Trace 记录最终模型与 Fallback 原因。
5. **会话摘要**：摘要覆盖明确消息 ID、滚动合并、弱依据过滤、Redis 锁绑定用户与会话、
   锁超时恢复、未获锁实例跳过本轮。

## 已知缺口（不声称通过）——2026-08-16 刷新

- 已由 P0（2026-08-16）关闭：多实例一体化故障切换六场景（`deploy/server/verify-failover.sh`）在
  服务器 `47.96.40.156` 全 PASS；Chat 双 Provider 真实 fallback（`final_model=qwen-plus`）实测通过。
  记录见 `docs/开发记录/验证记录/2026-08-16-failover-verification.md`。
- 已由 `4d0cf90` 关闭：空环境/重复部署/失败中断恢复（verify-deploy）、备份恢复（backup/restore-check）、
  4 小时观察（0 告警 12/12 healthy）。
- 待办：P1-4.2 用户反馈闭环（MessageFeedback）尚未实施；产品 API 尚未将 LLM fallback 的
  model/fallback_reason 透出到消息/SSE/Trace。

## v0.5.0 Team Trust（2026-08-16）

- 工作区/成员三角色：通过（access service + API + 前端 + 迁移 0018）。
- 实时授权（每次 KB read/write 重算）：通过（tests/test_team_trust.py 9 项 + 团队 API 端到端）。

## v0.5.1 per-KB 覆盖与成员组（2026-08-16）

- per-KB 差异化角色（KB override）：通过（service/API/前端 + DB 复合 FK + 并发行锁）。
- 成员组（Group 批量授权）：通过（service/API/前端 + DB 复合 FK）。

## v0.4 起始纵切：知识健康（2026-08-16）

提交：`ff5fbcf`（feat: add v0.4 knowledge health starting slice）
来源证据：`tests/test_knowledge_health.py`、`frontend/product/services/knowledge_health.py`、
`frontend/product/models.py` 的 `KnowledgeHealthSnapshot`、迁移 `20260816_0017`、
`docs/roadmap/trustworthy-rag-roadmap.md` 的 v0.4 已实现纵切。

| 能力 | 本地模式 | 单元测试 | API 冒烟 | 前端 |
|---|---:|---:|---:|---:|
| 数据健康（文档可用性/失败/空文档/索引/时效覆盖） | 是 | 通过 | 通过 | 通过 |
| 检索健康（真实问答在线观测，样本不足不评分） | 是 | 通过 | 通过 | 通过 |
| 回答可信度（声明支持/冲突/无效引用/一致性/语义矛盾） | 是 | 通过 | 通过 | 通过 |
| 快照持久化与历史对比（相对上一快照 delta） | 是 | 通过 | 通过 | 通过 |

### 证据

- 单元测试：`tests/test_knowledge_health.py` 12 项覆盖空库 0 分、全可用 100 分、失败/缺索引扣分、
  样本不足（<3）不参与总分、拒答扣分、声明扣分、快照持久化与 delta 对比；Python 全量
  911 passed、6 skipped。
- API 冒烟：认证后创建知识库、真实文档与 3 条 grounded 问答，`GET /api/knowledge-bases/{id}/health`
  返回 complete 100 分；`POST .../health/snapshot` 生成 `khs_` 快照；`GET .../health/history` 返回 1 条。
- 前端：知识库详情页“知识健康”面板（综合分、三维评分条、指标明细、扣分原因、建议动作与快照历史）；
  类型检查、4 文件 35 项测试与生产构建通过。

### 已知边界（不声称通过）

- 检索健康与回答可信度是基于真实问答的在线观测，不是金标评测；样本不足时明确提示而不是猜测分数。
- 公式权重属于契约，变更必须升级 `HEALTH_FORMULA_VERSION`（当前 v1.0）。
- 数据健康当前不含同系列重复版本、时间重叠、断代、冲突与孤立文档检测（ADR 0009 预告的后续迭代）。

## v0.4 深化：知识健康公式 1.1（2026-08-16）

- 数据健康 series 检测（重复/重叠/断代/取代异常/孤立），异常按关系/分组扣一次，penalty 封顶 40。
- 检索健康归因（最近样本未引用文档、按 query_type 聚类的拒答/证据不足，纯统计）。
- 等级 healthy/warning/critical 与趋势接口；建议动作闭环（retry/reindex/upload）。
- 测试：tests/test_knowledge_health.py 29 项；frontend/tests 128 项；前端 35 项 + 构建通过。

## P1-4.1 操作审计（2026-08-16，对齐 ragent BizChangeLog）

- `operation_audit_logs` 表 + Alembic `20260817_0020`；`services/audit.py`：`record_operation` /
  `page_audit_logs` / `@audit_operation` 装饰器（before/after JSON 快照 + JSON-pointer diff）/
  `AuditContext` contextvar（操作者/IP/UA/request_id）。
- 埋点：ACL 10 个服务函数（工作区成员/角色、KB 覆盖、成员组）+ 用户创建/知识库删除/健康建议执行；
  admin-only `GET /api/admin/audit-logs`（分页 + 过滤）与前端 `/admin/audit` 审计页。
- 验证：`frontend/tests/test_product_api.py` 审计 e2e 4 项；全量 pytest 1064 passed、11 skipped；
  前端 typecheck/build/35 项通过；Alembic 空库升级至 `20260817_0020` 成功。
  记录见 `docs/开发记录/验证记录/2026-08-16-operation-audit-verification.md`。
