# DeepSearcher FastAPI 运行时生命周期验证记录

- 验证日期：2026-07-30
- 对应问题：O-07 FastAPI 导入即初始化外部组件
- 验证结论：通过

## 架构结果

`main.py` 现在提供 `create_app()` 应用工厂。模块导入阶段仅加载轻量配置类型、错误协议和路由定义，不创建 LLM、Embedding、Loader、Crawler、Agent 或向量客户端。

FastAPI lifespan 负责：

1. 读取一份配置快照；
2. 构建隔离的 `RuntimeComponents`；
3. 将就绪运行时发布到 `app.state.runtime`；
4. 初始化失败时保存安全的 `RuntimeInitializationError`，继续提供 HTTP 服务；
5. 应用退出时关闭其拥有的 Milvus 客户端。

查询、入库和删除接口通过 `Depends(get_runtime)` 获取运行时。`tests/test_query_api.py` 使用注入的 Stub 完成真实 ASGI/HTTP 测试，不访问外部模型或 Milvus。库和 CLI 仍可使用 `init_config()`；该函数内部复用新的构建器并发布旧模块级全局变量。

## 导入性能

在同一 Windows 工作区中使用独立 Python 进程测量：

```text
改造前 import main: 约 17.05 秒
改造后 import main: 约 0.95 秒
```

Agent、Loader、Crawler 和具体向量数据库实现改为运行时懒加载。自动化测试在导入前把向量客户端构造替换为必定抛错的函数，`import main` 仍成功，证明导入阶段没有创建外部运行时。

## 真实 Milvus 离线验证

验证开始时 Docker Desktop、Milvus 和现有服务均未运行。在该状态下启动：

```text
python -m uvicorn main:app --host 127.0.0.1 --port 8660
```

核心 API 成功监听端口，没有因 Milvus 连接失败而退出。

`GET /health` 返回：

```json
{
  "status": "not_ready",
  "error": {
    "code": "RUNTIME_INITIALIZATION_FAILED",
    "message": "DeepSearcher runtime initialization failed.",
    "component": "vector_db",
    "retryable": true
  }
}
```

`GET /query/?original_query=Milvus` 同样返回 HTTP 503 和安全错误对象，不包含 Milvus URI、Token、异常栈或其他内部细节。

随后关闭隔离 API，通过项目启动脚本恢复完整服务。最终状态：

```text
Core API 8650: online, GET /health -> 200 {"status":"ready"}
User workspace 8600: online
Milvus 19530: online
LLM and Embedding: configured
```

## 自动化结果

```text
Python full regression: 492 passed, 7 skipped
Runtime/vector/API/product targeted regression: 51 passed, 6 skipped
Frontend Vitest: 3 files passed, 17 tests passed
Frontend query-failure interaction: 12 tests passed
TypeScript: tsc --noEmit passed
Ruff check and format: passed
Production build: Vite build passed, 585 modules transformed
Real Milvus-offline startup/readiness: passed
Normal runtime restart/readiness: passed
```

全量 Python 测试保留一个既有 Crawl4AI mock 协程未等待警告，不影响本次运行时生命周期结果。
