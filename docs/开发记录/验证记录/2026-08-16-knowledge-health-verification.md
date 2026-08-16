# 知识健康（v0.4 起始纵切）验证记录

日期：2026-08-16
提交：`ff5fbcf`（feat: add v0.4 knowledge health starting slice）
来源证据：`tests/test_knowledge_health.py`、`frontend/product/services/knowledge_health.py`、
`frontend/product/models.py`（`KnowledgeHealthSnapshot`）、迁移 `20260816_0017`、
`frontend/product/routes.py`（三个 health 路由）、`docs/roadmap/trustworthy-rag-roadmap.md`。

## 范围

验证 Knowledge Health 公式 v1.0（数据 40% + 检索 30% + 可信 30%）的计算、扣分与建议、快照持久化
与历史对比，以及工作台“知识健康”面板的接口契约。不验证趋势预测、阈值告警或自动修复（未实现）。

## 自动回归

- `tests/test_knowledge_health.py` 12 项全部通过：空库 0 分、全可用 100 分、失败文档/缺索引扣分、
  样本不足（<3）不参与总分、拒答率扣分、声明支持率/无效引用/一致性/语义矛盾扣分、快照持久化
  与上一快照 delta 对比。
- Python 全量：911 passed、6 skipped。
- 前端：类型检查通过；4 个文件 35 项测试通过；生产构建通过。

## API 冒烟（真实服务）

认证后创建知识库、上传真实文档并完成 3 条 grounded 问答：

- `GET /api/knowledge-bases/{id}/health` 返回 `snapshot=null` 与 `current` 完整 100 分（complete）；
- `POST /api/knowledge-bases/{id}/health/snapshot` 创建 `khs_` 前缀快照并返回与上一快照的 `change`；
- `GET /api/knowledge-bases/{id}/health/history` 返回 1 条快照记录。

## 结论

知识健康起始纵切在单元、前端与真实 API 冒烟层面通过；快照不是黑盒分数，公式版本、原始指标、
扣分原因与建议动作均持久化。多知识库并发快照与超长历史回放未做专门压测，属已知边界。
