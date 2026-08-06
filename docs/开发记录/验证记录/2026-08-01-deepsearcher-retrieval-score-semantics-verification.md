# DeepSearcher 检索数值语义验证记录

日期：2026-08-01  
范围：优化清单 O-02（distance、similarity、rank score）

## 1. 交付契约

`RetrievalResult` 保留向量库的原生数值语义，不把不同指标强行换算成统一分数：

| 适配器/指标 | 字段 | 排序方向 |
| --- | --- | --- |
| Milvus L2 | `distance` | 越小越近 |
| Milvus IP、COSINE | `similarity` | 越大越近 |
| Milvus 混合检索 RRF | `rank_score` | 越大排名越高 |
| Qdrant COSINE、DOT | `similarity` | 越大越近 |
| Qdrant EUCLID、MANHATTAN | `distance` | 越小越近 |
| Oracle COSINE 距离表达式 | `distance` | 越小越近 |
| Azure Search relevance | `rank_score` | 越大排名越高 |

每条结果同时提供 `metric_type`、`score_kind`、语义数值和 `higher_is_better`。旧构造参数
`score` 会迁移为未知指标的 `rank_score`，用于兼容现有调用方；Trace v3 不再输出含义不明的
`score`。布尔值、NaN 和无穷数不会被接受为检索值。

## 2. 实现与界面

- Milvus 按 Collection 的实际 metric 解释返回值，混合检索明确标记 `RRF`。
- Qdrant 从 Collection 配置读取 distance；读取失败时使用该适配器创建 Collection 的默认
  COSINE 语义，不根据数值大小猜测指标。
- Oracle SQL 固定使用 `VECTOR_DISTANCE(..., COSINE)`，因此映射为 COSINE distance。
- Trace v3 分别序列化 `distance`、`similarity` 或 `rank_score`，并携带排序方向。
- 学习控制台分别展示“距离 · 越小越近”、“相似度 · 越大越近”和“排序分 · 越大越近”；
  v2 及更早的 `score` 只显示为“旧版相关值”。
- 旧控制台查询现会把当前 Collection 作为显式查询范围发送，避免路由到无关或不兼容索引。

## 3. 自动化与真实 Milvus

本轮验证结果：

- `uv run ruff check .`：通过。
- `uv run pytest -q`：662 passed、10 skipped。
- `npm run typecheck`：通过。
- `npm test -- --run`：4 个测试文件、23 项测试全部通过。
- `npm run build`：585 个模块完成生产构建。
- `DEEPSEARCHER_RUN_LIVE_MILVUS=1 uv run pytest -q tests/integration/test_p0_milvus_live.py -k explicit_scope`：
  1 passed、1 deselected。

另一个真实 Milvus 用例写入固定二维向量 `[0, 0]`、`[1, 0]`、`[2, 0]`，以 `[0, 0]`
查询时验证 L2 结果按距离从小到大排列，且返回对象均为 `metric_type=L2`、
`score_kind=distance`。适配器单元测试覆盖 Milvus、Qdrant、Oracle、Azure 的指标映射，
Trace 和 React 测试覆盖三类新字段及旧数据兼容展示。

## 4. 浏览器验收

在 `http://127.0.0.1:8700/console` 使用真实浏览器选择
`kb_037e65f9612842fb809fd596de82e351` 并查询“Milvus 是什么？”：

- `POST /api/query` 返回 200，用时约 32.9 秒。
- 请求体含 `collection_name=kb_037e65f9612842fb809fd596de82e351`，没有再路由到其他旧索引。
- 页面返回 9 条检索文档，显示例如“距离 L2 · 越小越近 0.3355”。
- 浏览器控制台 0 错误、0 警告。
- 截图：`output/playwright/o02-score/console-l2-distance.png`。

## 5. 依据与结论

Milvus 官方指标文档定义 L2/JACCARD/HAMMING 为越小越相似，IP/COSINE 为越大越相似；
Qdrant 官方文档也说明阈值方向取决于所选 metric。因此本项目保留原生指标与排序方向，
而不是生成表面统一、实际不可比较的数值。

- Milvus metric 文档：<https://milvus.io/docs/v2.6.x/metric.md>
- Milvus metric 说明：<https://milvus.io/learn-milvus/metric>
- Qdrant 搜索文档：<https://qdrant.tech/documentation/search/search/>

O-02 验收完成。调用方与用户现在都能判断数值是什么、应按哪个方向理解；不同 metric
之间不会被误当成可直接比较的统一“相关分数”。
