# DeepSearcher A-02 受控异步召回验证

## 结论

A-02 已完成。DeepSearch 不再在事件循环中直接执行同步的 Collection 路由、
Embedding、Milvus 搜索或批量 Rerank 模型调用。当前同步 Provider 通过受控
线程适配，并具备并发上限、单操作超时、总 deadline 和取消传播。

## 实现约束

默认值：

| 参数 | 默认值 |
| --- | ---: |
| `retrieval_concurrency` | 4 |
| `external_call_timeout_seconds` | 30 |
| `request_timeout_seconds` | 300 |

同一 DeepSearch 请求的路由、Embedding、向量搜索和 Rerank 共用一个
`asyncio.Semaphore`，因此并发不会随着子查询数或 Collection 数无限增长。

每个子查询通过独立的 CollectionRouter 浅快照生成本地路由决策。并发任务完成后，
主事件循环按输入顺序记录 Trace 和合并结果，避免共享 `last_decision` 竞态。

### 取消边界

`asyncio.wait_for()` 的超时或上游任务取消会立即停止等待、取消逻辑检索并丢弃迟到
结果。Python 无法安全强杀已经进入同步 Provider 的工作线程；Milvus 自身仍有
10 秒 RPC timeout，其他 Provider 应同时配置其客户端超时。该限制已写入并发探针
报告，没有包装成“底层调用已被硬取消”。超时或取消后，对应 Semaphore 槽位会
保留到同步线程实际结束，避免迟到线程仍在运行时又放入新任务而突破物理并发上限。

## 真实 I/O 探针

环境：

- Embedding：`OpenAIEmbedding`
- VectorDB：`Milvus`
- Collection：`kb_037e65f9612842fb809fd596de82e351`
- top-k：5
- 最大并发：4

命令：

```powershell
uv run python -m evaluation.concurrency_probe `
  --collection kb_037e65f9612842fb809fd596de82e351 `
  --counts 1,2,4,8 --max-concurrency 4 --top-k 5 `
  --timeout-seconds 30 `
  --output evaluation/results/2026-07-30-deep-search-concurrency-v1/report.json
```

结果：

| 查询数 | 并发上限 | 串行耗时 | 受限并发耗时 | 加速比 | 并发心跳最大间隔 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 6709 ms | 8166 ms | 0.82× | 31 ms |
| 2 | 2 | 8406 ms | 8616 ms | 0.98× | 32 ms |
| 4 | 4 | 31018 ms | 7684 ms | 4.04× | 32 ms |
| 8 | 4 | 12330 ms | 2187 ms | 5.64× | 31 ms |

单查询和双查询受连接预热、限流及外部服务波动影响，本次没有加速；4/8 查询显示
受限并发能缩短批量墙钟时间。串行的 4 查询耗时高于 8 查询也直接说明单次外部
调用方差较大，因此这里验证的是非阻塞与并发上限，不把某个加速比当作稳定 SLA。

第一次探针执行中，Milvus 的 10 秒 RPC deadline 被触发，系统抛出
`VectorDBUnavailable` 并停止运行，没有保存虚假成功报告。服务健康复查及 5 次
顺序线程检索随后均成功，第二次完整探针成功。该过程同时验证了失败不会伪装成
空结果。

## DeepSearch 质量回归

固定前三题、`top_k=5`、`max_iter=1`：

| 指标 | A-01 批量版 | A-02 并发版 |
| --- | ---: | ---: |
| Recall@5 | 1.0000 | 1.0000 |
| Precision@5 | 0.4000 | 0.4000 |
| MRR | 1.0000 | 1.0000 |
| 证据要点覆盖 | 0.9167 | 0.9167 |
| 来源准确率 | 1.0000 | 1.0000 |
| LLM 调用/题 | 2.00 | 2.00 |
| 平均延迟 | 16032 ms | 4878 ms |

外部模型响应存在波动，因此并发收益以剥离 Rerank 的真实 I/O 探针为主；
此处主要证明端到端质量没有变化。

## 自动化验证

覆盖：

- 1/2/4/8 个任务下最大活跃调用不超过 Semaphore 上限；
- 阻塞 Provider 执行时事件循环心跳仍前进；
- 单操作 timeout 和总请求 deadline；
- 上游取消传播；
- 超时工作线程在实际结束前仍占用并发槽位；
- Provider 异常原样向上传播，不转空结果；
- 并发路由完成顺序不同，Trace 仍保持子查询输入顺序；
- 批量 Rerank、白名单、Trace 和评测回归。

结果：

- Python：`553 passed, 7 skipped`
- 前端：`17 passed`
- Ruff、TypeScript、Vite 生产构建全部通过

Python 回归另有 1 条既有 Crawl4AI “coroutine was never awaited”运行时警告，
不影响通过结果，留待独立治理。

## 报告

- `evaluation/results/2026-07-30-deep-search-concurrency-v1/report.json`
- `evaluation/results/2026-07-30-deep-search-concurrent-retrieval-v1/report.json`
