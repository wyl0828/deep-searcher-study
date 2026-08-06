# DeepSearcher O-06 混合检索效果评测

日期：2026-08-01  
范围：Milvus Dense、BM25、Hybrid/RRF 质量、延迟、稳定性和索引资源对比

## 1. 结论

O-06 已完成，但结论是“不启用 Hybrid 作为生产默认值”。在固定 30 题中文题集上，Hybrid
保持了 Dense 的 Recall@5 和证据要点覆盖，却把 MRR 从 `0.9400` 降到 `0.8733`，综合质量分
下降 `0.022233`。BM25 单独使用明显更差。Hybrid 还需要额外的稀疏字段和倒排索引，因此没有
证据支持承担迁移与资源成本。

这不是“Hybrid 永远无效”的结论。它只适用于当前 PDF、`1500/100` 分块、
`text-embedding-v4`、Milvus standard analyzer 和 RRF `k=60`。语料、分析器或融合参数变化后
必须重跑同一门禁。

## 2. 可重复评测契约

新增 `python -m evaluation.retrieval_compare`（同时提供
`deepsearcher-retrieval-compare` 命令）：

```text
uv run --frozen python -m evaluation.retrieval_compare \
  --prepare --cleanup \
  --collection eval_o06_milvus_v1 \
  --top-k 5 --repetitions 3 \
  --rrf-k 60 --batch-size 10 \
  --output evaluation/results/2026-08-01-o06-dense-bm25-hybrid-v1
```

- 只允许创建或删除 `eval_` 前缀 Collection，避免触碰产品知识库。
- 数据集和四页 PDF 均做 SHA-256 校验；索引固定生成 16 个 Chunk。
- 在同一份混合索引上比较三种模式，Dense 与 Hybrid 使用完全相同的向量和 Chunk。
- 每道题只调用一次查询 Embedding；三种模式各重复三次并轮换执行顺序。
- 保存完整 Manifest、Embedding 指纹、分块版本、RRF 参数、字段、索引和逐题结果。
- 自动门禁要求综合质量至少提升 1 个百分点、关键指标不明显回退、P95 可接受、至少 20 道
  可回答题且零错误；阈值均写入报告。

## 3. 真实结果

| 指标 | Dense | BM25 | Hybrid/RRF |
| --- | ---: | ---: | ---: |
| 成功率 | 100% | 100% | 100% |
| Recall@5 | 1.0000 | 0.6800 | 1.0000 |
| Precision@5 | 0.4400 | 0.1840 | 0.3760 |
| MRR | 0.9400 | 0.3367 | 0.8733 |
| 证据要点覆盖 | 0.9169 | 0.3843 | 0.9169 |
| 空结果率 | 0 | 0.1000 | 0 |
| 重复结果率 | 0 | 0 | 0 |
| 排序稳定率 | 100% | 100% | 100% |

Hybrid 相对 Dense 的综合质量变化为 `-0.022233`。它在部分多事实、性能和规模问题上提高首条
相关结果排名，但在定义、事实、同义表达、数据模型、架构、检索和生态标签上出现更多排名下降；
整体没有达到提升门禁。

第二次完整运行的检索 P50 为 Dense `399.495 ms`、BM25 `400.060 ms`、Hybrid
`403.612 ms`。P95 受本机 Milvus 抖动影响较大，分别为 `1870.707/1275.199/1658.723 ms`；
第一次完整运行约为 `421.336/411.150/412.778 ms`。两次质量结果完全一致，门禁失败来自质量
而非延迟，不利用一次有利的延迟波动改变结论。

## 4. 索引资源

评测 Collection flush 后统计为 16 行：

- Dense：已有 1024 维 `embedding` 字段和 `AUTOINDEX/L2`。
- Hybrid：在相同 Dense 结构上额外增加 `sparse_vector` 字段和
  `SPARSE_INVERTED_INDEX/BM25`。
- Milvus 当前接口未提供可靠的逐索引存储字节，因此报告只声明可核对的字段、索引数量与行数，
  不伪造磁盘节省比例。

## 5. 自动化与清理

- Milvus 模式与评测工具定向测试：39 passed、6 skipped。
- 全量回归：`707 passed, 10 skipped`；Ruff 全仓检查、`uv lock --check` 和
  `deepsearcher-retrieval-compare --help` 均通过。唯一 warning 是既有 Crawl4AI mock 协程告警，
  与本次改动无关。
- 覆盖显式 Dense 字段、BM25 sparse 字段、RRF `k`、数值语义、非法模式、资源快照、单次
  Embedding/多模式重复、排序稳定性、推荐门禁、安全命名和 JSON/CSV 原子写入。
- 首次准备阶段发现当前 Provider 单批最多 10 条，评测工具已显式提供 `--batch-size` 并默认 10，
  失败运行没有留下 Collection。
- 最终报告记录 `prepared_chunk_count=16`、Milvus `row_count=16`，两个索引均完成 16 行索引。
- `--cleanup` 返回 `deleted=true`；随后独立核对 Collection、版本集合和别名均不存在。

完整机器可读结果：
`evaluation/results/2026-08-01-o06-dense-bm25-hybrid-v1/report.json`；逐题表：同目录
`details.csv`。

## 6. 决策

生产配置明确保留 `hybrid: false` 和 `rrf_k: 60`。未来若要重新评估，应优先扩充多文档、专有
编号、日期和中文关键词题集，并分别比较中文 analyzer、RRF `k` 或加权融合；只有重新通过质量、
延迟和资源门禁后，才能配合安全索引重建切换默认策略。

最终重启后 Docker、Milvus、核心 API、Document worker 和用户工作台全部为 `ready`。独立读取
当前 Milvus 运行时确认 `configured_default=dense`，现有产品 Collection 只有 Dense 能力且
包含 16 行；报告递归检查未发现 API Key、密码、服务令牌或其他秘密字段。
