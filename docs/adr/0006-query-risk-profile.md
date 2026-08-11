# ADR 0006：服务端 Query Risk Profile

## 状态

Accepted，首版实现于 2026-08-11。

## 决策

风险不是客户端传入的展示标签，而是服务端根据原始问题生成的验证约束。当前确定性分类器识别：

- 财务与报销；
- 法律、制度、监管与合规；
- 权限、授权与敏感访问；
- 医疗、用药与安全处置。

只有“敏感领域 + 决策意图”联合出现时才提升为 high，例如“最高报销额度是多少”“普通成员是否
可以删除审计日志”。“解释 ACL 的实现原理”“总结报销制度章节”保持 medium。急救、剂量、事故
处理等即时安全影响可直接提升。

分类结果只保存受控字段，不保存或回显原始问题：

```json
{
  "version": 1,
  "classifier": "deterministic_query_risk",
  "classifier_version": "1.0.0",
  "risk_level": "high",
  "query_type": "financial_policy",
  "risk_factors": ["FINANCIAL_DOMAIN", "DECISION_REQUEST", "QUANTITATIVE_DECISION"],
  "requirements": {
    "require_citation": true,
    "require_decisive_entailment": true,
    "minimum_evidence_count": 2,
    "minimum_distinct_source_count": 2,
    "allow_unknown_entailment": false
  }
}
```

## Claim 门槛

high 风险 Claim 必须满足：

1. Citation 结构有效；
2. Entailment 为 entailed 或 contradicted，不能是 unknown/not_checked；
3. 一般决策至少一份证据；
4. 额度、期限、比例、剂量等定量决策至少两份 Evidence 且来自两个独立来源。

独立来源优先使用 URL、document_id 或 reference；同一文档的不同 Chunk 不重复计数。未达门槛的
Claim 降为 unsupported，并进入既有 downgrade/refuse。明确冲突的 Claim 保持
`conflict_disclosed`，继续向用户展示冲突资料。

## 信任边界

公共查询 API 不提供 risk_level 字段，因此用户不能把 high 降为 medium。未来管理端如支持覆盖，
也只能提升风险，并必须进入审计日志。

## 评测

人工金标 `evaluation/datasets/risk_profile_v1.json` 覆盖 high/medium、定量门槛、敏感关键词负控和
即时安全问题。`python -m evaluation.risk_profile` 是快速质量门禁，任何分类、query type 或独立
来源门槛漂移都会失败。

首版仍有语言和领域覆盖限制；必须优先依据真实误判扩展数据集，不能无边界增加关键词。
