# DeepSearcher 用户工作台 P0 实施计划

> 本计划对应 `docs/开发记录/设计规范/2026-07-23-deepsearcher-user-workspace.md`。  
> 当前阶段只定义实施顺序、接口契约和验收方法，不代表下述能力已经实现。

**Goal:** 在保留现有学习控制台的前提下，增加本地单用户的知识库问答工作台，使用户可以创建知识库、上传 PDF、查看真实处理状态、进行连续问答并核对引用来源。

**Architecture:** 继续采用模块化单体和两个本地进程。React SPA 通过同源产品 API 访问知识库、文档、任务和会话；产品 API 使用 SQLite 保存业务数据，并调用现有 DeepSearcher FastAPI 完成入库和查询。Milvus 只保存向量与切片，不承担会话、任务或页面列表数据。

**Tech Stack:** React 19、TypeScript、Vite、React Router、TanStack Query、Heroicons、Python 3.10+、FastAPI、Pydantic、SQLAlchemy 2、Alembic、SQLite、Milvus、Pytest、Vitest、Testing Library。

---

## 1. 目标架构

```text
Browser
  │
  │ same-origin /api/*
  ▼
frontend.server :8600
  ├─ React 静态资源
  └─ 产品 API
      ├─ SQLite：知识库、文档、任务、对话、消息
      └─ DeepSearcher FastAPI :8500
          ├─ Loader / Splitter / Embedding
          ├─ Agent 查询
          └─ Milvus：向量与切片
```

P0 不拆微服务、不引入 Redis 或 Celery。文档处理可以使用受控后台任务，但必须将 `queued / processing / ready / failed` 写入 SQLite。应用重启时，将遗留的 `queued` 或 `processing` 任务标为失败并允许重试，不能一直显示处理中。

## 2. 建议文件结构

```text
frontend/
├─ server.py                   # 静态资源、应用装配和健康检查
├─ product/
│  ├─ db.py                    # SQLite 连接与会话
│  ├─ models.py                # SQLAlchemy 模型
│  ├─ schemas.py               # Pydantic 请求与响应
│  ├─ repositories.py          # 数据访问
│  ├─ services/
│  │  ├─ knowledge_bases.py
│  │  ├─ documents.py
│  │  ├─ conversations.py
│  │  └─ citations.py
│  └─ routes/
│     ├─ knowledge_bases.py
│     ├─ documents.py
│     └─ conversations.py
├─ src/
│  ├─ app/
│  │  ├─ App.tsx
│  │  ├─ router.tsx
│  │  └─ query-client.ts
│  ├─ features/
│  │  ├─ chat/
│  │  ├─ knowledge/
│  │  ├─ citations/
│  │  └─ console/
│  ├─ components/
│  ├─ api/
│  └─ styles/
└─ tests/

deepsearcher/
├─ trace.py                    # 扩展安全引用字段
└─ agent/                      # 增加显式知识库范围
```

现有 `frontend/src/App.jsx` 和 `TracePanel.jsx` 迁入 `features/console/`，避免重写已经验证过的学习控制台。

## 3. 产品数据模型

### 3.1 KnowledgeBase

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID 字符串 | 产品稳定 ID |
| `name` | 字符串 | 用户可见名称 |
| `description` | 字符串 | 可选描述 |
| `collection_name` | 字符串 | 内部安全 Milvus 集合名 |
| `is_current` | 布尔 | P0 当前知识库 |
| `created_at` | 时间 | 创建时间 |
| `updated_at` | 时间 | 更新时间 |

用户名称与内部 `collection_name` 分离。内部名称由系统生成，避免中文、空格和重名直接传给向量数据库。

### 3.2 Document

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID 字符串 | 稳定文档 ID |
| `knowledge_base_id` | UUID 字符串 | 所属知识库 |
| `display_name` | 字符串 | 原始文件名的安全版本 |
| `storage_path` | 字符串 | 服务端保存路径，不返回浏览器 |
| `size_bytes` | 整数 | 文件大小 |
| `sha256` | 字符串 | 重复文件识别 |
| `status` | 枚举 | `queued/processing/ready/failed` |
| `error_code` | 字符串 | 安全错误分类 |
| `error_message` | 字符串 | 用户可见安全摘要 |
| `created_at` | 时间 | 添加时间 |
| `updated_at` | 时间 | 更新时间 |

### 3.3 IngestJob

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID 字符串 | 任务 ID |
| `document_id` | UUID 字符串 | 对应文档 |
| `status` | 枚举 | `queued/processing/succeeded/failed` |
| `attempt` | 整数 | 重试次数 |
| `started_at` | 时间 | 开始时间 |
| `finished_at` | 时间 | 结束时间 |

P0 不展示伪百分比。任务只有真实离散状态。

### 3.4 Conversation / Message

`Conversation`：

- `id`
- `knowledge_base_id`
- `title`
- `created_at`
- `updated_at`

`Message`：

- `id`
- `conversation_id`
- `role`：`user/assistant`
- `content`
- `status`：`pending/succeeded/failed`
- `created_at`

### 3.5 Citation

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID 字符串 | 引用 ID |
| `message_id` | UUID 字符串 | 所属回答 |
| `document_id` | UUID 字符串或空 | 可映射时保存 |
| `display_name` | 字符串 | 安全文件名 |
| `page_number` | 整数或空 | 后端真实提供时保存 |
| `chunk_index` | 整数或空 | 稳定切片序号 |
| `text` | 字符串 | 安全截断的原文片段 |
| `supported` | 布尔 | 是否通过支持过滤 |

## 4. P0 产品 API 契约

浏览器只调用 `/api/*`，不直接访问 DeepSearcher FastAPI。

## 4.1 知识库

### `GET /api/knowledge-bases`

响应：

```json
{
  "items": [
    {
      "id": "kb_...",
      "name": "AI 全栈学习资料",
      "description": "",
      "document_count": 8,
      "ready_document_count": 8,
      "is_current": true,
      "updated_at": "2026-07-23T10:30:00+08:00"
    }
  ]
}
```

### `POST /api/knowledge-bases`

请求：

```json
{
  "name": "AI 全栈学习资料",
  "description": "项目源码和学习文档"
}
```

响应：`201 Created`，返回完整知识库对象。

### `GET /api/knowledge-bases/{knowledge_base_id}`

返回知识库详情和文档汇总，不返回内部 `collection_name`。

### `PUT /api/knowledge-bases/{knowledge_base_id}/current`

将该知识库设为当前知识库。P0 本地单用户只允许一个 `is_current=true`。

## 4.2 文档

### `GET /api/knowledge-bases/{knowledge_base_id}/documents`

响应：

```json
{
  "items": [
    {
      "id": "doc_...",
      "display_name": "DeepSearcher 技术白皮书.pdf",
      "size_bytes": 1286041,
      "status": "ready",
      "error": null,
      "created_at": "2026-07-23T10:31:00+08:00"
    }
  ]
}
```

### `POST /api/knowledge-bases/{knowledge_base_id}/documents`

- 内容类型：`multipart/form-data`。
- 字段：`file`。
- 浏览器和服务端都校验 PDF、空文件和 20 MiB 上限。
- 响应：`202 Accepted`。

```json
{
  "document": {
    "id": "doc_...",
    "display_name": "DeepSearcher 技术白皮书.pdf",
    "status": "queued"
  },
  "job": {
    "id": "job_...",
    "status": "queued"
  }
}
```

### `GET /api/documents/{document_id}`

用于轮询真实处理状态。P0 建议在 `queued/processing` 时每 2 秒刷新，页面不可见时暂停。

### `POST /api/documents/{document_id}/retry`

仅允许 `failed` 文档调用，返回新的任务 ID。

### 删除接口

`DELETE /api/documents/{document_id}` 只有在 Milvus 支持按稳定 `document_id` 删除向量后才启用。P0 实施初期不得只删除 SQLite 记录。

## 4.3 对话

### `GET /api/conversations?limit=20`

返回最近对话，按 `updated_at` 倒序。

### `POST /api/conversations`

请求：

```json
{
  "knowledge_base_id": "kb_..."
}
```

响应：`201 Created`。首次回答成功后，根据第一个问题生成或截断标题。

### `GET /api/conversations/{conversation_id}`

返回会话、知识库摘要、消息和每条回答的引用。

### `POST /api/conversations/{conversation_id}/messages`

请求：

```json
{
  "content": "DeepSearcher 的查询流程由哪些核心模块组成？"
}
```

P0 可以同步等待查询完成，响应：

```json
{
  "user_message": {
    "id": "msg_user_...",
    "role": "user",
    "content": "DeepSearcher 的查询流程由哪些核心模块组成？",
    "status": "succeeded"
  },
  "assistant_message": {
    "id": "msg_assistant_...",
    "role": "assistant",
    "content": "DeepSearcher 的查询流程主要包括……",
    "status": "succeeded",
    "answer_state": "grounded",
    "citations": [
      {
        "id": "citation_...",
        "index": 1,
        "document_id": "doc_...",
        "display_name": "DeepSearcher 技术白皮书.pdf",
        "page_number": 12,
        "text": "……",
        "supported": true
      }
    ]
  }
}
```

`answer_state`：

- `grounded`：存在可展示的支持来源。
- `insufficient_evidence`：没有足够支持证据。
- `failed`：请求失败。

如果回答正文目前没有 `[1]` 标记，服务端不能仅靠前端猜测插入引用位置。P0 可以先在回答末尾展示“参考来源 [1][2]”，等生成提示或结构化响应支持行内引用后再升级。

## 4.4 错误响应

所有产品 API 使用统一格式：

```json
{
  "error": {
    "code": "DOCUMENT_INVALID_PDF",
    "message": "所选文件不是有效的 PDF。",
    "retryable": false
  }
}
```

不得把 Python 异常文本、本地绝对路径或第三方响应原文直接返回浏览器。

## 5. DeepSearcher 核心能力改造

## 5.1 显式知识库范围

当前查询由 Collection Router 从所有集合中选择，不能保证用户选择知识库后只检索该集合。P0 必须增加显式范围：

```python
query_with_trace(
    original_query,
    max_iter=3,
    collection_names=["kb_xxx"],
)
```

Agent 规则：

- 提供 `collection_names` 时，只检索这些集合，不再调用 Collection Router 扩大范围。
- 未提供时保持现有行为，确保 CLI、示例和学习控制台兼容。
- Trace 中记录最终实际使用的集合。

## 5.2 稳定文档和引用元数据

当前 PDFLoader 将整份 PDF 合并为一个 Document，只保留文件路径，无法提供真实页码。建议调整为每页一个 `Document`：

```python
Document(
    page_content=page_text,
    metadata={
        "reference": safe_reference,
        "document_id": document_id,
        "page_number": page_number,
    },
)
```

切片时补充稳定 `chunk_index`，并确保 Milvus 的 `metadata` 保存这些字段。

Trace 安全响应增加：

- `document_id`
- `page_number`
- `chunk_index`
- `display_name`

仍然保留文本长度限制和 URL 查询参数清理。

## 5.3 文档删除边界

当前 `BaseVectorDB` 只定义集合级 `clear_db`。如 P0 要支持单文档删除，需要新增统一能力：

```python
delete_by_document_id(collection: str, document_id: str) -> int
```

Milvus、Qdrant、Oracle 和 Azure Search 等适配器不能一次全部完成时：

- P0 只在当前默认 Milvus 实现该能力并明确兼容范围；或
- 将单文档删除延后到 P1。

不得让各适配器静默执行不一致行为。

## 6. 前端状态与交互策略

- 服务端状态使用 TanStack Query 管理。
- 输入框、抽屉、选中引用等短期状态使用 React Hooks。
- P0 不引入 Redux。
- 路由使用 React Router。
- API 请求和响应全部定义 TypeScript 类型。
- 保留现有 Heroicons，不手写 SVG。
- 回答正文使用经过安全配置的 Markdown 渲染器。
- 查询失败后保留输入和用户消息。
- 上传任务轮询只反映后端真实状态。
- 不把知识库、会话或回答内容只保存在浏览器 Local Storage。

## 7. 实施任务

### Task 1：建立安全基线

- [x] 运行现有 Python 和前端测试。
- [x] 构建现有前端并保存基线截图。
- [x] 确认当前学习控制台 `/` 的查询和 PDF 校验行为。
- [x] 将已选视觉稿作为实现视觉基准。

验证：

```powershell
uv run --frozen pytest frontend/tests tests/test_trace.py tests/test_query_api.py
npm --prefix frontend test
npm --prefix frontend run build
```

### Task 2：建立产品数据层

- [x] 添加 SQLAlchemy、Alembic 和 SQLite 配置。
- [x] 实现 KnowledgeBase、Document、IngestJob、Conversation、Message、Citation 模型。
- [x] 建立首次迁移。
- [x] 增加仓储层和事务测试。
- [x] 确保数据库文件和上传目录不提交到 Git。

### Task 3：补齐核心查询范围

- [x] 给在线查询入口增加可选 `collection_names`。
- [x] 在 NaiveRAG、ChainOfRAG 和 DeepSearch 中优先使用显式范围。
- [x] 保持未传范围时的当前 Collection Router 行为。
- [x] 增加单集合、多集合、非法集合和向后兼容测试。

### Task 4：补齐引用元数据

- [x] PDFLoader 改为保留页级 Document。
- [x] 为文档和切片增加 `document_id/page_number/chunk_index`。
- [x] 扩展 Trace 安全序列化。
- [x] 验证绝对路径和 URL 查询参数仍不会泄漏。
- [x] 增加页码、截断、空元数据和旧数据兼容测试。

### Task 5：实现产品 API

- [x] 实现知识库列表、创建、详情和切换。
- [x] 实现 multipart PDF 上传和双重校验。
- [x] 实现后台入库任务和状态恢复。
- [x] 实现最近对话、创建对话和消息查询。
- [x] 将 Trace 中的支持片段映射为 Citation。
- [x] 实现统一安全错误格式。
- [x] 为所有路由增加 FastAPI 测试。

### Task 6：迁移前端基础设施

- [x] 添加 TypeScript、React Router 和 TanStack Query。
- [x] 按 `App.tsx / ConsoleApp.jsx / product-api.ts / workspace.css` 拆分应用、技术控制台、API 与样式职责。
- [x] 将现有学习控制台迁入 `/console`，保持现有测试通过。
- [x] 增加应用框架、左侧导航和路由级错误状态。
- [x] 使用选定视觉稿建立颜色、间距、圆角和字体变量。

### Task 7：实现知识库闭环

- [x] 实现知识库空状态和创建弹窗。
- [x] 实现知识库列表与当前知识库切换。
- [x] 实现知识库详情和文档列表。
- [x] 实现上传、轮询、成功、失败和重试状态。
- [x] 验证刷新页面后状态仍来自服务端。

### Task 8：实现问答闭环

- [x] 实现新对话空状态。
- [x] 实现问题提交、等待、成功、证据不足和失败状态。
- [x] 实现回答 Markdown 展示和操作区。
- [x] 实现最近对话和对话详情恢复。
- [x] 实现继续追问的真实上下文策略。
- [x] 实现引用编号和右侧来源抽屉。

### Task 9：交互和可访问性

- [x] 支持 `Enter` 发送、`Shift + Enter` 换行。
- [x] 所有图标按钮增加可访问名称和焦点状态。
- [x] 抽屉打开后管理焦点，关闭后回到触发引用。
- [x] 错误后保留输入。
- [x] 在设计基准、1024、768 和 390 宽度检查布局。

### Task 10：自动验证和设计 QA

- [x] 运行 Python、前端单元测试和生产构建。
- [x] 启动本地后端和用户工作台。
- [x] 在设计基准视口打开 `/` 和一条完成回答的对话。
- [x] 测试创建知识库、上传、提问、打开引用和继续追问。
- [x] 检查浏览器控制台没有错误。
- [x] 将实现截图与选定视觉稿放在同一比较输入中进行设计 QA。
- [x] 修复所有 P0/P1/P2 差异，直到 `design-qa.md` 为 `final result: passed`。

最终验证：

```powershell
uv run --frozen pytest
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

## 8. 建议交付顺序

按可以演示的纵向切片交付，而不是先做完全部后端再统一做页面：

1. **切片 A：用户工作台外壳**  
   完成路由、导航、视觉变量和 `/console` 迁移。

2. **切片 B：可信问答**  
   使用已有知识库完成提问、真实回答、来源抽屉和失败状态。

3. **切片 C：知识库创建与 PDF 入库**  
   增加 SQLite 数据、上传任务、文档状态和显式查询范围。

4. **切片 D：会话与恢复**  
   增加最近对话、消息保存、连续追问和刷新恢复。

5. **切片 E：完整验收**  
   跑通首次使用流程、异常状态、响应式检查和设计 QA。

## 9. P0 完成定义

只有同时满足以下条件才算完成：

- 用户可以从空数据开始创建知识库并上传真实 PDF。
- 文档状态来自服务端持久化记录。
- 用户选择的知识库确实限制了查询范围。
- 回答引用来自真实检索结果，不使用前端示例数据。
- 页码只在 PDFLoader 提供真实页级元数据时展示。
- 最近对话刷新后仍然存在。
- 无证据、服务离线和处理失败都有可恢复界面。
- 当前学习控制台仍可通过 `/console` 使用。
- 所有自动测试和生产构建通过。
- 浏览器主流程验证通过。
- `design-qa.md` 的最终结果为 `passed`。
