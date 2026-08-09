# DeepSearcher 多文档评测、声明级引用与多轮上下文验证记录

日期：2026-08-09  
范围：业务评测 v2、声明—证据映射、产品持久化与展示、结构化多轮追问、真实 Milvus/模型基线。

## 1. 完成内容

- `workspace_v2.json` 升级到 `2.2.0`：固定 3 份仓库 PDF 的路径和 SHA-256，共 72 题；65 题可回答、
  7 题无答案，8 题要求跨文档证据，12 题携带对话历史；299 个 criteria 均可在金标证据页直接命中。
- 评测报告使用 `metric_version=2.1.0`，增加全部证据召回、跨文档覆盖、按标签/难度聚合、声明支持、引用
  precision/recall、上下文依赖判断、改写匹配和回退指标。
- 三类 Agent 的最终回答统一使用当前结果集的一次性证据编号 `[E1]`；解析器接受紧凑与带空格
  标记，拒绝不存在的编号，并显式识别冲突声明。
- Trace 升级为 v4，新增 `grounding` 与 `contextualization`；生成时使用的有界 `wider_text` 证据窗口
  按同一 Evidence 编号持久化，不保存原问题、历史正文或模型隐藏推理。
- 产品新增 `AnswerClaim` 表和迁移，声明只允许映射本次安全 Evidence 对应的 Citation；伪造
  `[E9]` 不会落成有效引用。
- 前端保留原始 Markdown 与代码块，在可展开核验区逐声明显示支持、无支持、无效引用或冲突，
  并保留可点击的来源抽屉。
- 产品不再拼接最近六条消息。核心 API 接收结构化 `conversation_history`，仅使用有界历史做
  指代消解；话题切换、非法输出和模型错误都安全回退当前问题。

## 2. 自动化验证

定向验证覆盖：

- 数据集 schema v1/v2 兼容、来源哈希、未知文档、历史标签和 72 题业务覆盖。
- 多文档全证据/全文档覆盖率与声明级引用指标。
- `[E1]`、`[ E1 ]`、伪造编号、冲突编号、连续中文声明和 Evidence 标签注入转义。
- 上下文追问、话题切换、未 grounded 助手消息排除、历史标签注入转义、模型失败回退。
- 核心 JSON/SSE、Collection 范围、联网开关与上下文 Token 统计。
- BFF 安全阶段白名单、AnswerClaim/Citation 持久化和 React 声明级展示。

阶段性结果：

```text
Grounding/指标/产品 API 定向测试  40 passed
前端组件测试                  31 passed
前端类型检查与生产构建         通过
```

最终全量结果：

```text
uv run --frozen ruff check .       All checks passed
uv run --frozen pytest -q          759 passed, 10 skipped
npm test -- --run                  4 files, 31 tests passed
npm run typecheck                  passed
npm run build                      587 modules transformed
uv run --frozen mkdocs build       passed（保留历史文档链接/nav warning）
Alembic 全新 SQLite upgrade head    20260809_0006 (head)
```

首次把 pytest 与 Vite build 并行执行时，Vite 清空 `dist` 的窗口导致 SPA 静态深链测试临时 404；
顺序执行后通过。SSE 断连测试同时发现无历史请求错误要求 runtime 暴露 LLM 的真实兼容问题，加入
零历史快速路径后，断连取消测试和全量测试均通过。

## 3. 真实多文档检索基线

Milvus 2.5.8 的 standalone、etcd 和 MinIO 均为 healthy。重新入库得到 282 个 Chunk 的混合索引，
对 72 题分别运行 Dense、BM25、Hybrid，`top_k=8`、每题每模式重复 3 次：

| 指标 | Dense | BM25 | Hybrid |
| --- | ---: | ---: | ---: |
| 成功率 | 100% | 100% | 100% |
| Retrieval hit rate | 92.31% | 73.85% | 89.23% |
| Recall@8 | 84.62% | 63.33% | 84.10% |
| MRR | 71.16% | 42.92% | 62.05% |
| 全证据召回率 | 76.92% | 53.85% | 76.92% |
| 跨文档完整覆盖率（8 题） | 25.00% | 25.00% | 50.00% |
| 检索 p95 | 406.521 ms | 412.741 ms | 417.591 ms |

Hybrid 质量综合分比 Dense 低 `0.035767`，门禁结论为 `recommended_default=dense`。
报告位置：

- `evaluation/results/workspace-v2-retrieval-20260809/report.json`
- `evaluation/results/workspace-v2-retrieval-20260809/details.csv`

## 4. 真实多轮与回答基线

12 个多轮样本真实运行上下文改写和三路检索：

- 历史依赖判断准确率：100%。
- 两个话题切换样本因模型没有原样复制当前问题而触发严格安全回退，回退率 16.67%。
- Dense：命中率 90%、Recall@8 85%、全证据召回率 80%。
- 独立问题规范化精确字符串匹配率仅 16.67%；模型对依赖型追问使用了语义等价改写，因此该指标
  只能作为严格格式信号，不能单独代表改写质量。

报告位置：`evaluation/results/workspace-v2-context-20260809/`。

另外选择 6 个代表样本，覆盖部署指代、话题切换、L2 易错点、停止条件、跨文档和无答案，
对 NaiveRAG、DeepSearch、ChainOfRAG 运行真实回答：18/18 成功。

| Agent | 答案要点覆盖 | 全证据召回 | 声明支持率 | 平均延迟 | 平均 Token | 平均 LLM 调用 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| NaiveRAG | 100% | 100% | 81.51% | 12.09 s | 2573.83 | 2.00 |
| DeepSearch | 92.00% | 100% | 71.76% | 16.18 s | 7138.50 | 5.83 |
| ChainOfRAG | 52.00% | 80% | 83.33% | 9.35 s | 5708.83 | 5.17 |

本轮报告在当前解析器和 `2.2.0` 数据集上真实重跑，不再依赖离线补算；三者拒答准确率均为
100%。这是 6 题冒烟基线，不足以宣称某 Agent 整体更优。
报告位置：`evaluation/results/workspace-v2-answer-smoke-20260809/`。

## 5. 真实浏览器验证

使用临时 SQLite 数据库打开生产构建，验证：

- `partially_grounded` 显示醒目但非阻断的部分支持提示。
- 原始 Markdown 与代码块始终保留，代码块前景/背景对比清晰，Claim 作为可展开的附加核验区显示。
- supported 声明显示“已有依据”并可打开 Citation。
- 伪造 Evidence 对应声明显示“引用无效”，不出现可点击引用。
- 桌面来源抽屉与声明列表同时显示时无覆盖、截断或水平溢出。
- DOM 中保留回答声明、引用来源和带文件名/页码的按钮名称。
- 浏览器 Console 为 0 warning / 0 error。

测试数据和预览服务未写入产品正式数据库。

## 6. 诚实边界

- 声明状态首先证明“回答使用了本次有效 Evidence 编号”，不等同于独立 NLI 已证明语义蕴含。
- 72 题来源于 3 份项目资料，能做版本内回归，不能代表开放领域或生产流量分布。
- 无答案检索仍会返回候选片段，因此 `no_answer_false_positive_rate` 不能直接当作最终拒答质量；
  回答模式要结合 `refusal_accuracy`。
- 多轮精确改写匹配对语义等价改写过严，应继续增加人工或受约束语义判定，但不能用同一回答模型
  无校验地给自己打分。
- 当前 Dense 结论只适用于本数据、模型、分块、索引和参数快照；切换 Embedding 或语料后必须重跑。
