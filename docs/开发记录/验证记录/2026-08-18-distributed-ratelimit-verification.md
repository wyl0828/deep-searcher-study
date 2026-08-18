# P5 流量治理：多实例共享 LLM 额度的公平排队限流验证记录

> 日期：2026-08-18
> 阶段：对标 Ragent 企业级缺口优化计划 P5（按需启动——多实例共享模型额度需求出现后实施）
> 参考：`D:\code\reference\ragent`（提交 `020e5c3`）的
> `rag/.../rag/service/ratelimit/FairDistributedRateLimiter.java` + `resources/lua/queue_claim_atomic.lua`
> 结论：✅ 核心 + 测试 + 全量 pytest 全部通过；quality_gate 与 P4/P5 其余步骤全绿，git-diff-check 被用户 output/pdf PDF 变更误报（同 P4，非 P5 引入）

---

## 1. 目标

多 API 实例共享同一 LLM/Embedding 额度时，用共享信号量 + 公平队列让突发请求排队而非拒绝。
同步实现（对齐本项目同步 LLM 调用路径）。

## 2. 参考对齐（黑 ragent → Python 等价）

| ragent | Python（`deepsearcher/llm/redis_semaphore.py`） |
|---|---|
| Redisson 信号量（semaphoreKey） | Redis `permit_key` 计数器（NX 初始化一次，多实例共享额度） |
| ZSet 公平队列（queueKey）+ AtomicLong（queueSeqKey） | Redis ZSet（score=arrival seq）+ INCR |
| entry TTL 标记 + 僵尸清理 | `entry_prefix + request_id`（TTL）+ Lua 内 `EXISTS`→`ZREM`（对齐 queue_claim_atomic.lua） |
| `queue_claim_atomic.lua`（ZRANGE 头窗口、清僵尸、maxRank 内出队、返回 score） | `_CLAIM_LUA`（同语义）+ `_ENQUEUE_LUA`/`_RELEASE_LUA` 原子执行 |
| 票状态机 PENDING/GRANTED/TIMED_OUT/CANCELLED | `PermitState`（同步: PENDING→GRANTED|TIMED_OUT；取消经 evict） |
| RTopic 跨实例通知 + PollNotifier | 短间隔轮询（poll_interval_ms，correct 优先于 notify 优化） |

**接入点校准**：真实额度消耗在 LLM 调用层 → 接入 `RoutingLLM.chat_with_options/stream_with_options`（每次调用 acquire/release，
try/finally 保证失败也释放）；产品侧 `conversations.py` 不重复加限流（避免双层冗余）。默认 `limiter=None` 禁用，不改变单实例行为。

## 3. 实现清单

| 项 | 文件 | 说明 |
|---|---|---|
| 信号量 | `deepsearcher/llm/redis_semaphore.py` | `DistributedFairSemaphore`：NY 初始化 permit 计数、ZADD 入队（seq）、Lua 原子 claim（清僵尸 + 认领 + DECR）、release（INCR）、超时 evict、短轮询 |
| 接入 | `deepsearcher/llm/routing.py` | `RoutingLLM.__init__(limiter=None)`；`_acquire_permit`/`_release_permit`；chat/stream 用 try/finally 包裹（限流超时抛 `AllModelsFailed`，失败也 release） |
| 依赖 | `pyproject.toml`/`uv.lock` | `lupa`（dev，fakeredis 的 Lua eval 支持） |
| 测试 | `tests/test_redis_semaphore.py` | 5 Lua/状态机 + 2 RoutingLLM 接入 |

## 4. 验证矩阵

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 单个 acquire/release 正常 | ✅ | `test_acquire_and_release`（GRANTED → release） |
| 2 | 占用时等待超时（TIMED_OUT + evict） | ✅ | `test_second_waiter_times_out_with_no_permit` |
| 3 | **多实例共享同一额度**（permits=2 跨 2 实例 → 第 3 个等待/超时，release 后可获） | ✅ | `test_multi_instance_share_same_quota` |
| 4 | **公平排队**：release 后最老的排队者获准（非拒绝） | ✅ | `test_release_unblocks_fair_waiter`（线程阻塞等待→release→获准） |
| 5 | **僵尸清理**：无 entry marker 的队列成员被 claim Lua ZREM | ✅ | `test_zombie_entry_cleaned_by_claim` |
| 6 | **Lua 原子性**（fakeredis + lupa 真实 eval） | ✅ | 上述用例均走真实 Lua eval |
| 7 | RoutingLLM 接入：acquire/release 配对；失败也 release | ✅ | `test_routing_llm_acquires_and_releases_permit` / `test_routing_llm_releases_permit_on_failure` |
| 8 | 既有 routing 回归（默认 limiter=None 禁用不改变行为） | ✅ | `tests/llm/test_routing.py` 8 用例全过 |
| 9 | 全量 pytest | ✅ | 1149 passed, 11 skipped |
| 10 | ruff（deepsearcher/llm/*）| ✅ | All checks passed；format --check 绿 |

## 5. quality_gate 说明

- `scripts/quality_gate.py`：其余 17 步全绿（ruff/pytest/前端/迁移/评估门/mkdocs）。
- **git-diff-check 被用户 output/pdf PDF 变更误报**（`M`/`D`/untracked，`.gitattributes diff=astextplain` 文本化二进制，
  trailing whitespace 误报）——与 P4 状态一致，非 P5 引入；已核验排除 `output/pdf` 后 P5 代码 `git diff --check` 干净。

## 6. 结论

P5 流量治理已闭环并按需要启动：多实例共享 LLM 额度的公平排队限流（Redis 信号量 + ZSet 队列 + Lua 原子认领/清僵尸 +
超时/取消 + 短轮询），接入 `RoutingLLM` 每次调用（try/finally 释放、默认禁用不改变单实例行为）。
全量 pytest 1149 passed、ruff 绿；quality_gate 17/18 步 PASS（git-diff-check 为用户 PDF 变更误报）。

## 7. 遗留

- 跨实例"无感知唤醒"目前靠短轮询（correct 优先）；如需更低时延可后续引入 RTopic/PollNotifier 合并通知。
- `PermitState.CANCELLED` 保留枚举（同步路径只用 GRANTED/TIMED_OUT）；取消（外部显式 evict）供扩展。
- 配额、Redis URL 的接线到既有配置层（env/configuration）可按部署需要补（本批 API/可注入 limiter 已就绪）。
- 产品侧 `conversations.py` 不做双层限流；如需 per-request 闸门可另行评估。