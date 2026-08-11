# ADR 0002：Answer Policy Engine

## 状态

Accepted，首版实现于 2026-08-10。

## 决策

验证结果必须改变最终用户体验。标准策略如下：

| 输入状态 | 动作 | 行为 |
| --- | --- | --- |
| fully_grounded | allow | 保留原回答 |
| partially_grounded | downgrade | 只保留有当前有效 Evidence ID 的声明 |
| conflicting_evidence | disclose_conflict | 保留明确披露冲突的回答 |
| insufficient_evidence | refuse | 替换为保守拒答 |

## 强制执行边界

策略只有在 TraceCollector 保存了生成模型实际看到的精确证据快照时才强制执行。没有该快照
的旧 Searcher 或第三方调用返回 `observe/TRUST_EVIDENCE_SNAPSHOT_MISSING`，保持原回答，
避免基于错误证据集合静默改写。

## 当前限制

- v1 降级以 Claim 为单位重建答案，会丢失原回答部分 Markdown 排版。
- v1 已验证阿拉伯数字/单位、年月或完整日期、版本、显式范围、限制条件和能够严格对齐同一
  命题的否定方向；尚不验证开放语义蕴含、复杂时间/条件关系和安全性。
- Query Risk Classifier 已接入；高风险 unknown/not_checked 会拒绝，定量决策还校验独立来源。
  当前分类规则是确定性首版，仍需用真实业务误判样本持续扩充。

这些限制必须通过 Trust Trace 对调用方公开，并由后续独立 Judge 和 Policy Profile 迭代解决。
可选 NLI Checker 已能将高置信 contradiction 接入本策略；unknown 在标准策略下保留并披露。
Citation Span Mapper 只负责定位，不依据近似分数改变语义支持判定。
