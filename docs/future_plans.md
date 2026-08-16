# 后续计划

本项目按 [可信 RAG 路线图](roadmap/trustworthy-rag-roadmap.md) 与
[企业级缺口优化计划](开发记录/实施计划/2026-08-16-ragent-enterprise-gap-plan.md) 推进。当前待办：

- **P1-4.2 用户反馈闭环**：回答 👍/👎 落库（MessageFeedback），重复提交幂等，健康报告纳入负反馈样本。
- **P2 企业文档能力**：Excel/PPT/图片解析与表格/代码分块；入库链路节点化可编排。
- **P3 v0.6 连接器**：本地目录/SMB 连接器 + 定时刷新 + 删除/权限同步。
- **P4 运营大盘**：管理端 KPI 概览与趋势。
- **P5 流量治理（按需）**：多实例共享模型额度时启用 Redis 公平排队。

已完成（不再列入待办）：RESTful API 接口、系统级操作审计（P1-4.1）、多实例故障切换六场景与
Chat 双 Provider fallback 验证（P0）。

欢迎参与贡献！欢迎 Star、Fork 项目，一起把 DeepSearcher 做得更强大。🎯
