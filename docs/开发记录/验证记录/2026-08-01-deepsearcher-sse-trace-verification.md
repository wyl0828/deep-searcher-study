# DeepSearcher 安全 SSE 与 Trace 治理验证记录

日期：2026-08-01  
范围：问题清单 S-02；核心 SSE、Trace v3、产品 BFF、React 增量状态、断线取消和敏感字段治理。

## 1. 验收结论

S-02 的产品问答链路已形成闭环：浏览器用 POST 发问题，经产品 BFF 连接核心
`POST /query/stream`；核心发送受控阶段，BFF 只转发白名单字段；完成时保存回答与支持引用，
停止或断连时关闭上游流、标记未完成回答并最终释放 runtime 租约。

Trace 和实时阶段都是程序显式埋点，不是模型隐藏思维链。实时阶段不持久化，响应使用
`Cache-Control: no-store, no-transform`、`X-Accel-Buffering: no` 和
`X-Trace-Retention: transient`。普通用户完成事件不包含核心 Trace。

## 2. 协议与隐私边界

- 事件协议版本为 1，信封字段为 `version`、`request_id`、`sequence`、`event`、`data`。
- 阶段事件为 `started`、`routing`、`iteration`、`retrieval`、`support`、`reflection`；
  终止事件为 `completed`、`error`、`cancelled`。
- 实时阶段只含 Agent 安全标识、回退布尔值、轮次、召回数量、支持数量和充分性布尔值。
- Trace v3 不保存原问题、子查询、Collection 名或中间答案；文档只保留最终受支持证据，
  最多 5 条，每条正文最多 600 字。
- 自由文本会清理控制字符并脱敏常见凭据、Bearer token、`sk-` key、邮箱和本地绝对路径；
  URL 去除 query 与 fragment，metadata 采用字段白名单和类型/范围验证。
- 产品 BFF 不透传核心 `completed.data.trace`，而是返回数据库序列化后的用户消息、助手消息和引用。

旧学习控制台和产品 BFF 均已迁移为 POST Body；GET `/query/` 已移除，原问题不再由项目代码
写入查询 URL。本记录与后续 S-03、安全补充收口记录共同证明 Trace、流式协议与日志边界。

## 3. 自动化证据

关键覆盖：

- `tests/test_trace.py`：Trace v3 白名单、支持证据限制、正文/路径/凭据脱敏、协作取消。
- `tests/test_query_api.py`：核心 SSE 顺序、安全完成事件和安全错误事件。
- `frontend/tests/test_product_api.py`：BFF 阶段重建、调试字段剔除、最终消息/引用落库、关闭流后失败状态。
- `frontend/src/product-api.test.ts`：跨网络分片的 SSE 解析和稳定错误映射。
- `frontend/src/App.test.jsx`：阶段文案、非思维链说明、停止按钮、AbortSignal 和自动滚动。
- `tests/integration/test_sse_disconnect.py`：真实 Uvicorn 客户端断线后 `cancelled=true`、
  `finished=true`、`runtime_references=0`、`cleanup_tasks=0`。
- `tests/test_runtime_scripts.py`：Windows 端口可绑定探针与动态端口记录往返。

本轮执行结果：

```text
uv run pytest -q
637 passed, 9 skipped, 1 existing crawler coroutine warning

S-02/启动脚本增量定向测试
60 passed

cd frontend && npx tsc --noEmit
通过

cd frontend && npm test -- --run
4 files, 22 tests passed

cd frontend && npm run build
585 modules transformed, build passed

uv run ruff check .
All checks passed
```

## 4. 真实服务证据

真实 Docker Milvus、模型 API 和产品工作台请求：

```text
HTTP 200
Cache-Control: no-store, no-transform
X-Trace-Retention: transient

1.597s started
4.058s routing (ChainOfRAG)
4.062s iteration 1
7.712s retrieval (10 candidates)
10.024s support (2 supported)
11.459s reflection (enough=true)
13.027s completed
```

完成事件落库状态为 `succeeded`，有 2 条引用。扫描浏览器收到的事件，没有出现 `trace`、
`subquery`、`metadata`、`original_query`、`api_key` 或 Windows 本地路径。

## 5. 浏览器证据

Playwright 在真实工作台执行了提交、阶段出现、停止和状态恢复：

- 进度卡片包含“正在依据知识库生成回答”“已开始处理问题”“停止生成”和
  “这里展示的是系统执行阶段，不是模型的思维链”。
- 自动滚动后卡片完整位于粘性输入框上方：卡片底部约 `754.9px`，输入框区域顶部约
  `776.7px`，`unobscured=true`。
- 点击停止后，持久化助手消息为失败/停止状态，页面显示“本次回答已停止”和“重新开始”。
- 浏览器控制台为 0 error、0 warning；网络记录显示消息使用 POST `/messages/stream`，响应 200。

截图位于：

- `output/playwright/s02-sse/.playwright-cli/s02-progress-card-final.png`
- `output/playwright/s02-sse/.playwright-cli/s02-progress-final.png`
- `output/playwright/s02-sse/.playwright-cli/s02-stopped.png`

## 6. 启动可靠性补充

真实重启时发现 Docker/Hyper-V 将 Windows `8553–8652` 划为动态保留区间，导致默认
8600/8650 无法绑定。启动脚本现会探测实际可绑定性，优先默认端口，失败时自动回退并把选择写入
`logs/runtime/ports.json`；`status.ps1`、`stop.ps1` 和浏览器启动共用该记录。本机验证自动选择
API 8750、工作台 8700，并在停止/重新启动后保持一致和健康。
