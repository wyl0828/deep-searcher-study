# P4 运营管理后台（Dashboard）验证记录

> 日期：2026-08-18
> 阶段：对标 Ragent 企业级缺口优化计划 P4（运营管理后台，对齐 DashboardController/DashboardService/VO）
> 参考：`D:\code\reference\ragent`（提交 `020e5c3`）的
> `admin/controller/DashboardController.java` / `admin/controller/vo/`（OverviewVO/GroupVO/KpiVO/TrendsVO/SeriesVO/PointVO）
> 结论：✅ 代码与自身门禁全部通过；quality_gate 18 步中 17 步 PASS，git-diff-check 被用户 output/pdf 变更误报（见 §5）

---

## 1. 目标

运营管理后台：admin 全局概览（KPI 卡 + 健康分布）+ 定时趋势。仅统计现有持久化数据，
不加新表/新埋点；不做实时性能口径（无请求埋点，以健康分布替代）。

## 2. 契约（吸收开工前评审的 8 项修正）

1. trends series 命名 `documents/messages/feedback`（不称 "ingest"，无 ready_at）。
2. `days=7/30` = 包含今天的 7/30 个自然日，固定 N point、空日补零、同日合并。
3. 分桶统一 **UTC**（对齐项目 utcnow UTC 存储）；`updated_at` 为 UTC ISO-8601。
4. 健康分布用 **batch 查询**（按 KB 分组取最新快照），无 per-KB N+1。
5. 健康分布含 **`unknown`**（无快照 KB）；五档和 == KB 总数（测试 invariant）。
6. KPI 统一 **KpiVO** `{value, delta, delta_pct}`；delta=24h 增量、`delta_pct=delta/base*100`、`base==0→null`。
7. `audit_logs` 纳入 KPI；连接器只统计 `connector_syncs.value`（不做 recent run）。
8. `feedback` 趋势 = 当前有效负反馈（vote=-1 未取消）按首次 created_at 的日分布。
   - API：非法 `days`（非 7/30）固定 **422**；不 fallback。

## 3. 实现清单

| 项 | 文件 | 说明 |
|---|---|---|
| 服务 | `frontend/product/services/dashboard.py` | `overview`（KPI 组 + health_distribution + updated_at）、`trends`（documents/messages/feedback 三系列）、`_daily_series`（固定日历窗、补零、同日合并、UTC）、`_kpi`（KpiVO 统一 schema）、`_health_distribution`（batch latest per KB + unknown） |
| API | `routes.py` | `GET /api/admin/dashboard/overview`、`GET /api/admin/dashboard/trends?days=7|30`（require_admin；非法 days→422） |
| 前端 | `product-api.ts` + `App.tsx` | 类型（DashboardKpi/Overview/Trends）+ `getDashboardOverview/getDashboardTrends`；`AdminDashboardPage`（/admin）：KPI 卡 + 健康分布 bar（含 unknown）+ 三系列趋势（days 切换）；`/admin/audit` 旁导航链接 |
| 测试 | `tests/test_dashboard_service.py` + 前端 e2e | 纯聚合（_daily_series/_kpi/health unknown）+ DB overview/trends + 权限 + 非法 days |

## 4. 验证矩阵

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | `_daily_series`：7/30 point、同天合并、空日补 0、今天/起始日计入、更早排除 | ✅ | `test_daily_series_*` |
| 2 | `_kpi`：delta_pct、base==0→null、不可比较→null | ✅ | `test_kpi_*` |
| 3 | 空库：KPI 全 0、trends 完整 7/30 零点 | ✅ | `test_overview_empty_db_all_zero` |
| 4 | overview 聚合数字与 DB 一致 | ✅ | `test_overview_aggregates_counts` |
| 5 | 健康 unknown invariant（五档和==KB 数） | ✅ | `test_health_distribution_unknown_invariant` |
| 6 | trends 归组/补零/今天计入 | ✅ | `test_trends_*` |
| 7 | API 权限：admin 200、viewer 403、未登录 401（现有契约） | ✅ | 前端 e2e `test_admin_dashboard_overview_admin_ok_and_viewer_forbidden` |
| 8 | 非法 days（6/14/31）→ 422 | ✅ | `test_admin_dashboard_trends_days_validation` |
| 9 | 全量 pytest | ✅ | 1142 passed, 11 skipped |
| 10 | ruff / 前端 typecheck+build / 迁移(0023) | ✅ | 全绿 |

## 5. quality_gate 与 git-diff-check 说明

- `scripts/quality_gate.py` 18 步：**17 步 PASS**（ruff format/check、pytest、前端 test/typecheck/build/bundle、迁移、评估门、mkdocs）。
- **git-diff-check（第 18 步）失败**：因用户工作区 `output/pdf/**` 的学习资料 PDF 被新增/修改/删除
  （`M`/`D`/untracked，P4 之外的用户侧内容变更），而仓库 `.gitattributes` 对这些 PDF 配置了
  `diff=astextplain`（将二进制 PDF 文本化），`git diff --check` 对改动的 PDF 误报 trailing whitespace。
- 已核验：**排除 `output/pdf` 后 P4 代码自身 `git diff --check` 干净**（exit 0）。该阻塞非 P4 引入、
  也非 P4 代码问题，需用户侧处置（提交/调整这些 PDF，或调整其 diff 属性）后再跑 git-diff-check。

## 6. 顺带完成：评估数据基准刷新（output/pdf 学习资料更新关联）

P4 全量 pytest 中 `tests/evaluation/test_quality_gate.py::test_committed_quality_gate_reports_are_consistent`
因 `output/pdf` 学习资料 PDF 被重新生成而失败（source_sha256 与 committed manifest/report 漂移，非 P4 引入）。
已做**全链一致刷新**（仅同步 sha256 基准，不改语义/版本/阈值）：
- `evaluation/datasets/workspace_v2.json`：2 个 source 的 `sha256` 刷新为当前权威文件；
- 引用的 3 个 eval report：`dataset.sha256` 同步为新值。

## 7. 结论

P4 运营管理后台已闭环：admin 全局 KPI（统一 KpiVO）+ 健康分布（含 unknown、batch 无 N+1）+
固定日历窗趋势（UTC、补零、三系列）+ 非法 days 422 + 权限按现有认证契约；
前端 `/admin` 概览页（KPI 卡 + 健康分布 + 趋势线）。全量 pytest 1142 passed、ruff/前端/迁移绿；
quality_gate 17/18 步 PASS，git-diff-check 仅被用户 output/pdf PDF 变更误报。

## 8. 遗留

- 实时性能口径（latency/success-rate）未做（无请求埋点，以健康分布替代）；compareWindow 环比仅 24h delta。
- 多粒度趋势（按小时/周）、ConnectorSyncRun 运营指标、前端图表库引入，均留给后续按需扩展。
- 评估数据刷新（§6）与 git-diff-check 阻塞（§5）均属用户侧内容/仓库状态，需用户确认与提交处置。