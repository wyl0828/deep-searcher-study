# 变更记录

本项目按持续维护的开发线记录面向使用者和贡献者的重要变化。条目描述的是已进入当前工作树的功能与验证材料；
未标注正式版本号的内容仍应在合并、测试与发布标签完成后再视为稳定发布。

格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本号采用语义化版本的意图，而非把尚未完成的
评测或外部服务验证包装成发布承诺。

## [Unreleased]

### 新增

- 增加分项可启动的工程 Compose 拓扑，统一 PostgreSQL、Redis、MinIO、RocketMQ、Milvus、双 API 与
  双 Consumer；本地端口只绑定回环地址，并提供配置校验、分组启停和 API 冒烟脚本。
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

- 入库任务在 PostgreSQL 使用 `FOR UPDATE SKIP LOCKED` 抢占，在 SQLite 保持原有条件更新，二者维持相同
  的单任务单 Worker 业务语义。
- DeepSearch 默认迭代数由 3 调整为 2；候选、重排、回答证据、单段证据和反思上下文均改为有界输入，
  跨迭代去重查询、文档位置与正文，简单且证据充分的查询可确定性跳过反思。
- 固定 Prompt 使用稳定 system 前缀，动态问题和证据位于后续消息；缓存命中/未命中按阶段记录，
  不设置脱离具体阶段的全局缓存率门禁。
- Full Gate 回答阶段对齐 `max_iter=2`，Benchmark 从 Trace 汇总真实 LLM 调用数，不再因 Provider
  代理未递增旧 `calls` 字段而错误报告为 0。

### 验证

- 使用真实 RocketMQ 5.3.2 Broker/Proxy 完成官方 Python 5.1.1 客户端 Spike：Commit、Rollback、
  Broker 回查、未 ACK 重试、ACK 后停止投递，以及长耗时消费的可见期续租均通过。

- PostgreSQL Schema 校验与抢占分支单测进入 Fast Gate；提供通过 `DEEPSEARCHER_TEST_POSTGRES_URL` 显式
  启用的真实 PostgreSQL 迁移/抢占集成测试，本地未配置 PostgreSQL 时不会伪造执行结果。
- Fast Gate、956 项 Python 测试和 DeepSeek Entailment live 校准通过；Entailment 校准准确率
  `0.9683`、稳定率 `1.0`、危险假阳性 `0`。
- 24 题真实回答复测平均 Token 为 NaiveRAG `2098.46`、DeepSearch `4211.04`、ChainOfRAG
  `5239.92`，相对旧基线分别下降约 `29.8%/72.2%/42.8%`；`5000` 为 ChainOfRAG 预期目标，
  不是硬发布门禁。
- Full Quality Gate 保留一个已知质量观察：ChainOfRAG Claim Support `0.625 < 0.65`；未修改既有
  tolerance，相关报告不宣称为完整门禁通过。

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
