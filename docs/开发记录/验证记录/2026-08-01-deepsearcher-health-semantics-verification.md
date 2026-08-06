# DeepSearcher 健康检查语义验证记录

日期：2026-08-01  
范围：优化清单 O-01（liveness、readiness、degraded）

## 1. 交付契约

| 入口 | 语义 | 外部调用 | 状态码 |
| --- | --- | --- | --- |
| `GET /health/live` | 核心进程存活 | 无 | 存活即 200 |
| `GET /health/ready` | 默认运行时与 Milvus 就绪 | 最小 Milvus RPC | ready 为 200，not_ready 为 503 |
| `GET /health` | readiness 兼容入口 | 同上 | 同上 |
| `POST /health/diagnostics` | LLM、Embedding 深度诊断 | 真实最小模型调用 | ready/degraded 为 200，必需依赖失败为 503 |
| `GET /api/health/live` | 用户工作台进程存活 | 无 | 存活即 200 |
| `GET /api/health` | 聚合核心 readiness | 调用核心 readiness | ready/degraded 为 200，not_ready 为 503 |
| `POST /api/health/diagnostics` | 聚合核心深度诊断 | 通过 BFF 内部服务令牌调用核心 | 同核心诊断 |

深度诊断需要 `X-DeepSearcher-Service-Token`。核心按运行时实例缓存 LLM 与 Embedding 结果，默认 300 秒；普通 readiness 不调用模型。所有失败只返回稳定错误码、状态和可重试标记，不返回异常文本、URL 或凭据。

## 2. 自动化回归

执行结果：

- `uv run ruff check .`：通过。
- `uv run pytest -q`：654 passed、9 skipped。
- `npm run typecheck`：通过。
- `npm test -- --run`：4 个测试文件、22 项测试全部通过。
- `npm run build`：585 个模块完成生产构建。

新增自动化覆盖运行时初始化失败仍存活、Milvus 探测失败、深度诊断鉴权、真实 Provider 401 分类、结果缓存、工作台聚合和工作台独立 liveness。全量测试仍有一条既有 Docling crawler mock 协程告警，与本次修改无关。

## 3. 真实依赖与故障注入

正常状态：

- 核心 liveness：`200/alive`。
- 核心 readiness：`200/ready`，`runtime` 与 `vector_db` 均为 `ready`，模型为 `DEEP_PROBE_NOT_RUN`。
- 工作台 readiness：`200/ready`。
- 首次真实深度诊断约 1.8 秒，四个组件均为 `ready`；缓存命中约 0.04 秒。

Milvus 停机：

- 核心 liveness 继续返回 `200/alive`。
- 核心 readiness 返回 `503/not_ready`，`vector_db.code=DEPENDENCY_UNAVAILABLE`。
- 工作台返回 `503/not_ready`，`milvus.code=DEPENDENCY_UNAVAILABLE`，没有退化为 TCP 端口判断。
- 重启 Milvus 后，核心 readiness 恢复 `200/ready`。

核心后端离线：

- 工作台 liveness 继续返回 `200/alive`。
- 工作台聚合健康返回 `503/not_ready`，`fastapi.code=BACKEND_OFFLINE`；Milvus、LLM 与 Embedding 标记为 `BACKEND_UNREACHABLE/unknown`，避免伪报依赖离线。

模型鉴权失败：

- 使用隔离端口、隔离运行时数据库和无效测试密钥启动临时核心进程。
- 深度诊断返回 `200/degraded`。
- LLM 与 Embedding 均返回 `not_ready/PROVIDER_AUTH_FAILED/retryable=false`；运行时与 Milvus 仍为 `ready`。
- 临时进程已停止，未修改当前服务配置。

所有故障注入结束后，Docker、Milvus、核心 API 和用户工作台均恢复 ready。

## 4. 浏览器验收

在 `http://127.0.0.1:8700/console` 使用真实浏览器验证：

- 初始刷新只做 readiness，LLM 与 Embedding 显示“未探测”。
- 点击“深度检查”后，浏览器发出 `POST /api/health/diagnostics`，四个组件均显示“就绪”。
- 浏览器控制台 0 错误、0 警告。
- 截图：`output/playwright/o01-health/console-deep-health-ready.png`。

## 5. 结论

O-01 验收完成。端口连通、进程存活、必需依赖就绪和模型能力可用已成为不同层级；Milvus 未就绪、核心后端离线和模型鉴权失败会返回可机器识别的不同状态与错误码。
