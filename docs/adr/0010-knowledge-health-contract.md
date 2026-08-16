# ADR 0010：Knowledge Health Contract

## 状态

Accepted，首版实现于 2026-08-16（v0.4 起始纵切，提交 `ff5fbcf`）。
公式 v1.1 于 2026-08-16 深化：数据健康 series 检测、检索归因、等级/趋势/动作闭环。

## 问题

系统能解释“这条回答为什么被保留、降级或拒答”，但知识库整体质量为什么变化、变化发生在数据、检索
还是回答可信度，缺少可解释的度量。直接展示若干原始指标（如失败文档数、拒答率）难以回答“该先做什么”，
而一个没有来源的“健康分”又会变成黑盒分数，无法审计。

## 决策

引入 Knowledge Health 公式 v1.1（起始纵切为 v1.0）：综合分 = 数据健康 40% + 检索健康 30% + 回答可信度 30%。
每个快照持久化公式版本（`HEALTH_FORMULA_VERSION`）、三维得分、生成得分的原始指标、扣分原因与
建议动作，保证健康分可被回放审计而不是黑盒数字。

- 数据健康：文档可用比例（ready/total）、失败文档、空文档、索引已验证状态、业务日期覆盖率；
  空库得 0 分并提示上传。数据权重内：就绪度 70%、失败率 15%、索引状态 15%。
- 检索健康：来自真实问答的在线观测（最近 50 条已完成回答），包括引用覆盖率、平均引用数、拒答率、
  证据不足率与联网引用占比；样本少于 3 条（`MIN_RETRIEVAL_SAMPLE`）时不参与总分并提示先积累问答。
  权重内：引用覆盖 60%、证据不足 20%、拒答率 20%。
- 回答可信度：来自持久化 `AnswerClaim` 的声明支持率、冲突率、无效/缺失引用率、一致性冲突率与
  语义矛盾率；声明少于 3 条（`MIN_TRUST_SAMPLE`）时不参与总分。权重内：支持率 55%、无效引用 20%、
  一致性 15%、语义矛盾 10%。

扣分与建议使用稳定代码（如 `KB_EMPTY`、`FAILED_DOCUMENTS`、`INDEX_MISSING`、
`LOW_CITATION_COVERAGE`、`HIGH_REFUSAL_RATE`、`LOW_CLAIM_SUPPORT`、`INVALID_CITATIONS`、
`CONSISTENCY_ISSUES`、`ENTAILMENT_CONTRADICTIONS` 等），便于前端展示与后续自动化消费。

### 公式 1.1：数据健康 series 检测

scope 为 `ready` 且 `version_family` 非空的文档；时间标准化
`start = effective_at or published_at`、`end = superseded_at`，区间语义 `[start, end)`。
异常按**异常关系/分组**扣一次（每条 deduction 携带 `document_ids` 与 `version_family`），
抑制为**局部关系级**，不做 series 级 early return：

- `DUPLICATE_DOCUMENTS`：按 `(knowledge_base_id, version_family, sha256)` 分组，组内 >1 产生一条；
- `TEMPORAL_OVERLAP`：按 `start` 排序后相邻 pair 满足 `end > next.start`；
- `SERIES_GAP`：`next.start - end > 90 天`（`SERIES_GAP_DAYS=90`）；`next.start == end` 为正常连续，
  `0 < 间隔 <= 90 天` 为允许空档；
- `SERIES_SUPERSEDE_ANOMALY`：`end < start`（该文档随后被排除出时间关系检测），或同 series 中
  “当前有效”文档 >1（当前有效 = `start <= now` 且 `end IS NULL`，未来版本不算）；
- `SERIES_ORPHANED`：存在 `end` 但同 series 无 `start >= end` 的后继。

penalty 契约：`DUPLICATE_DOCUMENTS -10/组`、`TEMPORAL_OVERLAP -10/相邻关系`、
`SERIES_GAP -5/相邻关系`、`SERIES_SUPERSEDE_ANOMALY -10/异常单元`、`SERIES_ORPHANED -5/文档`；
同 code 按异常单元可累计，series penalty 总和封顶 `-40`，
`data_score = clamp(基础分 + penalty, 0, 100)`。旧 v1.0 快照保留自己的公式版本，不动态套用 v1.1。

### 公式 1.1：检索归因与等级

- 归因为纯统计、不引入 LLM：`unreferenced_documents` 以快照时全部 `ready` 文档为分母，
  描述“最近样本中未被引用”而非永久质量结论；`query_type = message.query_type or "unknown"`；
  每项记录 `sample_count/count/rate`，避免 1/1 显示成 100%。
- 等级机器码由唯一函数 `get_health_level` 计算：`>=60 healthy`、`40<=x<60 warning`、
  `<40 critical`；`health`/`snapshot`/`trend` 响应共用同一函数，前端显示 健康/偏低/严重。

持久化使用 `knowledge_health_snapshots` 表（迁移 `20260816_0017`）：保存公式版本、状态、
`overall/data/retrieval/trust` 得分、原始 `metrics`、`deductions` 与 `actions`；快照属于
知识库并随知识库级联删除。API 只接受拥有该知识库的用户：

- `GET /api/knowledge-bases/{id}/health`：返回最新快照与当前实时值；
- `POST /api/knowledge-bases/{id}/health/snapshot`：生成快照并返回与上一快照的 delta；
- `GET /api/knowledge-bases/{id}/health/history`：返回快照历史。
- `GET /api/knowledge-bases/{id}/health/trend?limit=30`：按创建时间升序返回
  `{created_at, overall, data, retrieval, trust, level}`。
- `POST /api/knowledge-bases/{id}/health/actions/run`：body `{actions: [code]}`，返回
  `{results: [{code, status, affected_count, message}], snapshot, delta}`；支持
  `RETRY_FAILED_DOCUMENTS`（筛选 failed 后逐个调用现有 `retry_document`）、`REBUILD_INDEX`
  （调用现有 `reindex_knowledge_base`）、`UPLOAD_DOCUMENTS`（返回 `requires_user_action` 由前端跳转）。
  Knowledge Health 只做编排，不重实现文档/索引工作流。

## 可审计性与可观测性

- 快照保存公式版本，权重修改必须升级 `HEALTH_FORMULA_VERSION`，否则旧快照无法解释。
- 检索健康与回答可信度是真实链路观测而非金标：样本不足时维度得分为 `None` 并给出
  `NO_ANSWER_SAMPLES`/`NO_CLAIMS` 提示，综合分不把缺样本维度伪装成数字。
- 动作执行后立即生成的快照与 delta 是**即时重新评估值**，不隐含异步重试/重建已完成；异步 Worker
  完成后由后续快照反映真实变化。
- 综合分不是评测替代：金标评测（`evaluation/`）继续负责模型质量结论，健康分只解释知识库状态。

## 已知边界

- 检索健康与回答可信度依赖真实问答积累，新知识库在积累前只能得到数据健康与明确提示。
- 数据健康 series 检测只做元数据/内容指纹层面的确定性问题（重复、重叠、断代、取代异常、孤立），
  不做内容级语义冲突判断。
- 在线观测指标受使用方式影响（如拒答率低可能是因为用户很少问敏感问题），不能等同于检索能力上限。
- 当前提供等级与趋势展示，不做趋势预测、推送告警或自动修复；公式 1.1 不检测同系列语义冲突与
  孤立文档的内容原因（ADR 0009 后续迭代）。
