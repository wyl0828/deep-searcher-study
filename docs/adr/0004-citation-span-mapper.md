# ADR 0004：Citation Span Mapper

## 状态

Accepted，首版实现于 2026-08-11。

## 问题

Citation 的页码、Chunk 和 `char_start/char_end` 描述证据片段在原始文档中的位置，但不能回答
“这个 Claim 具体对应证据片段中的哪一句”。直接复用这些字段会混淆两套坐标系。

## 决策

每个 Claim 为其合法 Evidence ID 保存独立 `citation_spans`：

```json
{
  "evidence_id": "E1",
  "start": 12,
  "end": 34,
  "text": "每份 PDF 最大 20 MiB",
  "match_type": "normalized_exact",
  "score": 1.0
}
```

- `start/end` 是生成模型实际看到的 Evidence 快照中的半开字符区间。
- `normalized_exact` 忽略空白、常见标点和 Markdown 装饰后精确定位，再映射回原始字符区间。
- `sentence_overlap` 选择字符二元组覆盖率达到受控阈值的完整句子；数字存在时不得指向不同数字。
- `not_found` 必须使用空 quote 与空偏移，不猜测位置。
- 多 Citation Claim 对每个 Evidence 独立定位，允许不同证据分别支持 Claim 的不同部分。

句级近似只是 UI 导航提示，不改变 Claim 的 `support_status`，也不等同于 Entailment。前端使用不同
视觉样式区分精确与近似定位。

## 安全与持久化

产品层只持久化能映射到当前消息 Citation 的 Evidence ID。非空 span 必须同时满足：

1. 偏移为有界非负整数且 `end > start`；
2. `end` 不超过有界 Evidence 文本；
3. `Citation.text[start:end]` 与 span quote 完全相等；
4. match type 和 score 通过枚举与有限数校验。

因此污染 Trace、伪造 Evidence ID、越界 offset 或不一致 quote 都不会进入产品数据库。

## 评测与边界

人工金标位于 `evaluation/datasets/citation_span_v1.json`，由
`python -m evaluation.citation_span` 生成机器可读报告，并进入快速质量门禁。

首版 Span Mapper 不做同义词、指代和跨句语义对齐；这些情况返回 `not_found` 或低承诺的句级
近似。NLI 作为独立 Checker 消费 Claim 与原始 Evidence；只有 Claim 与完整证据句等价时才使用
Span 做零成本短路，嵌在指令、转述或限定句中的精确子串仍需 NLI，且不能反向改变证据快照。
