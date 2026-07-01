# DeepSearcher Agent Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏现有 `query()` 和 `/query/` 默认响应的前提下，返回真实结构化查询过程，并在中文学习控制台中以可展开时间线展示。

**Architecture:** 新增请求级 `TraceCollector`，由 `query_with_trace()` 创建并通过可选参数传入 RAGRouter 与 ChainOfRAG。Collector 从现有局部变量接收事件，序列化安全文档摘要；FastAPI 仅在 `include_trace=true` 时返回 Trace，前端代理默认启用并渲染折叠面板。

**Tech Stack:** Python 3.10、FastAPI、DeepSearcher Agent、pytest、React 19、Vitest、Testing Library。

---

### Task 1: 建立版本化 TraceCollector

**Files:**
- Create: `deepsearcher/trace.py`
- Create: `tests/test_trace.py`

- [ ] **Step 1: 写失败测试**

测试 `TraceCollector` 能记录 Agent、一次迭代、Collection、召回文档、中间答案、支持文档和分阶段 Token；断言输出 `version == 1`，文档正文截断到 600 字符，且不包含 `embedding` 与原始 `metadata`。

```python
collector = TraceCollector("question")
collector.select_agent("ChainOfRAG", 11)
collector.start_iteration(1)
collector.record_subquery("subquery", 12)
collector.record_collections(["deepsearcher"], 13)
collector.record_documents_retrieved([result])
collector.record_intermediate_answer("answer", 14)
collector.record_documents_supported([result], 15)
trace = collector.build(total_tokens=65, final_results=[result], final_answer_tokens=16)
assert trace["iterations"][0]["retrieved_documents"][0]["supported"] is True
```

- [ ] **Step 2: 运行 RED**

Run: `uv run --frozen pytest tests/test_trace.py -q`

Expected: FAIL，原因是 `deepsearcher.trace` 不存在。

- [ ] **Step 3: 实现 Collector**

提供 `select_agent`、`start_iteration`、`record_subquery`、`record_collections`、`record_documents_retrieved`、`record_intermediate_answer`、`record_documents_supported`、`record_reflection` 和 `build`。所有方法在没有当前迭代时安全降级；序列化只保留 `text`、`reference`、`score`、`supported`。

- [ ] **Step 4: 运行 GREEN**

Run: `uv run --frozen pytest tests/test_trace.py -q`

Expected: 全部通过。

### Task 2: 将 Collector 接入一次真实 Agent 执行

**Files:**
- Modify: `deepsearcher/agent/rag_router.py`
- Modify: `deepsearcher/agent/chain_of_rag.py`
- Modify: `deepsearcher/online_query.py`
- Modify: `tests/agent/test_rag_router.py`
- Modify: `tests/agent/test_chain_of_rag.py`
- Modify: `tests/test_trace.py`

- [ ] **Step 1: 写失败测试**

新增测试验证：

```python
answer, results, tokens, trace = query_with_trace("question", max_iter=2)
assert trace["agent"] == "ChainOfRAG"
assert len(trace["iterations"]) == 2
assert trace["summary"]["total_tokens"] == tokens
```

同时验证旧 `query()` 仍返回三元组。

- [ ] **Step 2: 运行 RED**

Run: `uv run --frozen pytest tests/test_trace.py tests/agent/test_rag_router.py tests/agent/test_chain_of_rag.py -q`

Expected: FAIL，原因是 Agent 尚未写入 Trace。

- [ ] **Step 3: 接入执行点**

`RAGRouter` 在选中 Agent 后记录名称和路由 Token，并把 `trace_collector` 继续传给 Agent。`ChainOfRAG.retrieve()` 从 kwargs 取出 Collector，在现有循环中记录每轮事件，不增加任何 LLM 或 Milvus 调用。

`online_query.py` 新增：

```python
def query_with_trace(original_query: str, max_iter: int = 3):
    collector = TraceCollector(original_query)
    answer, results, tokens = configuration.default_searcher.query(
        original_query,
        max_iter=max_iter,
        trace_collector=collector,
    )
    return answer, results, tokens, collector.build(tokens, results)
```

- [ ] **Step 4: 运行 GREEN 与回归测试**

Run: `uv run --frozen pytest tests/test_trace.py tests/agent -q`

Expected: 新旧 Agent 测试全部通过。

### Task 3: 扩展兼容的 HTTP API 与本地代理

**Files:**
- Modify: `main.py`
- Modify: `frontend/server.py`
- Modify: `frontend/tests/test_server.py`

- [ ] **Step 1: 写失败测试**

新增纯函数响应映射测试：默认模式没有 `trace`，启用模式保留 `trace`；前端代理把 DeepSearcher 的 `trace` 返回给浏览器。

```python
assert build_query_response("answer", 10, None) == {
    "result": "answer", "consume_token": 10
}
assert build_query_response("answer", 10, {"version": 1})["trace"]["version"] == 1
```

- [ ] **Step 2: 运行 RED**

Run: `uv run --frozen pytest frontend/tests/test_server.py tests/test_trace.py -q`

Expected: FAIL，原因是 Trace HTTP 映射尚不存在。

- [ ] **Step 3: 实现兼容参数**

`main.py` 的 `/query/` 增加 `include_trace: bool = False`。False 时继续调用 `query()`；True 时调用 `query_with_trace()` 并追加 `trace`。

`frontend/server.py` 请求后端时固定发送 `include_trace=true`，响应中追加：

```python
"trace": payload.get("trace")
```

不得覆盖 `main.py` 中用户已有的 dotenv 与 batch size 修改。

- [ ] **Step 4: 运行 GREEN**

Run: `uv run --frozen pytest frontend/tests/test_server.py tests/test_trace.py -q`

Expected: 全部通过。

### Task 4: 构建可展开查询过程面板

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/api.test.js`
- Create: `frontend/src/TracePanel.jsx`
- Create: `frontend/src/TracePanel.test.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/App.test.jsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: 写失败测试**

API 测试断言 `queryDeepSearcher()` 返回 `trace`。组件测试渲染两轮 Trace，验证默认展开第一轮、第二轮可点击展开、召回原文可展开，以及“不是隐藏思维链”说明存在。

- [ ] **Step 2: 运行 RED**

Run: `npm test -- src/api.test.js src/TracePanel.test.jsx src/App.test.jsx`

Expected: FAIL，原因是 TracePanel 不存在且 API 未映射 Trace。

- [ ] **Step 3: 实现组件与集成**

`TracePanel` 提供“查询过程 / 系统日志”标签、Agent 概览、迭代 accordion 和文档 accordion。`App` 在查询成功后保存 Trace；无 Trace、加载和失败都有明确中文状态。复用现有色彩、边框、字体和 Heroicons，不创建新路由。

- [ ] **Step 4: 运行 GREEN 与构建**

Run:

```powershell
npm test
npm run build
```

Expected: 全部测试与构建通过。

### Task 5: 真实查询与视觉验收

**Files:**
- Modify: `design-qa.md`
- Create: `frontend/screenshots/agent-trace-1440x1024.png`

- [ ] **Step 1: 重启 FastAPI 与前端**

重启 8500 载入 Trace API，重新构建并重启 8600。保持用户当前千问配置与 Milvus 数据不变。

- [ ] **Step 2: 执行真实中文查询**

问题：`Project Aurora 的负责人是谁，批准的正式上线日期是什么时候？`，`max_iter=3`。

验收响应：答案包含 `Lin Qiao` 或 `林乔` 与 `2026 年 9 月 15 日`；Trace 至少包含 1 轮、`deepsearcher` 和 1 个真实片段。

- [ ] **Step 3: 浏览器验证**

在 1440 × 1024 验证标签切换、迭代展开、文档展开、键盘可操作、无整页溢出和浏览器 console 无错误；保存截图。

- [ ] **Step 4: 更新设计 QA**

将现有视觉基准、旧控制台和新 Trace 截图放入同一比较证据。修复所有 P0/P1/P2，`design-qa.md` 保持 `final result: passed`。

- [ ] **Step 5: 完整验证**

Run:

```powershell
uv run --frozen pytest tests frontend/tests -q
npm test
npm run build
git diff --check
```

Expected: 全部退出码为 0，用户现有未提交文件仍保持原修改。
