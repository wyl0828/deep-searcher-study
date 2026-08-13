# v0.3 Trust Layer 预发布收口验证

日期：2026-08-13
目标版本：`0.3.0rc1` / 本地 annotated 标签 `v0.3.0-rc.1`。

## Entailment 校准

- Gold Dataset 1.1.0：63 条；entailed 20、contradicted 21、unknown 22。
- Provider/模型：DeepSeek / `deepseek-v4-flash`；Checker 1.2.0；8 条/批，重复 3 次。
- 推荐阈值：0.90。
- Accuracy 96.30%，Macro F1 96.38%，contradicted Recall 100%。
- 危险误判 0，样本结论稳定率 98.41%，最终 Provider/解析失败为 0；一次批量解析失败由确定性二分补偿隔离，
  所有额外尝试的 Token 均计入报告。
- Checker 保持 `enabled: false`；报告位于
  `evaluation/results/v0.3-entailment-deepseek-20260812/report.json`。

## 多文档检索

- 修正完整文档覆盖契约：文档覆盖只比较规范化来源身份，Gold 页码仅用于 Evidence Recall 和运行后诊断。
- 8 个跨文档样本定向验证达到完整文档覆盖 100%、Hybrid 相对 Dense 综合增益 3.02 个百分点、
  错误率 0，Query Plan、固定计划检索和端到端排名稳定率均为 100%。
- 72 题×3 最终默认产品路径中，Hybrid Recall@8 87.44%、MRR 71.07%、Grounded Criteria
  Coverage 82.35%、完整文档覆盖 62.5%，相对 Dense 综合增益 1.13 个百分点；三类稳定率均为 100%，
  P95 检索延迟 658 ms，满足门禁。
- document-aware decomposition 的定向实验通过，但 72 题全量未胜出，因此实现保留且继续默认关闭；
  最终胜出配置仍为 weighted RRF `1.5:1`、`k=5`、候选倍率 1、Dense anchors 2。

## 发布卫生

- Ruff 格式漂移已清零，格式检查升级为阻断门禁。
- 前端使用 Vite manual chunks 拆分，最大 JS Chunk 约 219 KiB；新增 500 KiB 阻断门禁。
- 版本元数据为 `0.3.0rc1`；本地标签不推送。
- Fast 质量门禁完整通过：949 passed、10 skipped；前端 35 项、Chromium E2E 2 项、迁移、Trust
  数据集、真实 Entailment 报告契约、MkDocs 和 diff 检查均通过。
- 上下文 12 题×3 中 Dense/Hybrid Recall 均为 90%、错误率 0，三类稳定率均为 100%；
  `workspace-065/068` 三次均命中。产品 `/query`、Trace 和流式入口均把内部双查询传给最终 Agent。
- 24 题×3 回答报告中，Naive/DeepSearch/ChainOfRAG Coverage 分别为 85.00%/85.92%/72.42%，
  claim support 为 79.17%/78.49%/79.17%，三类 Agent 错误率均为 0、无答案拒答准确率均为 100%。
- `workspace-015` 的“当前项目”曾被误判为政策时效请求；Freshness Classifier 1.1 将仓库版本语义与
  “现行/当前有效”政策语义分离。Docker Desktop 恢复期间出现 2 次 `TimeoutError` 和 4 次
  `VectorDBUnavailable`；获准 checkpoint 重试后均恢复，最终 `recovered=6`、`still_failed=0`。

## 诚实边界

Entailment 校准仅适用于报告绑定的数据集、模型和 Checker/Prompt 版本，并继续默认关闭。共享
document-aware decomposition 未在 72 题全量中胜出，因此继续默认关闭。2026-08-13 的最终 Full Quality
Gate 已通过：Entailment、72 题×3 检索、12 题×3 上下文、24 题×3 回答以及报告门禁均满足既定阈值；
允许创建发布提交和本地 annotated 标签，不推送远端。
