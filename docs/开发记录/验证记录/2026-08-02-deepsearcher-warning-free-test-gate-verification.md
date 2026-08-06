# DeepSearcher 零告警测试门禁验证记录

日期：2026-08-02  
范围：Crawl4AI 未等待协程、SSE 集成测试子进程资源释放、pytest warning 门禁。

## 1. 根因与修复

- Crawl4AI 生产实现使用 `asyncio.run()` 正常等待协程；告警来自测试把 `asyncio.run()` 替换为
  普通 Mock，导致已经创建的 `_async_crawl` / `_async_crawl_many` 协程无人接管。协程稍后在
  Docling 测试期间被垃圾回收，因此告警位置具有误导性。
- Crawl4AI 测试改为保留真实 `asyncio.run()`，使用 `AsyncMock` 替换异步抓取方法，并断言
  `assert_awaited_once_with`。删除全局 warning 过滤，不再隐藏生命周期错误。
- 全量 `-W error` 随后发现 SSE 断线集成测试只等待 Uvicorn 子进程退出，没有读取并关闭
  `stdout` / `stderr` 文本管道。清理逻辑改为 terminate/kill 后调用 `communicate()`，并在
  finally 中兜底关闭两个流。
- `pyproject.toml` 新增 pytest `filterwarnings = ["error"]`；普通 `uv run pytest` 现在默认
  将所有 warning 当作失败，后续不能静默引入未等待协程、未关闭文件或其他资源告警。

## 2. 验证结果

```text
Crawl4AI + Docling 定向 warnings-as-errors
11 passed

Crawl4AI + Docling + SSE 断线定向 warnings-as-errors
12 passed

显式全量 warnings-as-errors
725 passed, 10 skipped

启用默认门禁后的普通全量测试
725 passed, 10 skipped

uv run ruff check .
All checks passed

uv lock --check
passed

git diff --check
passed
```

全量输出不再包含 warnings summary。本轮仅修复测试替身和测试子进程资源生命周期，不改变
Crawl4AI 或 SSE 的产品行为。
