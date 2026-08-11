# ADR 0001：Trust Layer 契约

## 状态

Accepted，首版实现于 2026-08-10。

## 决策

Trace v5 在兼容 `grounding` v1 的同时新增 `trust` v1。Trust、Safety 和 Policy 使用独立
字段，避免把证据充分性、安全审查与产品动作混成一个状态。

```json
{
  "version": 1,
  "trust_status": "partially_grounded",
  "safety_status": "not_evaluated",
  "verification_level": "deterministic_consistency",
  "claims": [
    {
      "text": "系统支持 PDF 上传。",
      "support_status": "supported",
      "citation_status": "valid",
      "entailment_status": "not_checked",
      "consistency_status": "inconsistent",
      "consistency_checks": [
        {
          "kind": "quantity",
          "status": "inconsistent",
          "reason_code": "QUANTITY_NOT_IN_EVIDENCE",
          "missing_values": ["100|mb"]
        }
      ],
      "confidence": null,
      "evidence_ids": ["E1"],
      "citation_spans": [
        {
          "evidence_id": "E1",
          "start": 12,
          "end": 26,
          "text": "系统支持 PDF 上传",
          "match_type": "normalized_exact",
          "score": 1.0
        }
      ]
    }
  ],
  "policy": {
    "action": "downgrade",
    "reason_codes": ["ANSWER_PARTIALLY_GROUNDED"]
  }
}
```

## 约束

- `supported` 在 v1 只表示引用结构有效；`verification_level` 和 limitations 必须明确说明
  尚未完成语义蕴含验证。
- 未运行 Checker 时使用 `not_checked` 和 `null`，不得伪造置信度。
- Trust 契约不保存模型隐藏推理。
- 所有公开字段必须有长度、类型、数量和枚举边界。
- 新 Checker 通过增加状态与 reason code 演进，不修改旧字段语义。
- `structural_support_status` 保存引用结构结论，`support_status` 保存所有已执行 Checker 后的
  有效结论；两者不一致时必须保留检查详情。
- `citation_spans.start/end` 相对于同一 Trace 中该 Evidence 的 `text`，不是原始文档全局偏移；
  原文页码、Chunk 和全局字符位置继续由 Citation locator 字段表达。
- Entailment Checker 必须保存 checker/version、状态、Token、置信度与受控 reason code；不得保存
  模型思维链或未经清洗的异常。

## 后果

产品可以使用确定性策略和可选 NLI 降低无引用及语义矛盾风险，同时保留后续独立 Judge 与 Risk
Profile 的扩展空间。调用方需要同时理解 Trace v5 与 Trust Contract v1 两个版本号。
