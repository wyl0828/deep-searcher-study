# DeepSearcher A-01 批量 Rerank 验证

## 结论

A-01 已完成。DeepSearch 从“每个子查询的每条 Chunk 各调用一次模型”改为：

1. 同轮各子查询分别做向量召回；
2. 按各查询排名交错合并并按文本去重；
3. 限制本轮候选总量；
4. 用严格零基索引数组分批选择证据；
5. 已确认存在证据时，补充每个子查询排名第一的候选作为覆盖保护。

默认 `rerank_candidate_limit=20`、`rerank_batch_size=10`。模型返回合法空数组时
不保留证据；解析失败时才回退到向量候选，决策会进入 Trace。

## 同样本前后对比

### 检索模式

固定样本为 `milvus-001`～`milvus-003`，`top_k=5`、`max_iter=1`。

| 指标 | 优化前 | 优化后 | 变化 |
| --- | ---: | ---: | ---: |
| Recall@5 | 1.0000 | 1.0000 | 持平 |
| Precision@5 | 0.3333 | 0.4000 | +0.0667 |
| MRR | 1.0000 | 1.0000 | 持平 |
| 证据要点覆盖 | 0.9167 | 0.9167 | 持平 |
| LLM 调用/题 | 21.00 | 2.00 | -90.48% |
| Token/题 | 7089.67 | 2124.67 | -70.03% |

三题外部模型延迟受单次请求抖动影响，因此 P95 使用下面的完整 30 题结果观察。

### 回答模式

固定样本为事实题 `milvus-001`、多事实题 `milvus-020`、无答案题
`milvus-026`。

| 指标 | 优化前 | 优化后 | 变化 |
| --- | ---: | ---: | ---: |
| 答案要点覆盖 | 1.0000 | 1.0000 | 持平 |
| 拒答准确率 | 1.0000 | 1.0000 | 持平 |
| Recall@5 / MRR | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 持平 |
| 平均延迟 | 36415 ms | 16746 ms | -54.01% |
| Token/题 | 8937.00 | 3261.00 | -63.51% |
| LLM 调用/题 | 21.67 | 2.67 | -87.68% |

## 完整 30 题最终基线

| 指标 | 数值 |
| --- | ---: |
| 可回答题 Recall@5 | 1.0000 |
| Precision@5 | 0.2960 |
| MRR | 0.9400 |
| 证据要点覆盖率 | 0.9486 |
| 来源准确率 | 1.0000 |
| 无答案检索假阳性率 | 0 |
| 空结果率 | 0.1667 |
| 错误率 | 0 |
| 平均延迟 | 6129.747 ms |
| P95 延迟 | 6992.402 ms |
| Token/题 | 1927.93 |
| LLM 调用/题 | 2.00 |

五道无答案题全部被批量 Rerank 拒绝；25 道可回答题全部命中标注页。

## 运行命令

完整检索：

```powershell
uv run python -m evaluation.benchmark `
  --collection kb_037e65f9612842fb809fd596de82e351 `
  --source-alias doc_d8555f3a34b741d788fe88793d489fce.pdf `
  --agents deep_search --mode retrieval --top-k 5 --max-iter 1 `
  --output evaluation/results/2026-07-30-deep-search-batch-retrieval-full-v1
```

回答抽样：

```powershell
uv run python -m evaluation.benchmark `
  --collection kb_037e65f9612842fb809fd596de82e351 `
  --source-alias doc_d8555f3a34b741d788fe88793d489fce.pdf `
  --agents deep_search --mode answer --top-k 5 --max-iter 1 `
  --sample-ids milvus-001,milvus-020,milvus-026 `
  --output evaluation/results/2026-07-30-deep-search-batch-answer-v1
```

## 自动化验证

- 批量索引选择覆盖正常、空、解析失败、越界、负数、重复和分批场景。
- 跨子查询候选按排名交错、去重、限额。
- 无答案不会被查询首位候选保护重新污染。
- 批量选择与覆盖保护均记录安全 Trace。
- 相关测试：`45 passed`。
- 全量 Python：`543 passed, 7 skipped`。
- 前端：`17 passed`；TypeScript 与 Vite 生产构建通过。

## 报告

- `evaluation/results/2026-07-30-deep-search-batch-retrieval-v1/report.json`
- `evaluation/results/2026-07-30-deep-search-batch-retrieval-full-v1/report.json`
- `evaluation/results/2026-07-30-deep-search-batch-answer-v1/report.json`
