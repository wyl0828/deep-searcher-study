# DeepSearcher 流式与 API 安全补充收口验证记录

日期：2026-08-01  
范围：S-02/S-03 补充审计；适配器日志、Trace URL、BFF SSE 协议与引用归属边界。

## 1. 验收结论

S-02 与 S-03 在当前单用户工作台、服务间鉴权和租户/Collection 隔离范围内完成安全收口。
本轮没有改变用户功能，而是补齐此前自动化未覆盖的底层日志与 BFF 信任边界：上游异常内容不再
进入日志，BFF 不再接受错版本、错请求、乱序或超限的 SSE，核心 Trace 也不能借引用字段把
其他知识库文档关联到当前回答。

## 2. 修复内容

- 新增 `safe_exception_message()`，只输出受限操作名与异常类型。Milvus、Qdrant、Oracle、
  Azure Search、Docling、Crawl4AI、JSON/Unstructured Loader、WatsonX、SentenceTransformer
  和 Ollama 等适配器不再记录原始异常文本、SQL/返回数据、向量、DSN、URL、凭据或本地路径。
- runtime 组件关闭失败不再写 traceback；日志保留请求关联和稳定分类，详细外部异常不进入
  普通应用日志。
- 新增 AST 安全门禁，扫描 `deepsearcher`、整个 `frontend` 和 `main.py`：异常处理器中的日志
  调用不得直接序列化异常对象，也不得启用 `exc_info=True`；安全操作名和异常类型仍可用于诊断。
- Trace 的公网 URL 使用统一规范化策略，拒绝凭据 URL、IP、内网主机、非标准端口、非
  HTTP(S) 协议，并删除 query 与 fragment；文件引用只保留脱敏后的文件名。
- BFF 强制验证 SSE 协议版本、事件名、信封事件一致性、合法且一致的请求 ID、从 1 开始的
  连续序号，并限制单行、单事件和 `data:` 行数量。违规流统一返回可重试的
  `502/QUERY_STREAM_INVALID`，不把原始帧交给浏览器。
- 引用持久化只接受受限字段、长度、数量、有限数值和合法 bbox/URL；文档 ID 或 SHA 只能在
  当前对话所属知识库内解析，无法跨知识库绑定文档。

## 3. 自动化结果

```text
安全、流式、API、适配器定向回归
516 passed, 6 skipped

Python 全量回归
718 passed, 10 skipped, 1 warning

前端
Test Files  4 passed (4)
Tests       25 passed (25)
TypeScript typecheck passed
Vite production build passed (585 modules)

uv run ruff check .
All checks passed

uv lock --check
passed
```

唯一 warning 仍是既有 Crawl4AI mock 协程未 await 告警，与本轮改动无关。

关键新增回归覆盖：原始异常和 traceback 日志门禁、Trace 内网/凭据 URL 拒绝、BFF 请求 ID
错配/序号断裂/版本错配/事件错配、超限 SSE 帧，以及跨知识库引用绑定。

## 4. 真实服务验证

重启后真实组件均就绪：Milvus `127.0.0.1:19530`、核心 API `127.0.0.1:8650`、工作台
`127.0.0.1:8600` 和入库 worker。

在“Milvus 学习资料”创建临时对话并通过工作台 SSE 提问：

```text
request_id             live.s02-s03-0801
frame_count            15
events                 started → routing → iteration → retrieval → support → reflection
                       → iteration → retrieval → support → reflection
                       → iteration → retrieval → support → reflection → completed
version                全部为 1
request_id             全部一致
sequence               1–15 连续
assistant status       succeeded
answer state           grounded
supported citations    1
temporary cleanup      DELETE 204
```

错误契约实测：

```text
核心无令牌查询          401 / SERVICE_UNAUTHORIZED
工作台不存在对话        404 / CONVERSATION_NOT_FOUND
响应头/错误体请求 ID    一致
测试密钥/本地路径回显   无
当前 runtime 日志扫描   无测试问题、密钥或本地路径匹配
```

## 5. 保留边界

- 当前鉴权闭环是工作台到核心 API 的服务令牌、管理令牌以及租户/Collection 授权；尚未实现最终
  用户登录、会话、角色与 RBAC。这是后续产品化能力，不应把当前单用户工作台描述成多用户权限系统。
- 为避免泄漏，普通日志不保存供应商原始异常与 traceback。若未来需要更深诊断，应建设受限、
  结构化且字段脱敏的安全日志通道，而不是恢复原始异常串。
- BFF 的 SSE 大小限制是应用边界保护；生产部署仍应在反向代理和网关配置请求/响应体、连接时长
  与并发限制。
