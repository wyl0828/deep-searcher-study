# P1-4.2 用户反馈闭环验证记录

> 日期：2026-08-17
> 阶段：对标 Ragent 企业级缺口优化计划 P1-4.2（用户反馈闭环）
> 参考：`D:\code\reference\ragent`（提交 `020e5c3`）的
> `rag/.../service/impl/MessageFeedbackServiceImpl.java` / `MessageFeedbackController.java` /
> `MessageFeedbackDO.java` / `MessageFeedbackConsumer.java`
> 结论：✅ 全部通过

---

## 1. 目标

把前端已有的本地 👍/👎 按钮（原先仅 `useState` 切换、不落库）接到后端闭环：
- 新增 `MessageFeedback` 表 + `POST`/`DELETE` 反馈 API（对齐 ragent 双端点，`(user_id, message_id)` 唯一幂等）
- 负面反馈并入检索健康：仅指标 + 建议，**formula 1.1 score 显式不变**（不 bump 版本、不改金标）
- 同步落库，不引入 MQ；范围锁定：不做 MQ、管理后台、审计、reason/comment UI

## 2. 实现清单

| 项 | 文件 | 说明 |
|---|---|---|
| 模型 | `frontend/product/models.py::MessageFeedback` | 表 `message_feedback`，字段对齐 MessageFeedbackDO（去 conversationId 冗余，经 message→conversation 获取）；`(user_id, message_id)` 唯一 + `ck_message_feedback_vote`（vote IS NULL OR IN (-1,1)，NULL=取消） |
| 迁移 | `frontend/product/migrations/versions/20260817_0021_message_feedback.py` | 建表 + FK(CASCADE) + Unique + Check + 2 索引 |
| 服务 | `frontend/product/services/feedback.py` | `_upsert_feedback`（SAVEPOINT 并发幂等）/ `submit_message_feedback` / `cancel_message_feedback` / `get_feedback_map`（等价 MessageFeedbackServiceImpl 各方法） |
| Schema | `frontend/product/schemas.py` | `MessageFeedbackCreate`（vote: Literal[1,-1]）；`MessageResponse.feedback` 可选字段 |
| 路由 | `frontend/product/routes.py` | `POST/DELETE /api/conversations/{id}/messages/{message_id}/feedback`；`_feedback_target` 错误语义固定（404/404/400）；`conversation_detail` 附带 feedback |
| 健康 | `frontend/product/services/knowledge_health.py` | `compute_retrieval_health` 增加 `negative_feedback_rate`（按 distinct message_id 统计）+ informational `NEGATIVE_FEEDBACK`；`assemble_health_payload` 按 sampled message IDs 精确打标 |
| 前端 | `frontend/src/product-api.ts` / `App.tsx` | `submitMessageFeedback` / `cancelMessageFeedback`；`AssistantMessage` 按钮接线 + `useEffect` 服务器状态同步 |

## 3. 验证矩阵

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 健康纯函数：distinct 分母契约 | ✅ | `test_retrieval_health_negative_feedback_distinct_message_denominator`：同一 message_id 3 行 + 另一消息 1 行 → `negative_feedback_rate == 0.5`（不是 0.25） |
| 2 | 健康纯函数：无 message_id 旧调用按行退化 | ✅ | `test_retrieval_health_negative_feedback_legacy_rows_fallback`：2/4 → 0.5 |
| 3 | 健康纯函数：阈值（>0.2 触发、≤0.2 不触发） | ✅ | `test_retrieval_health_negative_feedback_threshold`：0.2 无 NEGATIVE_FEEDBACK、0.25 触发且带 REVIEW_RETRIEVAL 建议 |
| 4 | **score 不变强断言** | ✅ | `test_retrieval_health_negative_feedback_does_not_change_score`：`flagged["score"] == baseline["score"]`（负反馈只出诊断不改分） |
| 5 | 向后兼容：无 flag → rate=0.0、无新 deduction | ✅ | `test_retrieval_health_no_feedback_flag_backward_compat` |
| 6 | 空样本 → rate=0.0 | ✅ | `test_retrieval_health_negative_feedback_empty_sample` |
| 7 | POST 落库 / 覆盖幂等 / DELETE 取消 / 再 POST 恢复 | ✅ | `test_message_feedback_submit_overwrite_cancel_and_restore`：行数始终 1；DELETE 后 vote=NULL+cancelled=True+reason 清空；`conversation_detail` 回读 feedback 状态 |
| 8 | DELETE 对从未提交过的消息幂等 | ✅ | `test_message_feedback_cancel_is_idempotent_without_prior_vote`：两次 DELETE 后仅 1 条 cancelled 记录 |
| 9 | 错误语义（404/404/400/422） | ✅ | `test_message_feedback_target_and_ownership_errors`：他人会话 404、跨会话消息 404、幽灵会话 404、user 消息 400、vote=0/2 → 422 |
| 10 | 健康 e2e：负反馈进入快照且只统计实际 sample | ✅ | `test_message_feedback_negative_rate_enters_health_snapshot`：4 条样本 1 条差评 → `message_sample_count=4`、`negative_feedback_rate=0.25` |
| 11 | 全量 pytest | ✅ | `pytest tests frontend/tests`：1074 passed，11 skipped（含新增 6 个健康纯函数 + 4 个反馈 e2e；head 断言更新为 `20260817_0021`） |
| 12 | 前端单测 | ✅ | `vitest run`：40 passed（新增 2 个 api 测试 + 3 个按钮交互/状态恢复测试） |
| 13 | 前端 typecheck / build / bundle | ✅ | `tsc --noEmit` 通过；`vite build` 成功；bundle 检查通过 |
| 14 | Alembic 迁移链 | ✅ | 空库 `alembic upgrade head` 升级至 `20260817_0021` 成功 |
| 15 | 完整 quality_gate | ✅ | `scripts/quality_gate.py` 全 18 步 PASS（ruff format/check、pytest、frontend test/typecheck/build/bundle/e2e、alembic、trust/citation/entailment/risk/provenance/evaluation 门、mkdocs、git diff） |

## 4. 过程中发现并就地修复的问题

1. **健康采样 status 与生产终态不一致（既有 bug）**：`knowledge_health.py` 查询 `Message.status == "completed"`，而生产消息终态是 `"succeeded"`，导致生产健康样本永远为空、负反馈指标不生效。修复为查询 `"succeeded"`，同步修正 `test_knowledge_health.py` 的 `make_message` helper。
2. **e2e seed 未适配 v0.5 workspace 化（既有 bug）**：`scripts/e2e_workspace_server.py::_seed_workspace` 创建 `KnowledgeBase` 未给 `workspace_id`（NOT NULL），浏览器 e2e 服务无法启动。补建 workspace + owner 成员并挂载知识库。
3. **`MessageResponse.feedback` 与 ORM relationship 同名冲突**：模型新增的 `Message.feedback` relationship 与 schema 字段同名导致 Pydantic 校验失败；relationship 更名 `feedback_records`（DB 语义不变）。
4. **e2e 造数唯一约束**：反馈 e2e 重复创建同名知识库触发唯一约束；造数 helper 支持名称后缀。
5. **ruff 门禁**：新增/既有文件格式与 import 排序统一由 `ruff format` + `ruff check --fix` 修复（含既有 `db.py`/`0018`/`repositories.py` 的历史格式遗留）。

## 5. 结论

P1-4.2 用户反馈闭环已闭环：前端 👍/👎 落库（POST 幂等覆盖、DELETE 取消），
`conversation_detail` 回读反馈状态供 UI 恢复；负面反馈按 distinct 采样消息统计进入
检索健康指标与诊断建议，且经强断言保证 **formula 1.1 score 不变**；错误语义不泄露
消息存在性；SAVEPOINT 并发 upsert 保证提交与取消双路径幂等；全量门禁 18 步全绿。

## 6. 遗留

- P2 企业文档能力（多格式解析/分块）未开始，属下一阶段。
- P4 运营管理大盘中可继续消费 `negative_feedback_rate` / `NEGATIVE_FEEDBACK` 诊断；本批未做管理侧展示。
- 若未来出现多节点共享反馈写入需求，再对齐 ragent RocketMQ `MessageFeedbackConsumer`（当前同步落库）。