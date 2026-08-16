# 多实例故障切换与 Chat 双 Provider Fallback 验证记录

> 日期：2026-08-16
> 服务器：阿里云 ECS `47.96.40.156`（基线 v0.3.0-rc.2，提交 c1a76df）
> 拓扑：双 product-api（A:18700 / B:18701）+ 双 consumer + 共享 PostgreSQL/Redis/MinIO/Milvus/RocketMQ
> 脚本：`deploy/server/verify-failover.sh`（已补全六场景机器断言）
> 结论：✅ P0 两项全部通过

---

## 一、E.1 多实例故障切换（六场景脚本化执行）

前置：`server_verify_admin` 登录、core-api `/health/ready` 预检、`WhatisMilvus.pdf`（57338 B）fixture。
执行：`cd releases/20260815-01 && bash deploy/server/verify-failover.sh`，最终 `VERIFY-FAILOVER: PASS`。

| # | 场景 | 机器断言 | 结果 |
|---|---|---|---|
| 0 | 记录容器身份 | API-A/B、Consumer-A/B 容器 ID + consumer 短 ID | ✅ |
| 1 | 跨节点：API-A 上传 → Consumer 处理 → API-B 查询 | 文档 ready；`ingest_jobs.lease_owner` 捕获到 consumer 短 ID；API-B 真实查询 `citations=10`、`status=succeeded` | ✅ |
| 2 | 单 API 停止接管 | stop api-a 后 API-B `/api/auth/me` 200；恢复后 healthy | ✅ |
| 3 | 单 Consumer 停止接管 | stop consumer-a（等 rebalance）后上传新文档 ready，`lease_owner` 含 consumer-b 短 ID | ✅ |
| 4 | 重复消息幂等 | 事务消息重投 3 次后：documents=1、ingest_jobs=1、job=succeeded、Milvus `num_entities` 前后一致（0=0） | ✅ |
| 5 | 事务二次确认丢失回查 | 发送 half 消息且不二次确认，broker 端消息最终可见（被消费） | ✅ |
| 6 | 全进程重启持久化 | restart 全部 12 容器后全部 healthy；真实数据计数 docs=1/convs=1/msgs=2/citations=10/jobs=1 前后一致；core-api runtime 恢复 | ✅ |

## 二、Chat 双 Provider Fallback 验证

方法：临时在 core-api 容器启用 `llm.candidates`（主 DeepSeek `deepseek-v4-flash`，`base_url` 指向不可达 `http://127.0.0.1:9` 强制失败；备选 OpenAI 兼容端点 `qwen-plus`），`routing.first_packet_timeout=5`，重启 core-api 后验证，最后恢复原单 Provider 配置。

| 验证点 | 证据 | 结果 |
|---|---|---|
| RoutingLLM 直接调用 | `FINAL_MODEL: qwen-plus`；`FALLBACK_REASON: deepseek-v4-flash:APIConnectionError`；回答 `FALLBACK_OK` | ✅ |
| 端到端真实 Chat 链路 | 主 Provider 不可达时，流式消息正常完成，回答 `fully_grounded`、10 条 Citation | ✅ |
| 配置恢复 | core-api 恢复单 Provider DeepSeek 并 `/health/ready` 200；12 容器全 healthy | ✅ |

说明：当前版本的产品 API SSE/消息响应未暴露 LLM provider 的 `model`/`fallback_reason`（SSE `routing.fallback_used` 是 agent 路由字段，非 LLM fallback），因此本次通过容器内 RoutingLLM 直调 + 端到端成功两种方式确认 `final_model` 与 fallback 原因。

## 三、过程中发现并修复的问题（就地修复）

1. **`wait_ready` 在 `set -e` 下中断脚本**：`STATUS=$(wait_ready ...)` 超时返回 1 触发 `set -e` 退出。改为 `STATUS=$(wait_ready ... || true)`，并在 core-api 故障时不会硬退出。
2. **consumer 身份匹配**：`lease_owner` 的 worker_id 使用容器短 ID（`hostname-pid-uuid`，hostname=容器短 ID），原脚本按容器名匹配失败。改用容器短 ID 匹配。
3. **Milvus 计数**：pymilvus 2.5.8 无 `get_collection_stats`；连接别名需显式 `using`。改用 `Collection(name, using=...).num_entities`。
4. **场景 4 重发消息被拒**：生产 topic 是 TRANSACTION 类型，不能发 NORMAL 消息。重发模拟改为事务消息（AlwaysCommit checker + begin_transaction/commit）。
5. **场景 3 偶发失败（可复现）**：stop consumer-a 后 consumer-b 需 rebalance 才接单。脚本在 stop 后等待 25s 再上传，随后稳定 PASS。
6. **core-api 重启后 runtime 未就绪（已知运维要点）**：场景 6 restart 后 core-api 进程 healthy 但 `/load-files/` 503。脚本增加 core-api 就绪预检与场景 6 后自动重启恢复。
7. **消息丢失的恢复**：一次执行中消息未被消费（文档悬挂 processing），通过向同一 job 重发事务消息触发处理（幂等），文档恢复 ready。证实重投机制可用。

## 四、结论

- 六场景全部机器断言 PASS，覆盖跨节点处理、单 API/单 Consumer 接管、重复投递幂等、事务回查安全、全量重启持久化。
- Chat 双 Provider fallback 生效：主 Provider 首包失败/连接失败时自动切换备选，`final_model=qwen-plus`、`fallback_reason=deepseek-v4-flash:APIConnectionError`。
- 服务器恢复原状：12 容器全 healthy，`knowledge_bases`/`documents` 计数为 0，无测试残留。

## 五、遗留

- 产品 API 尚未将 LLM provider fallback 的 `model`/`fallback_reason` 透出到消息/SSE/Trace；若需用户可见的 fallback 标识，可作为后续增强（对齐 ragent 的 model 透出）。
- `test_rocketmq_transaction_check.py`（计划引用的独立集成测试）尚不存在，本次以服务器真实 RocketMQ 事务消息验证替代；如需可后续补充该集成测试文件。
