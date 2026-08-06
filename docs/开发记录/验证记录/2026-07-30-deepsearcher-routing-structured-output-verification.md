# DeepSearcher 路由与结构化输出边界验证记录

- 验证日期：2026-07-30
- 对应问题：R-02、R-03、R-04
- 验证结论：通过

## Agent 路由

RAGRouter 只接受完整匹配的单个一基正整数，并转换为经过范围检查的零基索引。以下模型输出均有确定行为：

| 输出类型 | 示例 | 结果 |
| --- | --- | --- |
| 合法枚举 | `1`、`2` | 选择对应 Agent |
| 空或无数字 | `""`、`No digits` | 回退 baseline |
| 非法范围 | `0`、`-1`、`3` | 回退 baseline |
| 解释或多数字 | `agent 2`、`1 2` | 不猜测数字，回退 baseline |

Trace 的 `routing` 字段记录安全的 selected、rejected、fallback_used 和 reason。模型原始解释和思维内容不会进入 Trace。

## Collection allowlist

CollectionRouter 每次调用都重新读取真实 Collection，并按以下顺序约束：

```text
真实 Collection
→ 可选 allowed_collections
→ 提示给模型的候选
→ 模型输出类型校验
→ 再次精确白名单过滤
→ 稳定去重
→ 明确 fallback
```

显式 `collection_names` 也经过真实集合与授权集合交集，但不会在全部无效时回退到更宽范围。DeepSearch、ChainOfRAG 和 NaiveRAG 均使用这一路径。

安全 Trace 可区分：

- `source`：model、explicit、single 或 all；
- `requested`：解析出的安全名称；
- `selected`：真正进入搜索的名称；
- `rejected`：幻觉、不存在或越权名称；
- `fallback_used` 与稳定 `reason`。

## 字符串列表与文档索引

DeepSearch 子查询和补充查询执行：

- 容器必须为列表；
- 元素必须是字符串；
- 去除首尾空白；
- 拒绝空字符串和超长字符串；
- 稳定去重；
- 限制最大条数；
- 补充查询排除已经执行的内容；
- 初始解析失败或空列表时回退原问题。

ChainOfRAG 支持文档索引执行：

- 仅接受 Python `int`，显式拒绝 `bool` 和浮点数；
- 严格检查 `0 <= index < len(results)`；
- 稳定去重；
- 全部非法或解析失败时选择零篇文档。

Markdown 代码围栏和只包含一个列表的解释文本由字面量提取层处理，随后仍必须通过上述语义校验。

## 真实查询

使用已有“Milvus 学习资料”知识库和真实 PDF 发起：

```text
Milvus 是什么？请根据文档简要回答。
```

核心 API 参数包含 `include_trace=true`、`max_iter=1` 和该知识库的唯一 Collection。结果：

```text
HTTP: 200
Agent: ChainOfRAG
Agent routing fallback: false
Collection selected: kb_037e65f9612842fb809fd596de82e351
Collection rejected: []
Retrieved documents: 5
Supported document indices: [0]
Support-selection rejected: []
Consumed tokens: 3741
```

回答将 Milvus 概括为高性能、高可扩展的向量数据库，并说明其部署范围及开源/云服务形态，与文档内容一致。查询没有创建产品对话或临时消息，因此无需数据清理。

## 自动化结果

```text
Routing/selection/Trace targeted regression: 92 passed
Python full regression: 521 passed, 7 skipped
Ruff check and format: passed
Real scoped query with safe Trace: passed
```

全量 Python 测试仍保留一个既有 Crawl4AI mock 协程未等待警告，与本次修改无关。
