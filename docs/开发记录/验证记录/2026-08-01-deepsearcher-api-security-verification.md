# DeepSearcher API 稳定协议与安全边界验证记录

日期：2026-08-01  
范围：S-03 POST 查询、稳定错误信封、请求 ID、服务鉴权、查询限流、CORS、BFF 透传与日志泄漏防护。

## 1. 完成内容

- 同步查询由 GET Query String 改为 `POST /query` JSON Body；GET `/query/` 不再注册，应用关闭自动斜杠重定向。
- 核心 API 与用户工作台 BFF 的错误统一为：
  `error.code`、安全 `error.message`、`error.request_id`、`error.retryable`，响应头返回同一 `X-Request-ID`。
- Pydantic 校验、未知路由和未知异常不再返回请求内容、原始异常、绝对路径或供应商详情。
- 核心查询增加每进程固定窗口准入限制，默认 60 次/60 秒；429 返回 `Retry-After`。
- CORS 默认关闭，仅接受精确 HTTP(S) origin 白名单，通配符与带用户信息、路径、查询或 fragment 的值被忽略。
- 文件/网站入库、Provider 更新、Manifest、索引重建、删除、查询与 runtime 上下文都要求有效服务令牌；未配置令牌时受保护入口直接拒绝。
- `start.ps1` 为核心 API 与工作台注入同一枚不落盘随机服务令牌，并为管理入口生成独立令牌。
- BFF 将用户请求 ID 继续传给核心 API；核心 SSE 全部事件、BFF 响应头与浏览器请求使用同一 ID。
- Windows 停止脚本同时识别 `uvicorn.exe` launcher 与 Python-hosted Uvicorn 子进程，并要求命令行同时匹配当前项目路径和目标应用后才停止。

## 2. 自动化验证

核心 API 测试覆盖：

- 400：空查询或非法业务参数。
- 401：无服务令牌的配置切换与服务端文件读取。
- 404：未知资源与已移除的 GET `/query/`。
- 409：Collection/Embedding/版本冲突。
- 422：Pydantic 请求体校验。
- 429：查询固定窗口限流和 `Retry-After`。
- 503：runtime 或向量库不可用。
- 504：查询依赖超时分类。
- 500：未知查询失败。
- 请求 ID 合法值透传、非法值替换、HTTP/SSE 对齐。
- 可信与不可信 CORS 预检。
- 响应和错误日志不出现测试密钥、本机绝对路径与原始异常文本。
- 未授权请求不会调用文件加载函数，也不能发布 Provider 配置。
- 未知 `/api/*` 返回 JSON 404，不再错误回退到 SPA HTML。

最终自动化结果：

```text
uv run ruff check .
All checks passed!

uv run pytest -q
646 passed, 9 skipped, 1 warning

npm run typecheck
通过

npm test -- --run
Test Files  4 passed (4)
Tests       22 passed (22)

npm run build
585 modules transformed
```

唯一 warning 是已有 crawler 异步协程未 await 告警，本阶段没有修改对应 crawler。

## 3. 真实服务验证

一键启动自动避开 Windows/Docker 保留端口，当前实际地址：

```text
DeepSearcher API  http://127.0.0.1:8750  ready
User workspace    http://127.0.0.1:8700  ready
Milvus            127.0.0.1:19530        healthy
```

真实 HTTP 契约：

- OpenAPI 的 `/query` 只包含 POST。
- 未携带服务令牌的 POST `/query` 返回 401/`SERVICE_UNAUTHORIZED`，响应头与错误体请求 ID 一致。
- GET `/query/` 返回 404/`NOT_FOUND`。
- 默认未配置 CORS 时，不可信 origin 没有 `Access-Control-Allow-Origin`。
- 工作台非法请求返回 422/`INVALID_REQUEST`；未知 `/api` 返回 JSON 404/`NOT_FOUND`。

真实产品 SSE 使用“Milvus 学习资料”创建临时对话：

```text
HTTP                    200
X-Request-ID            product-live-s03-2
SSE event request IDs   product-live-s03-2
events                  started → routing → iteration → retrieval → support
                        → reflection → iteration → retrieval → support
                        → reflection → completed
assistant status        succeeded
answer state            grounded
citations               2
temporary cleanup       DELETE 204, subsequent GET 404
```

## 4. 真实浏览器验证

Playwright 在真实工作台中选择当前“Milvus 学习资料”，从首页提交问题并进入新对话：

- `POST /api/conversations/{id}/messages/stream` 返回 200。
- 页面显示最终回答和 2 条可打开原文的引用。
- 浏览器控制台：0 error、0 warning。
- 临时对话在验证后删除，GET 验证为 404。
- 截图：`output/playwright/s03-api-security/product-query-completed.png`。

## 5. 诚实边界

- 当前限流是每个 Uvicorn 进程的本地保护层，不是 Redis/网关级分布式配额。
- 当前完成的是服务到服务令牌与租户/Collection 授权，不是最终用户登录、会话和 RBAC。
- 查询超时异常已经稳定映射到 504，但同步查询尚未实现可强制中断底层 SDK 的端到端 deadline；产品主链路依靠 SSE 协作取消。
- CORS 不是鉴权；生产环境仍需 TLS、网关、密钥轮换和边界审计。
- 日志为防泄漏只记录未知异常类型和请求关联字段；如需完整堆栈，必须先建设结构化脱敏与受限日志访问。
