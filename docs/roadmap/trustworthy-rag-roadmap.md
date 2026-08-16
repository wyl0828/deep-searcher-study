# Trustworthy RAG Platform 路线图

## 产品定位

DeepSearcher Study 的目标不是扩展成另一个通用工作流平台，而是建设一套可信 RAG
平台。核心能力是 Evidence Management、Answer Verification 和 Knowledge Quality
Control。

## 维护承诺与核验方式

本路线图是持续维护的验收契约，而不是功能愿望清单。每个已实现纵切都应具备：可追溯设计决策、
自动化测试或人工金标、可复现命令，以及对失效模式的明确说明。当前开发线的功能记录在根目录
`CHANGELOG.md`；上游来源、许可和贡献边界见 `UPSTREAM.md`。

| 维护信号 | 当前做法 |
| --- | --- |
| 功能范围 | 以 ADR 定义 Trust、Citation Span、Entailment、Risk、Provenance、Freshness 与版本系列的契约和边界。 |
| 质量回归 | 通过快速质量门禁运行 Python/前端/E2E/迁移、人工金标和报告校验；真实模型质量另行显式评测。 |
| 发布透明度 | 未稳定的能力保持在 `Unreleased`，不以数据集结构校验替代模型效果结论。 |
| 后续取舍 | 每个版本以可验证的问题为验收条件；不满足条件时保留保守策略，而非默认放宽验证。 |

主数据流：

```text
Document -> Index -> Retrieval -> Evidence -> Claim
         -> Verification -> Answer -> Quality Feedback
```

## 版本路线

| 版本 | 名称 | 核心验收问题 |
| --- | --- | --- |
| v0.3 | Trust Layer | 系统能否识别风险声明，并真正改变交付给用户的回答？ |
| v0.4 | Knowledge Intelligence | 系统能否解释知识库质量为什么发生变化？ |
| v0.5 | Team Trust | 未授权知识是否在检索前彻底不可见？ |
| v0.6 | Enterprise Connect | 数据、删除和权限能否持续与源系统一致？ |
| v1.0 | Enterprise Ready | 系统能否在多人、多机、故障和审计要求下稳定运行？ |

## v0.3 Trust Layer

### 领域组件

```text
Trust Layer
├── Citation Validator
├── Claim Extractor
├── Evidence Mapper
├── Consistency Checker
├── Entailment Checker
├── Answer Policy Engine
└── Trust Trace
```

### 当前已实现的纵切

- Trace v6 提供独立、版本化的 `trust` 契约，并绑定去密 Trust Provenance。
- Citation、Entailment、Consistency、Safety 和 Policy 分开建模。
- 明确区分引用结构、确定性一致性、Citation Span 定位与可选语义蕴含。
- 保存输入回答和策略处理后回答的 Trust 状态。
- 部分有依据时删除缺少有效引用的声明。
- 完全无依据时交付保守拒答。
- 明确披露冲突证据，不自动选择其中一方。
- 只有保存了“模型实际看到的证据快照”时才执行策略；旧调用仅观察，不静默改写。
- Message 和 AnswerClaim 持久化 Trust/Policy 与后续 Checker 所需字段。
- 确定性 Consistency Checker 校验数字/单位、完整日期，以及能够严格对齐同一命题的否定冲突。
- 明确不一致的 Claim 即使引用编号有效，也会降为 unsupported 并进入删除或拒答策略。
- 策略处理前的原始错误 Claim、检查值和 reason code 会保存到 Trust Trace 和消息审计摘要，
  不会因最终回答被改写而丢失。
- Evaluation Pipeline 与线上共用 `finalize_answer`，并独立版本化 Trust Metric；报告包含策略前
  Claim 支持率、一致性冲突率和策略改写率。
- 新增不依赖模型的人工标注 Trust Consistency Gold Dataset，将版本、范围方向、条件省略和
  已知语义边界纳入快速质量门禁。
- Consistency Checker 1.1 将条件规范化为关系、原子项和否定状态：支持单层 AND/OR、复合前置
  条件与“除非”例外，区分条件缺失、关系偷换和显式否定。
- Trust Consistency Gold Dataset 扩展到 27 例；复杂条件关系、前置条件、权限枚举和例外条件进入阻断门禁，
  前端将内部关系签名翻译为可读解释。
- Consistency Checker 1.2 将数量和范围值绑定到同一事实子句中的受控实体类别，阻断“相同数字、
  不同对象”的假支持；存在正确实体证据时允许同值干扰项，未知实体则保守回退而不强判。
- Trust Consistency Gold Dataset 扩展到 32 例，新增实体错配、正确证据与干扰项并存、未知实体回退
  和跨 Citation 联合支持；前端区分“值不存在”和“值存在但对象不一致”。
- Consistency Checker 1.3 建立请求级 Temporal Context：使用显式 UTC 时刻和 IANA 时区解析日、周、
  月、季度与年份相对表达，支持跨日界线和跨年换算，并将时间值绑定到受控实体。
- Evidence 中没有文档时间锚点的相对表达、或缺少请求时钟的回答统一成为 unknown 并 fail-closed；
  Trust Trace、消息持久化和前端显示实际参考日期/时区。
- Trust Consistency Gold Dataset 扩展到 41 例，新增相对时间匹配/冲突、实体错配、正确干扰项、
  跨 UTC 日界线、跨年季度及双重缺失锚点。
- Claim 的每个 Evidence ID 都生成相对于实际证据快照的 `citation_spans`；规范化精确命中、
  句级近似和未定位分别建模，前端点击 Claim 引用后高亮对应原文。
- Citation Span 在持久化前重新校验偏移、quote 与 Citation.text，非法或污染结果不会进入产品库。
- 独立 Citation Span Gold Dataset 覆盖精确命中、OCR/空白归一化、句级改写、多引用分布支持、
  数字冲突、无关证据和已知语义边界，并作为快速质量门禁阻断步骤。
- 新增可插拔 Entailment Checker：确定性冲突先行，只有与完整证据句等价的 Claim 才进行零成本
  短路，其余 Claim 在一次有界批量调用中返回 entailed/contradicted/unknown。
- 高置信 contradicted 会进入现有 downgrade/refuse 策略；低置信、缺失结果、非法 JSON、超时或
  Provider 失败统一记录为 unknown，标准策略保留回答并向用户披露不确定性。
- Checker Token 纳入线上响应、Trace 和离线 Benchmark 的同一成本口径；模型调用失败不泄露
  Provider 异常或证据内容。
- 人工 Entailment Gold Dataset 已建立；快速门禁只验证数据集与契约，真实模型准确率必须显式运行
  live 评测，禁止用 Stub 结果冒充语义质量。
- 新增服务端 Query Risk Classifier。只有敏感领域与决策意图同时出现才提升为 high，避免架构学习
  问题因包含“权限/审计/报销”等词被误判。
- high 风险 Claim 必须获得明确 Entailment；unknown/not_checked 不能继续交付。额度、期限、比例、
  剂量等定量决策还要求至少两个 Evidence 和两个独立来源，同一文档多个 Chunk 只算一个来源。
- 风险结果进入 Claim、Answer Policy、Trace、Message、前端解释和 Trust Metric；客户端没有降低
  risk_level 的请求字段，风险宇宙由服务端定义。
- Risk Profile Gold Dataset 覆盖高风险决策题、即时安全题、学习型敏感词问题和普通数值题，并
  作为快速质量门禁。
- 新增 Trust Provenance v2（兼容读取 v1）：每次回答绑定运行时、去密配置、生成模型、Embedding、显式索引
  Manifest、Prompt、Checker、Risk 与 Policy 版本，并生成规范化 SHA-256 摘要。
- Collection 名称、Tenant ID、原始问题、完整 Prompt 和密钥不进入谱系；动态路由选库前不枚举
  整库并标记为 `dynamic_unbound`，选库后合并各轮实际 Manifest、重新生成摘要。
- Provenance 进入同步 Trace、SSE、离线 Benchmark、Message 持久化和前端审计摘要；嵌套注入与
  摘要篡改在持久化前被白名单清洗器拒绝。
- v2 绑定最终回答模型实际看到的 Evidence 顺序、内容/来源/定位指纹和 Web Provider/trusted 状态；
  网页仅表示搜索 snippet 快照，不冒充网页全文版本。
- Provenance Builder 2.2 将相对时间参考日期、时区及其指纹纳入总摘要；参考日期变化会改变摘要，
  时间字段与 Trust Trace 不一致时拒绝持久化。
- Trust Provenance 人工不变量集扩展为 18 项，覆盖顺序稳定、密钥轮换、模型漂移、动态范围、路由
  后绑定、多轮合并、混合 Evidence、Web 去泄露、内容/顺序漂移、时间身份与篡改检测。
- Trust Metric 1.4 输出 Provenance 覆盖率、Evidence/Temporal 身份绑定率、相对时间 unknown/拒绝率
  和 Web 快照数量，使文档时间元数据建设能够被量化验证。
- Consistency Checker 1.4 引入显式文档业务时间：`published_at/effective_at/superseded_at` 从产品库、
  上传/编辑 API、持久 Worker、Loader、Chunk 和 Citation 快照贯穿到 RetrievalResult；只有可信来源
  的 `published_at` 能锚定 Evidence 内“今天/明天/下周”等相对表达，文件 mtime 与上传时间永不代替。
- 修改已就绪文档的业务日期会创建新一轮持久化任务并更新索引；日期参与 Document/Data Manifest
  指纹，避免只改数据库而继续查询旧向量元数据。产品页可在上传和文档管理中声明、查看与修改日期。
- Trust Consistency Gold Dataset 扩展到 45 例，新增发布日期匹配/冲突、生效日期不能冒充发布锚点、
  Connector 可信来源；Trust Provenance 不变量扩展到 20 项，证明日期身份进入 Evidence 摘要且变化可检测。
- Trust Metric 1.4 进一步输出最终知识库 Evidence 的发布日期绑定数量与覆盖率，网页 Evidence 不进入
  分母；产品 Trust 谱系同时显示本次有多少证据绑定了发布日期。
- Freshness Policy v1.0 在请求入口区分当前有效、最新生效、最新发布与未定义“近期”窗口；使用请求
  日期和显式业务日期检查尚未生效、已经失效、引用旧版本及排序覆盖不完整，unknown/inconsistent
  均进入现有 downgrade/refuse 策略，原问题不会进入 Profile 或审计摘要。
- Grounding Prompt 1.1 把经过白名单验证的业务日期作为 Evidence 属性提供给模型，同时禁止用上传
  时间推断；Consistency Checker 升级到 1.5，Trust Metric 1.5 输出时效意图、unknown 与拒绝率。
- Trust Consistency Gold Dataset 扩展到 55 例，其中 10 例专门覆盖 Freshness；Provenance Builder
  2.2 绑定 Freshness Classifier 版本，不变量扩展到 21 项。产品页显示本次时效模式和具体拒绝原因。
- 新增受控 `version_family` 文档系列身份，并从上传/编辑 API、持久 Worker、Loader、Chunk、Manifest、
  Citation 快照贯穿到 Trust Layer；系列标识规范化，来源仅接受 user-declared、connector 或
  admin-verified，修改已就绪文档会触发重新索引。
- Consistency Checker 1.6 将“最新生效/最新发布”限制在被引用 Evidence 的同一系列；缺少系列、
  Claim 混用多个系列时 fail-closed，其他系列中时间更新的资料不再导致误拒绝。Grounding Prompt 1.2
  明确禁止模型猜测系列。
- Trust Consistency Gold Dataset 扩展到 58 例，其中 13 例覆盖 Freshness；Trust Metric 1.6 输出
  Evidence 文档系列覆盖率，Provenance Builder 2.3 以去密指纹绑定系列，不变量扩展到 23 项；产品
  Trust 谱系显示本次有多少知识库证据绑定了文档系列。

### 后续迭代

1. Entailment Gold 1.1.0 已扩展到 63 条并在 `deepseek-v4-flash` 上完成三次真实校准；Checker 1.2
   的推荐阈值为 0.90，Accuracy 96.30%、Macro F1 96.38%、contradicted Recall 100%、危险误判 0、
   稳定率 98.41%。能力继续显式启用，模型、Prompt/Checker 或数据集版本变化后必须重新校准。
2. LLM Judge 只复核 NLI unknown 与低置信疑难项，不作为唯一裁判。
3. 基于真实业务误判样本扩展 Risk Profile，并增加仅允许管理员“提升而不能降低”风险的受控覆盖。
4. 多文档默认产品路径已达到 62.5% 完整文档覆盖、Recall@8 87.44%、MRR 71.07% 和 Grounded
   Coverage 82.35%；共享 document-aware decomposition 的全量实验未胜出，继续默认关闭并保留诊断能力。
5. 建立 Retrieval Recall 与连接器同步完整性门禁；对需要全文研究的 Web Evidence 增加受控网页归档，并为跨信任域
   审计增加可选服务端签名。

### 建议质量门槛

| 指标 | 首个目标 |
| --- | ---: |
| 无效引用率 | 0% |
| Claim Support Rate | >= 90% |
| 无答案拒答准确率 | >= 90% |
| 数字一致性 | >= 98% |
| Citation Span 命中率 | >= 90% |
| Trust 判断与人工标注一致率 | >= 85% |

## v0.4 Knowledge Intelligence

### 已实现的纵切（2026-08-16 起始）

- Knowledge Health 公式 v1.0：综合分 = 数据健康 40% + 检索健康 30% + 回答可信度 30%；
  每个快照持久化公式版本、三维得分、原始指标、扣分原因与建议动作，总分不是黑盒分数。
- 数据健康：文档可用比例（ready/total）、失败文档、空文档、索引已验证状态、业务日期覆盖率；
  空库得 0 分并提示上传。
- 检索健康（在线观测，非金标）：最近 50 条已完成回答的引用覆盖率、平均引用数、拒答率、
  证据不足率与联网引用占比；样本少于 3 条时不评分并提示先积累问答。
- 回答可信度：声明支持率、冲突率、无效/缺失引用率、一致性冲突率、语义矛盾率；声明少于
  3 条时不评分。
- 快照与对比：支持生成快照、历史列表与相对上一快照的 delta；产品工作台知识库详情页提供
  “知识健康”面板。

### 后续迭代

- 数据健康 series 检测（2026-08-16 已实现，公式 1.1）：同系列重复版本、时间重叠、断代、
  取代异常与孤立文档；异常按关系/分组扣一次，series penalty 封顶 40，局部抑制不遮蔽其他问题。
- 检索健康归因（已实现）：最近样本中未被引用的 ready 文档、按问题类型聚类的拒答/证据不足归因，
  纯统计、不引入 LLM，样本数与占比一起展示。
- 健康等级与趋势（已实现）：healthy/warning/critical 机器码与阈值由服务端唯一函数计算，
  快照趋势接口与前端等宽 bar 展示变好/变坏。
- 建议动作闭环（已实现）：扣分对应的建议动作可点击执行（重试失败文档、重建索引、去上传），
  执行后即时重新快照；delta 不隐含异步完成。

待办：

- 知识健康金标评测：评测集至少覆盖单文档事实、跨文档综合、冲突资料、无答案/证据不足和对抗问题。
- 阈值告警与推送通知；series 语义冲突检测（ADR 0009 预告）。

## v0.5 Team Trust

最小模型为 Organization、Workspace、KnowledgeBase、Document，以及 User、Group、Role、
Permission。权限定义用户可见的知识空间，必须在 Vector/BM25 检索之前进入过滤条件；
Retriever、Rerank、Cache、Trace 和 Agent Tool 均不得接触未授权证据。

### 已实现的纵切（2026-08-16，v0.5.0 最小闭环）

- Workspace + WorkspaceMember 固定三角色（owner/editor/viewer），知识库归属工作区并最终
  `workspace_id NOT NULL`；名称唯一性迁移到工作区级。
- 三层 access service：`user_workspace_role` / `require_workspace_access` /
  `require_accessible_knowledge_base`，权限用集合表达。
- **每次检索前实时授权**：普通/流式问答、会话创建、预览/下载、健康等所有重读 KB 的入口，在读取
  Cache、构造 Retriever、取得 collection_name 之前按当前成员状态重新 read 授权；会话创建授权不作
  凭据；已授权运行中的流不中途鉴权。
- 403/404 语义：不存在/非成员 → 404（不泄露存在性），成员角色不足 → 403。
- 单 owner 数据库级防线（PostgreSQL partial unique index）+ 服务层约束（owner 不可 PATCH/DELETE）。
- 工作区管理 API 与前端页面：创建/列表、成员添加/改角色/移除；知识库创建选择工作区；viewer 隐藏
  上传/删除/重建/生成快照等写入口。
- 自动个人工作区迁移：现有用户各得个人工作区，legacy 数据归首个 active admin（无 admin 用
  `__legacy__` 真实用户）；`claim_legacy_data` 同步迁移 owner 与 workspace。

### 待办（v0.5.1+）

- per-KB 差异化角色（2026-08-16 已实现）：KB 覆盖 editor/viewer，owner 永不参与；成员管理 admin-only。
- Group 成员组（2026-08-16 已实现）：工作区批量授权（editor/viewer），组不授予 admin，成员必须是
  工作区成员；四条 ACL 数据规则由复合 FK + cascade + 行锁双保险。
- 待办：Organization 顶层、工作区所有权转移与删除/重命名、Milvus 侧 ACL（当前依赖集合随机名 +
  API 白名单，检索核心零侵入）。

## v0.6 Enterprise Connect

只优先实现两个完整连接器：本地目录/SMB/NAS，以及一个企业协作数据源。每个连接器必须
同时完成 Initial Sync、Incremental Sync、Delete Sync 和 Permission Sync。

## v1.0 Enterprise Ready

企业就绪以能力验收，而不是以使用 Kubernetes 为验收：多组织隔离、审计、数据同步、
高可用、成本控制、质量监控和灾备缺一不可。部署演进顺序为单机、多进程、两台机器，
验证稳定后再进入 Kubernetes。
