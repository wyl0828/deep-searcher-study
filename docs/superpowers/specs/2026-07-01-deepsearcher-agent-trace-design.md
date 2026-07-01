# DeepSearcher 真实查询过程清单设计

> 日期：2026-07-01  
> 前置界面：`frontend/` 中文双泳道学习控制台  
> 视觉基准：`docs/design-references/2026-07-01-deepsearcher-learning-console.png`

## 1. 目标

让用户在获得最终答案后，可以展开查看 DeepSearcher 实际执行过的查询步骤：使用了哪个 Agent、每轮生成了什么子问题、查询了哪些 Collection、召回了哪些原文片段、生成了什么中间答案，以及每个阶段消耗了多少 Token。

第一版是“真实但非流式”的查询过程清单。Trace 随最终答案一次性返回，不在查询过程中实时推送。

## 2. 用户看到什么

现有页面保持双泳道结构。底部右侧“事件日志”升级为双标签面板：

- **查询过程**：默认标签，展示结构化 Agent Trace。
- **系统日志**：保留现有前端可观测日志。

查询过程顶部展示 Agent 名称、总迭代次数、最终召回片段数和总 Token。下方按时间顺序显示可展开的迭代卡片：

1. 本轮子问题。
2. 实际查询的 Collection。
3. 召回片段数量。
4. 中间答案。
5. 本轮 Token 消耗。

展开“召回片段”后，显示相关度分数、来源引用和原文摘要。每个摘要最多显示 600 个字符，单轮最多展示前 5 条，避免页面失控；数量统计仍使用真实完整结果。

页面必须明确说明：查询过程是系统执行记录，不是模型隐藏思维链。

## 3. 不展示的内容

- 不展示或推断模型的隐藏思维链、系统提示词和供应商内部推理。
- 不把控制台日志解析结果冒充结构化 Trace。
- 不返回向量 embedding、API Key、环境变量、本机临时路径或 Milvus 凭据。
- 不在第一版实现 SSE、WebSocket 或逐 Token 流式更新。
- 不为展示 Trace 重复执行一次检索或 LLM 查询。

## 4. 后端架构

### 4.1 TraceCollector

新增独立的 `TraceCollector`，由一次请求创建并通过可选参数向下传递。没有 Collector 时，DeepSearcher 保持原行为。

Collector 只接收显式事件，不拦截标准输出，也不解析日志文本。事件包括：

- `agent_selected`
- `iteration_started`
- `subquery_generated`
- `collections_selected`
- `documents_retrieved`
- `intermediate_answer_generated`
- `documents_supported`
- `reflection_checked`
- `final_answer_generated`

Collector 在请求结束时生成版本化 JSON，首版 `version` 为 `1`。

### 4.2 Agent 接入

`RAGRouter` 在选择 Agent 后记录 `agent_selected`。`ChainOfRAG` 在现有执行点写入迭代事件；事件数据来自已经存在的局部变量，不增加 LLM 或向量库调用。

其他 Agent 暂不补齐细粒度迭代事件，但必须返回 Agent 名称、最终召回片段和总 Token。这样 Trace 接口对所有路由结果可用，ChainOfRAG 提供最完整视图。

### 4.3 公共 Python API

保留现有接口：

```python
query(original_query, max_iter=3) -> (answer, results, consume_token)
```

新增接口：

```python
query_with_trace(original_query, max_iter=3) -> (answer, results, consume_token, trace)
```

旧调用者不创建 Collector，也不承担 Trace 序列化成本。

## 5. Trace JSON

```json
{
  "version": 1,
  "agent": "ChainOfRAG",
  "original_query": "Project Aurora 的负责人是谁？",
  "iterations": [
    {
      "index": 1,
      "subquery": "Who owns Project Aurora?",
      "collections": ["deepsearcher"],
      "retrieved_documents": [
        {
          "text": "Project Aurora is owned by Lin Qiao...",
          "reference": "aurora-facts.pdf",
          "score": 0.91,
          "supported": true
        }
      ],
      "retrieved_count": 1,
      "intermediate_answer": "Lin Qiao owns Project Aurora.",
      "has_enough_information": null,
      "token_usage": {
        "subquery": 120,
        "retrieval_answer": 260,
        "support_filter": 90,
        "reflection": 0,
        "total": 470
      }
    }
  ],
  "summary": {
    "iteration_count": 1,
    "supported_document_count": 1,
    "total_tokens": 980
  }
}
```

`score`、`reference` 或反思结果不存在时返回 `null`，不得编造默认值。文档只保留安全字段；metadata 不原样透传。

## 6. HTTP API

现有 FastAPI `/query/` 新增可选参数：

```text
include_trace=false
```

- `false`：响应保持 `result` 与 `consume_token`，确保向后兼容。
- `true`：额外返回 `trace`。

前端本地代理默认请求 `include_trace=true`，并把 Trace 原样映射给浏览器；代理仍负责错误摘要和本地连接隔离。

## 7. 前端交互

- 查询开始时，查询过程标签显示“查询完成后展示真实过程”。
- 查询完成后，默认选中查询过程标签。
- 迭代卡片默认只展开第一轮，其余折叠。
- 用户可以展开/收起每轮和每条召回片段。
- 没有 Trace 的旧后端响应显示“当前后端未返回查询过程”，最终答案仍正常显示。
- 查询失败时保留错误信息与系统日志，不显示残缺 Trace。
- Trace 面板保持键盘可操作，并为展开按钮提供明确的可访问名称。

视觉上复用现有蓝、青、绿和红色令牌、细边框、字体与圆角，不增加新的页面路由，也不改变服务状态栏。

## 8. 错误与隐私边界

- Collector 失败不得导致主查询失败；序列化异常返回空 Trace 并记录服务端错误。
- 文档摘要在服务端截断，浏览器不能请求完整 embedding 或隐藏 metadata。
- Trace 仅存在于本次 HTTP 响应和页面内存，不写入数据库、日志文件或浏览器持久存储。
- 不把大模型自由文本当作结构化 Collection、分数或 Token 数据；这些字段必须来自程序变量。

## 9. 测试与验收

- 原 `query()` 返回签名和 `/query/` 默认 JSON 不变。
- `query_with_trace()` 在 ChainOfRAG 查询中返回版本 1 的结构化 Trace。
- Trace 的子问题、Collection、片段、分数、中间答案和 Token 来自一次真实执行。
- 不包含 embedding、API Key、系统提示词和未筛选 metadata。
- 前端能够显示多轮 Trace、折叠片段、无 Trace、加载和失败状态。
- 使用 Aurora PDF 和千问配置完成一次真实中文 `max_iter=3` 查询；答案必须包含 Lin Qiao 与 2026-09-15，Trace 至少包含 1 轮、`deepsearcher` Collection 和 1 个真实片段。
- Python 测试、前端测试、生产构建和浏览器视觉检查通过。

## 10. 用户现有改动

工作区当前已有用户未提交修改：

- `deepsearcher/config.yaml`：恢复千问模型并保留 Gemini 注释。
- `main.py`：加载 `.env`，为空 batch size 设置 256。
- `infra/milvus/docker-compose.yml`：增加自动重启策略。
- `start_server.bat`：本地启动脚本。

实现必须在这些改动之上做最小补丁，不得覆盖、回退或混入与 Trace 无关的修改。
