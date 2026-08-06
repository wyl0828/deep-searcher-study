# DeepSearcher Q-01 质量评测闭环验证

## 结论

Q-01 已形成可重复执行的最小评测闭环：固定题集、金标证据、三 Agent
统一运行器、确定性质量指标、成本与延迟指标、版本化 JSON/CSV 报告和自动化测试。

## 固定输入

- 数据集：`evaluation/datasets/milvus_v1.json`
- 数据集版本：`1.0.0`
- 指标版本：`1.0.0`
- 题量：30（可回答 25，无答案 5）
- 原始资料：`examples/data/WhatisMilvus.pdf`
- 原始资料 SHA-256：
  `00cac5d6ca84077ed81380becc06cde379be11db37fcef59ec4b041a4f39acc1`
- Collection：`kb_037e65f9612842fb809fd596de82e351`
- 入库来源 alias：`doc_d8555f3a34b741d788fe88793d489fce.pdf`
- 模型：`qwen-plus`
- Embedding：`text-embedding-v4`，1024 维

运行器会在初始化外部组件前校验原始资料 SHA-256；不会把密钥写入报告。

## 真实基线

### 完整 NaiveRAG 检索（30 题）

命令：

```powershell
uv run python -m evaluation.benchmark `
  --collection kb_037e65f9612842fb809fd596de82e351 `
  --source-alias doc_d8555f3a34b741d788fe88793d489fce.pdf `
  --agents naive --mode retrieval --top-k 5 --max-iter 1 `
  --output evaluation/results/2026-07-30-naive-retrieval-v1
```

结果：

| 指标 | 数值 |
| --- | ---: |
| 可回答题 Recall@5 | 1.0000 |
| Precision@5 | 0.4160 |
| MRR | 0.8633 |
| 证据要点覆盖率 | 0.9086 |
| 来源正确率 | 1.0000 |
| 无答案检索假阳性率 | 1.0000 |
| 平均延迟 | 960.046 ms |
| P95 延迟 | 1597.174 ms |
| 错误率 | 0 |

无答案题也总能召回相似内容，因此“有召回”不能直接等价于“问题可回答”。

### 三 Agent 检索对比（固定前三题）

| Agent | Recall@5 | Precision@5 | MRR | 平均延迟 | Token/题 | LLM 调用/题 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| NaiveRAG | 1.0000 | 0.4667 | 1.0000 | 1183 ms | 0 | 0 |
| DeepSearch | 1.0000 | 0.3333 | 1.0000 | 24757 ms | 7089.67 | 21.00 |
| ChainOfRAG | 0.6667 | 0.2000 | 0.6667 | 3975 ms | 2598.00 | 2.67 |

ChainOfRAG 在基金会/许可证题生成了偏离目标的追问，最终没有保留证据。
DeepSearch 命中稳定，但逐 Chunk 筛选导致成本与延迟显著上升，验证了 A-01。

### 三 Agent 回答对比（事实、多事实、无答案各一题）

固定样本：`milvus-001,milvus-020,milvus-026`。

| Agent | 答案要点覆盖 | 拒答准确率 | 平均延迟 | Token/题 | LLM 调用/题 |
| --- | ---: | ---: | ---: | ---: | ---: |
| NaiveRAG | 1.0000 | 0.0000 | 12688 ms | 2131.67 | 1.00 |
| DeepSearch | 1.0000 | 1.0000 | 36415 ms | 8937.00 | 21.67 |
| ChainOfRAG | 0.7083 | 1.0000 | 6352 ms | 3130.67 | 3.67 |

NaiveRAG 对价格题继续基于相似内容作答；DeepSearch 和 ChainOfRAG 在本次样本中
正确拒答。答案要点覆盖是透明字符串规则，不冒充 LLM 裁判正确率。

## 自动化验证

```text
uv run ruff format --check evaluation
uv run ruff check evaluation
uv run pytest tests/evaluation -q
```

结果：`13 passed`，Ruff 全部通过。

全量回归：

- Python：`534 passed, 7 skipped`；保留 1 个既有 Crawl4AI 未 await 警告。
- 前端：`17 passed`。
- TypeScript：`npx tsc --noEmit` 通过。
- Vite：生产构建通过，585 个模块完成转换。

## 报告位置

- `evaluation/results/2026-07-30-naive-retrieval-v1/report.json`
- `evaluation/results/2026-07-30-three-agents-retrieval-smoke-v1/report.json`
- `evaluation/results/2026-07-30-three-agents-answer-smoke-v1/report.json`

每个目录同时包含便于人工查看和表格分析的 `details.csv`。

## 后续优化入口

1. 以完整 30 题运行三 Agent，建立成本预算允许下的正式对比基线。
2. A-01 已于 2026-07-30 完成批量 Rerank，结果见
   `2026-07-30-deepsearcher-batch-rerank-verification.md`。
3. 为无答案判定加入检索阈值或显式 answerability 判断，降低 NaiveRAG 误答。
4. 后续可增加人工或独立裁判集；现阶段保留“证据要点覆盖”命名，避免过度宣称忠实度。
