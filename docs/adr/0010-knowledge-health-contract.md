# ADR 0010：Knowledge Health Contract

## 状态

Accepted，首版实现于 2026-08-16（v0.4 起始纵切，提交 `ff5fbcf`）。

## 问题

系统能解释“这条回答为什么被保留、降级或拒答”，但知识库整体质量为什么变化、变化发生在数据、检索
还是回答可信度，缺少可解释的度量。直接展示若干原始指标（如失败文档数、拒答率）难以回答“该先做什么”，
而一个没有来源的“健康分”又会变成黑盒分数，无法审计。

## 决策

引入 Knowledge Health 公式 v1.0：综合分 = 数据健康 40% + 检索健康 30% + 回答可信度 30%。
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

持久化使用 `knowledge_health_snapshots` 表（迁移 `20260816_0017`）：保存公式版本、状态、
`overall/data/retrieval/trust` 得分、原始 `metrics`、`deductions` 与 `actions`；快照属于
知识库并随知识库级联删除。API 只接受拥有该知识库的用户：

- `GET /api/knowledge-bases/{id}/health`：返回最新快照与当前实时值；
- `POST /api/knowledge-bases/{id}/health/snapshot`：生成快照并返回与上一快照的 delta；
- `GET /api/knowledge-bases/{id}/health/history`：返回快照历史。

## 可审计性与可观测性

- 快照保存公式版本，权重修改必须升级 `HEALTH_FORMULA_VERSION`，否则旧快照无法解释。
- 检索健康与回答可信度是真实链路观测而非金标：样本不足时维度得分为 `None` 并给出
  `NO_ANSWER_SAMPLES`/`NO_CLAIMS` 提示，综合分不把缺样本维度伪装成数字。
- 综合分不是评测替代：金标评测（`evaluation/`）继续负责模型质量结论，健康分只解释知识库状态。

## 已知边界

- 检索健康与回答可信度依赖真实问答积累，新知识库在积累前只能得到数据健康与明确提示。
- 数据健康当前不检测同系列重复版本、时间重叠、断代、冲突与孤立文档（ADR 0009 已预告为后续迭代）。
- 在线观测指标受使用方式影响（如拒答率低可能是因为用户很少问敏感问题），不能等同于检索能力上限。
- 首版只做快照与对比，不做趋势预测、阈值告警或自动修复动作。
