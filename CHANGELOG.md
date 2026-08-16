# 变更记录

本项目按持续维护的开发线记录面向使用者和贡献者的重要变化。条目描述的是已进入当前工作树的功能与验证材料；
未标注正式版本号的内容仍应在合并、测试与发布标签完成后再视为稳定发布。

格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本号采用语义化版本的意图，而非把尚未完成的
评测或外部服务验证包装成发布承诺。

## [Unreleased]

### 新增

- 增加 v0.4 Knowledge Health 起始纵切：知识健康分不是黑盒分数，每次快照保存公式版本、三维得分、
  原始指标、扣分原因与建议动作。数据健康覆盖文档可用性、失败/空文档、索引状态与业务日期覆盖率；
  检索健康与回答可信度基于真实问答的引用覆盖、拒答率、证据不足率、声明支持率、无效引用率、一致性
  冲突与语义矛盾率（样本不足时该维度不参与总分并明确提示）。
- 增加知识健康快照表与 Alembic 迁移 20260816_0017，支持生成快照、历史对比（与上一快照的 delta）
  和 API：GET /knowledge-bases/{id}/health、POST /knowledge-bases/{id}/health/snapshot、
  GET /knowledge-bases/{id}/health/history。
- 产品工作台知识库详情页增加“知识健康”面板：综合分、数据/检索/可信三维评分条、指标明细、扣分原因、
  建议动作和快照历史。

### 验证

- 新增 tests/test_knowledge_health.py 12 项：空库、全可用、失败/缺索引、样本不足、拒答扣分、
  声明扣分、快照持久化与 delta 对比；Python 全量 911 passed、6 skipped。
- API 冒烟验证：认证后创建知识库、真实文档与 3 条 grounded 问答，GET health 返回 complete 100 分，
  POST snapshot 生成 khs_ 快照，history 返回 1 条；前端类型检查、4 文件 35 项测试与生产构建通过。

## [v0.3.0] - 2026-08-16

### 新增

- 增加分项可启动的工程 Compose 拓扑，统一 PostgreSQL、Redis、MinIO、RocketMQ、Milvus、双 API 与
  双 Consumer；本地端口只绑定回环地址，并提供配置校验、分组启停和 API 冒烟脚本。
- 增加服务器部署文件：compose.server.yaml（与 compose.yaml 叠加，仅 API 绑定服务器回环
  18700/18701、引擎诊断 18702，中间件不发布端口，日志轮转 10 MB × 3 与 CPU 限制）、
  .env.server.example、deploy/server/ 打包/上传/构建/分组启停与状态、冒烟脚本，以及
  deploy/nginx/deepsearcher.conf.example 反向代理模板和服务器部署与验收文档。
- 增加可选 PostgreSQL 产品数据库支持；正式 PostgreSQL Schema 只允许通过 Alembic 升级，服务启动时
  校验当前 revision，SQLite 继续保留本地自动建表和旧库兼容升级。
- 增加统一 PDF 对象存储接口，保留本地实现并提供可选 S3/MinIO/RustFS 实现；上传、Worker 读取、
  原文预览、索引重建和删除共用 Bucket/Object Key 语义，且不把对象上传宣称为消息事务的一部分。
- 增加可选 RocketMQ 5.x 事务入库模式，以官方 Python 客户端保护 Document/IngestJob 状态迁移与
  处理消息投递；本地轮询 Worker 继续作为默认模式，不引入 Outbox 或自定义消息状态表。
- 增加可选的有序 Chat 候选路由、有效首包探测与进程内 `CLOSED/OPEN/HALF_OPEN` 熔断；单模型配置
  保持兼容，Trace 记录最终实际模型和脱敏 fallback 原因。
- 增加可选持久化滚动会话摘要，保存覆盖消息、实际模型和 Prompt 版本；上下文组合最新摘要与近期可信
  原文，多 API 摘要模式强制使用带超时的 Redis 会话锁。
- 增加 Provider-neutral `ChatOptions`、完整 `TokenUsage`、调用前 Token 估算和按阶段 Trace；DeepSeek
  V4 Thinking 通过 `extra_body` 显式启停，reasoning 作为输出 Token 子集统计，不重复计入总量。
- 增加查询级 LLM 调用、输入、输出、reasoning 预算以及最终回答/必需 Trust 预留；Provider usage
  缺失时使用本地估算约束预算，并在调用后用真实 usage 校正。

### 变更

- Compose 应用容器共享受控入库临时卷，使 Consumer 从 S3 物化的短期文件可由 Core API 读取；处理完成
  后仍立即删除，不将临时卷作为长期存储或扩大消息事务边界。
- 工程启动脚本从被 Git 忽略的 Provider 环境文件注入模型配置，并拒绝空值与示例占位符。
- Citation 主键由 40 扩到 48 字符并增加 Alembic `20260814_0016`，容纳 `citation_` 前缀 UUID。
- Playwright 本地 E2E 默认端口移到 18766，并支持 `DEEPSEARCHER_E2E_PORT` 覆盖，避开 Windows
  动态保留端口段。
- 入库任务在 PostgreSQL 使用 `FOR UPDATE SKIP LOCKED` 抢占，在 SQLite 保持原有条件更新，二者维持相同
  的单任务单 Worker 业务语义。
- DeepSearch 默认迭代数由 3 调整为 2；候选、重排、回答证据、单段证据和反思上下文均改为有界输入，
  跨迭代去重查询、文档位置与正文，简单且证据充分的查询可确定性跳过反思。
- 固定 Prompt 使用稳定 system 前缀，动态问题和证据位于后续消息；缓存命中/未命中按阶段记录，
  不设置脱离具体阶段的全局缓存率门禁。
- Full Gate 回答阶段对齐 `max_iter=2`，Benchmark 从 Trace 汇总真实 LLM 调用数，不再因 Provider
  代理未递增旧 `calls` 字段而错误报告为 0。

### 验证

- Compose `infra` 分组完成真实 PostgreSQL、Redis、MinIO 健康检查、重启恢复与停止清理；应用镜像完成
  多阶段构建和运行期依赖/前端产物自检，构建上下文不再包含本地虚拟环境、私有学习资料和缓存。
- Compose RocketMQ 分组完成权限初始化、事务 Commit/Rollback、Broker 回查、ACK/重试、长耗时续租、
  最大重试进入 DLQ 与 Broker 重启恢复验证；分组停止后保留消息命名卷。
- Compose Milvus 分组完成真实向量写入、查询、删除、全组件重启恢复和应用镜像环境变量连接验证；
  项目既有 Milvus/Citation/Manifest/Collection 版本集成测试全部通过。
- Compose 双 API 分组完成共享认证、知识库、会话、MinIO 原文预览、单实例停止接管、应用重启恢复与
  Redis 摘要锁并发验证；同一会话仅生成一条有效摘要且锁在完成后释放。
- 本地完整拓扑完成真实 PDF 上传、指定 Consumer 处理、Embedding/Milvus 入库、跨 API 原文预览、
  真实问答、10 条 Citation/Trust 持久化、应用重启恢复和 PostgreSQL/MinIO/Milvus 联动删除。
- 使用真实 RocketMQ 5.3.2 Broker/Proxy 完成官方 Python 5.1.1 客户端 Spike：Commit、Rollback、
  Broker 回查、未 ACK 重试、ACK 后停止投递，以及长耗时消费的可见期续租均通过。
- 阿里云 ECS 完整拓扑部署与验收：Storage/Vector/Messaging 分组真实验证、双 API 双 Consumer
  真实链路（上传→事务消息→解析/Embedding→Milvus→查询引用→原文预览）、故障恢复（API/Consumer
  接管、消息重投自愈、中间件重启恢复）与 Nginx 反向代理接入全部通过；记录见
  docs/验证记录/2026-08-15-server-full-deployment.md。

- PostgreSQL Schema 校验与抢占分支单测进入 Fast Gate；提供通过 `DEEPSEARCHER_TEST_POSTGRES_URL` 显式
  启用的真实 PostgreSQL 迁移/抢占集成测试，本地未配置 PostgreSQL 时不会伪造执行结果。
- Fast Gate、956 项 Python 测试和 DeepSeek Entailment live 校准通过；Entailment 校准准确率
  `0.9683`、稳定率 `1.0`、危险假阳性 `0`。
- 24 题真实回答复测平均 Token 为 NaiveRAG `2098.46`、DeepSearch `4211.04`、ChainOfRAG
  `5239.92`，相对旧基线分别下降约 `29.8%/72.2%/42.8%`；`5000` 为 ChainOfRAG 预期目标，
  不是硬发布门禁。
- Full Quality Gate 保留一个已知质量观察：ChainOfRAG Claim Support `0.625 < 0.65`；未修改既有
  tolerance，相关报告不宣称为完整门禁通过。

- 阿里云 ECS 停机重启后恢复验证（2026-08-16）：公网 IP 变更后本地脚本与 `.env.server` 同步新 IP；
  core-api runtime 重启修复、Consumer gRPC 重启恢复、Nginx `/docs`/`/openapi.json` 公网封堵
  （应用层保留 OpenAPI 契约，仅 Nginx 层 404）均验证通过；记录见
  docs/验证记录/2026-08-16-server-recovery-and-verification.md。
- 服务器连续 4 小时观察（08:22-12:23）：191 个采样 12/12 容器 healthy、0 告警、内存 3.5-3.9GiB
  无上涨、磁盘稳定 53%、日志 4 小时仅增约 2M；观察结束后真实业务链路复验通过（答案 4970 字、
  10 条 Citation、预览 57338B）。记录见 docs/验证记录/2026-08-16-resource-boundary.md。
- 部署可重复性验证（独立 Compose 项目名）：空环境部署（Storage→Alembic→Vector→Messaging→
  Application 12 容器 healthy）、重复部署幂等（数据卷/数据库/容器不受影响）、失败中断恢复
  （坏 env 的 migrate Exited(1) 被捕获，修复后从失败阶段续跑成功）全部通过；记录见
  docs/验证记录/2026-08-16-deploy-repeatability.md。
- 数据备份与恢复验证：`deploy/server/backup.ps1` 生成 pg_dump/MinIO 对象/配置快照备份包；
  `restore-check.ps1` 恢复验证 PASS（PostgreSQL 11 张业务表全量恢复、MinIO 对象 1:1、Milvus
  卷存在、测试资源清理）。
- 修复 RocketMQ Broker healthcheck 超时缺陷：`mqadmin clusterList` 在 4 核并发下实测约 11.4s，
  超过原 `timeout: 8s` 导致 broker 被误标 unhealthy（生产 broker 亦受影响）；改为
  `interval: 15s / timeout: 30s / start_period: 60s` 后 broker 正常 healthy；
  `tests/test_compose_contract.py` 13 项通过。
- 本地 Fast Gate 全绿（2026-08-16）：1000 项 Python 测试、35 项前端测试、2 项 E2E、Alembic
  全量升级、Trust/Citation/Entailment/Risk/Provenance 门禁、MkDocs 构建全部通过；本地轻量模式
  （SQLite + Local 存储 + 本地 Worker）真实启动冒烟通过。

## [0.3.0-rc.1] - 2026-08-13

### 新增

- 建立版本化 Trust Layer：引用结构、确定性一致性、时效性、风险约束、可选语义蕴含、回答策略与
  去密 Trust Provenance 分别建模，并贯穿查询、Trace、SSE、持久化与离线 Benchmark。
- 提供 Citation Span Mapper：Claim 的引用可映射到模型实际看到的证据快照；精确、句级近似与未定位状态
  分开表达，持久化前校验偏移、引用文本和 Evidence ID。
- 引入请求级时间语义与 Freshness Policy：对“当前”“最新”“近期”等问题使用文档业务日期和显式
  `version_family` 保守判断；缺少可信锚点、范围或版本系列时不臆测有效性。
- 引入服务端 Query Risk Profile：对财务、制度合规、权限和健康安全等决策请求提高证据与语义核验门槛，
  客户端不能降低风险等级。
- 增加可插拔 Entailment Checker。其默认关闭，直到真实模型在人工金标集上完成阈值校准；失败、超时和
  低置信结果明确标为 `unknown`，不会伪装为已核验。
- 增加文档业务时间与版本系列字段，并在上传、持久 Worker、索引、Citation、Manifest 和谱系间保持一致。

### 变更

- 回答策略以模型实际看到的有界 Evidence 快照为前提：部分有依据的回答移除无效或明确冲突 Claim；
  完全无依据时交付保守拒答，并保留策略处理前的审计状态。
- 工作台中的知识库、文档入库、问答与引用核验路径增强了可恢复 Worker、SSE 阶段事件、文档删除同步和
  可解释的失败分类。
- 检索与回答评测统一记录质量、拒答、引用、延迟和 Token；Hybrid/RRF 对比与默认 Agent 选择保留
  可复现报告，而非只依据主观体验调整。

### 质量与验证

- 新增并纳入快速质量门禁的人工数据集：确定性 Trust Consistency、Citation Span、Entailment 契约、
  Query Risk Profile 和 Trust Provenance 不变量。
- 快速质量门禁覆盖 Python 静态检查与测试、前端测试/类型检查/构建、Chromium E2E、全新 SQLite 迁移、
  评测报告校验、MkDocs 构建和 `git diff --check`；完整档额外重建真实评测 Collection。

### 已知边界

- Citation 结构与字符定位不等于语义蕴含；Entailment 的真实模型质量必须通过 `--mode live` 评测证明。
- “最新”仅在最终 Evidence 快照的同一 `version_family` 内判断，不替代全库召回、外部网页归档或连接器同步完整性。
- 当前开发线尚未声明为通用生产 SLO。基线报告只覆盖固定数据集与明确的运行配置。
- Entailment 真实校准已通过严格门槛，但仍默认关闭；模型、Prompt/Checker 或 Gold 版本变化后必须重跑。
- 72 题×3 默认产品路径达到 Hybrid Recall@8 87.44%、MRR 71.07%、Grounded Coverage 82.35%、
  多文档完整覆盖 62.5%，三类稳定率均为 100%；全量 decomposition 未胜出，继续默认关闭。
- 上下文 Hybrid Recall 达到 90%；24 题×3 中 Naive Coverage 达到 85.00%，三 Agent 错误率均为 0。
- 2026-08-13 的 Full Quality Gate 已通过。回答集曾因 Docker Desktop 恢复期间出现 2 次超时和 4 次
  `VectorDBUnavailable`；获准 checkpoint 重试后均恢复，最终 `recovered=6`、`still_failed=0`。

## 变更证据

- 版本路线和每项能力的验收边界：[docs/roadmap/trustworthy-rag-roadmap.md](docs/roadmap/trustworthy-rag-roadmap.md)
- 架构决策和安全约束：[docs/adr/](docs/adr/)
- 运行命令、数据集范围与基线报告：[evaluation/README.md](evaluation/README.md)
- 上游来源与许可：[UPSTREAM.md](UPSTREAM.md)
