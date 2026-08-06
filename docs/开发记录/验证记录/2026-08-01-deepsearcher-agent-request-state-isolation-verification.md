# DeepSearcher 共享 Agent 请求状态隔离验证

日期：2026-08-01

对应优化项：`S-01 全局配置不适合并发和多租户` 补充收口

## 验收结论

S-01 的版本化 `RuntimeRegistry`、租户绑定、Collection 权限和多 worker 一致性已在
2026-07-31 完成。本次继续审计发现：同一个 Runtime 会复用 Agent 实例，而部分 Agent 曾把
`last_decision`、`last_route_decision` 和选择事件列表保存在实例字段中。两个请求即使属于同一
租户，也可能在“完成决策”和“写入 Trace”之间互相覆盖这些字段。

该缺口现已收口。同一 Runtime、同一 Agent 实例的并发请求拥有独立的决策快照和 DeepSearch
事件列表；生产 Trace 与返回的 `additional_info.selection_decisions` 不会串入其他请求的数据。

## 实现结果

- `CollectionRouter` 的 Collection 快照和最后一次路由决策改为 `ContextVar` 请求状态；公开
  `all_collections`、`last_decision` 仍兼容原调用方式，但只表示当前请求上下文。
- `RAGRouter.last_route_decision` 改为请求上下文快照。路由模型选择和显式 Web 能力选择均通过
  同一个记录入口写入，`retrieve()` / `query()` 读取的是本请求决策。
- `ChainOfRAG` 的支持文档判断与追问守卫决策改为请求上下文快照，不再由共享实例字段跨线程
  传递。
- `DeepSearch` 每次 `async_retrieve()` 进入后创建独立选择事件列表。阻塞 LLM 调用通过
  `asyncio.to_thread()` 继承当前上下文并写入该请求列表；父协程保留对本请求列表的显式引用，
  Trace 和最终 `additional_info` 都从该引用读取，而不读取共享实例列表。
- 兼容诊断属性仍可用于单次同步调用后的测试和排障，但它们表示“当前上下文最后一次决策”，
  不能作为跨请求全局监控指标。

## 并发回归

新增 `tests/agent/test_request_state_isolation.py`，所有用例都复用同一个 Agent/Router 实例，并用
Barrier 强制两个请求在决策完成后同时继续，从而稳定覆盖原竞争窗口：

1. 两个线程分别选择 `alpha` / `beta` Collection，各自读取到自己的白名单决策；
2. 两个线程分别触发 RAGRouter 的无效回退 / 有效第二 Agent，Trace 中的 Agent 和回退状态
   各自正确；
3. 两个线程分别得到 ChainOfRAG 的“有支持证据”/“无支持证据”判断，决策不互相覆盖；
4. 两个异步 DeepSearch 请求共享一个实例，并在两个 `to_thread()` LLM 调用处同步；每个返回值
   只包含自己的子查询和一条自己的选择事件。

聚焦结果：

```text
80 passed in 14.54s
```

其中包含 4 个新增并发用例，以及 CollectionRouter、RAGRouter、ChainOfRAG、DeepSearch 和
Collection scope 的既有回归。

## 全量静态与自动化验证

```text
pytest: 711 passed, 10 skipped, 1 warning in 96.61s
ruff check .: All checks passed
uv lock --check: Resolved 344 packages
git diff --check（本次文件）: passed
```

唯一 warning 是既有 `Crawl4AICrawler._async_crawl_many` 模拟协程未 await 警告，本次修改未涉及
Crawler，也没有新增 warning。

## 真实运行验收

使用项目脚本重载服务后，最终状态为：

```text
Docker           ready
Milvus           ready  127.0.0.1:19530
DeepSearcher API ready  http://127.0.0.1:8650
Document worker  ready
User workspace   ready  http://127.0.0.1:8600
```

随后通过用户工作台 BFF，在同一个知识库和同一 Runtime 上同时提交两条真实问题：

```text
请求 1: question_matches=true, status=succeeded, answer_state=grounded, citations=5
请求 2: question_matches=true, status=succeeded, answer_state=grounded, citations=5
```

两个验收对话在检查完成后均通过产品删除接口删除，没有留下测试对话。

## 诚实边界

- `ContextVar` 解决同一进程内线程、异步任务和 `to_thread()` 上下文间的请求状态隔离；跨进程
  配置一致性仍由共享 `RuntimeControlStore` 保证，两者职责不同。
- Runtime 内的 LLM、Embedding 和 VectorDB 客户端仍会按版本复用，这是连接池与资源复用的
  预期设计；本次隔离的是 Agent 的请求决策和事件数据，不是为每个请求重建外部客户端。
- 本地默认租户仍保留兼容性的 `*` Collection 权限；正式多租户环境仍应使用显式租户、服务
  令牌和 Collection 白名单。
