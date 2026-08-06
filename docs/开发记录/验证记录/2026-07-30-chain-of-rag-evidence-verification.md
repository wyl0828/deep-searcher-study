# ChainOfRAG A-03 证据闭环验证

## 结论

A-03 已完成。ChainOfRAG 的下一轮检索不再直接消费未经验证的中间答案；
可信上下文只包含被支持文档完整支撑、与主问题相关且带可定位引用的答案。
产品运行时已启用证据数量约束的 early stopping。

同时完成 O-03：一个追问选择多个 Collection 时只生成一次 Embedding。

## 可信数据流

1. 第一轮直接检索原始问题，避免在零证据状态下发生目标漂移，也减少一次 LLM 调用。
2. 中间答案生成后，支持判定必须同时满足：
   - 选中文档共同完整支持答案中的实质性事实；
   - 该问答直接有助于回答主问题；
   - 模型输出是合法、去重且范围正确的零基索引。
3. 只有选中至少一篇支持文档时，答案才进入可信上下文，并附带
   `[S1E1]`、`[S1E2]` 等引用以及安全的来源名、页码和 Chunk 编号。
4. 无支持文档、无召回、解析失败或偏离主问题的答案仍保留在结构化审计步骤中，
   但不会进入下一轮提示、最终证据或最终回答上下文。
5. 后续提示只包含可信上下文和历史追问文本，不包含未验证答案。

## 查询与停止保护

- 空追问、超长追问和规范化后重复的追问会在 Embedding 前拒绝。
- 默认 `min_evidence_for_stop=2`；未达到两条不同证据时，即使模型认为信息足够，
  也不能提前结束。
- 达到证据预算后才调用反思模型；模型和证据门同时通过才停止。
- `confidence = min(不同证据数 / 证据阈值, 1)`，它是公开、确定性的证据预算
  代理值，不是统计概率或事实正确率。
- 无召回文档时直接返回 `No relevant information found`，不调用中间回答模型。

支持判定仍是基于 LLM 的语义校验，不是形式化事实证明。规范化重复检查能稳定
拦截大小写、空白和标点差异；语义改写型重复仍需依靠主问题相关性和证据校验。

## 错误传播回归

自动化测试显式构造：

- 第一轮中间答案包含错误日期；
- 支持文档选择为空；
- 第二轮获得另一条有证据答案。

断言证明错误日期不出现在第二轮输入或最终可信上下文中；第一步标记
`trusted=false`，第二步标记 `trusted=true`。此外覆盖：

- 重复追问不会触发第二次检索；
- 两条不同证据之前不能 early stop；
- 无文档时跳过支持模型和中间回答模型；
- 目标偏移/不完整支持返回空选择并记录原因；
- Trace 保留 query guard、支持状态、证据数量和停止门；
- 两个 Collection 只调用一次 Embedding。

## 真实质量评测

环境：

- LLM：`OpenAI / qwen-plus`
- Embedding：`OpenAIEmbedding / text-embedding-v4`
- VectorDB：`Milvus`
- Collection：`kb_037e65f9612842fb809fd596de82e351`
- 样本：`milvus-001`、`milvus-002`、`milvus-003`、`milvus-026`
- `top_k=5`、`max_iter=2`、证据停止阈值 `2`

命令：

```powershell
uv run python -m evaluation.benchmark `
  --collection kb_037e65f9612842fb809fd596de82e351 `
  --source-alias doc_d8555f3a34b741d788fe88793d489fce.pdf `
  --agents chain_of_rag --mode answer --top-k 5 --max-iter 2 `
  --chain-min-evidence-for-stop 2 `
  --sample-ids milvus-001,milvus-002,milvus-003,milvus-026 `
  --output evaluation/results/2026-07-30-chain-of-rag-evidence-v1
```

最终结果：

| 指标 | 结果 |
| --- | ---: |
| 成功数 | 4 / 4 |
| 错误率 | 0 |
| Recall@5 | 1.0000 |
| Precision@5 | 0.2667 |
| MRR | 1.0000 |
| 答案要点覆盖 | 0.8333 |
| 证据要点覆盖 | 0.9167 |
| 来源准确率 | 1.0000 |
| 拒答准确率 | 1.0000 |
| 无答案误报率 | 0 |
| 平均延迟 | 8118 ms |
| 平均 Token | 5523 |
| 平均 LLM 调用 | 5.00 |

无答案题 `milvus-026` 返回零篇证据并明确拒答；三个可回答问题均命中标注来源。
报告不包含 `api_key`。

## 失败基线与修正

第一次启用严格证据停止后的真实基线中，第一轮仍由模型在零证据状态生成追问。
模型把“基金会与许可证”改写为“官网”，导致正确文档虽然被召回，中间回答仍因
问题偏移而拒答：

| 指标 | 漂移失败版 | 最终版 |
| --- | ---: | ---: |
| Recall@5 | 0.6667 | 1.0000 |
| 答案要点覆盖 | 0.3889 | 0.8333 |
| 来源准确率 | 0.6667 | 1.0000 |
| 平均 LLM 调用 | 5.50 | 5.00 |
| 平均延迟 | 8808 ms | 8118 ms |

修正为“第一轮固定检索原问题”后，同一题集恢复全部命中。失败报告被保留，
没有只保留有利结果。

## 自动化验证

- Python：`559 passed, 7 skipped`
- 前端：`17 passed`
- Ruff、TypeScript、Vite 生产构建全部通过

## 报告

- 最终：
  `evaluation/results/2026-07-30-chain-of-rag-evidence-v1/report.json`
- 漂移失败版：
  `evaluation/results/2026-07-30-chain-of-rag-first-query-drift-failed-v1/report.json`
