# v0.4 知识健康深化实施计划（1.1–1.3）

> 日期：2026-08-16
> 状态：已实现并提交（验证见 `docs/开发记录/验证记录/2026-08-16-knowledge-health-deepening-verification.md`）
> 前置：`ff5fbcf` v0.4 知识健康起始纵切（公式 1.0）

## 目标

把 v0.4 知识健康从“可解释的三维评分”推进到“可解释、可归因、可行动”：

1. 数据健康增加 version family 语义检测（重复、重叠、断代、取代异常、孤立）；
2. 检索健康增加真实问答归因；
3. 增加等级、趋势与建议动作闭环。

## 决策摘要（公式 1.1 契约）

- 公式版本升级 1.1：新增扣分代码会改变评分行为；旧快照保留 1.0 不动态套用。
- series 检测按“异常关系/分组”扣一次，禁止逐文档重复扣；抑制为局部关系级，不做 series 级短路。
- “当前有效” = `start <= now` 且 `end IS NULL`；未来版本不触发多当前。
- 时间区间统一 `[start, end)`；`end == next.start` 为正常连续；90 天内空档允许。
- penalty：`DUPLICATE -10/组`、`OVERLAP -10/关系`、`GAP -5/关系`、`ANOMALY -10/单元`、
  `ORPHANED -5/文档`；累计封顶 -40，`data_score` clamp 0..100。
- 归因纯统计：`query_type or "unknown"`，记录 `sample_count/count/rate`；
  未引用文档以全部 ready 文档为分母，措辞为“最近样本中未被引用”。
- 等级机器码 `healthy/warning/critical`（>=60 / >=40 / <40），`health`/`snapshot`/`trend` 共用。
- 动作闭环复用现有 `retry_document` / `reindex_knowledge_base`，只做编排；
  delta 为即时重新评估值，不隐含异步完成。

## 实现清单

- [x] 数据健康 series 检测（`knowledge_health.py` `compute_series_detections`）
- [x] 检索归因（`compute_retrieval_attribution`）
- [x] 等级 `get_health_level`、趋势 `health_trend_payload`、动作 `run_health_actions`
- [x] 路由：`GET /health/trend`、`POST /health/actions/run`
- [x] 前端：等级徽标、趋势等宽 bar、动作按钮（`App.tsx`/`product-api.ts`/`workspace.css`）
- [x] 测试：series/归因/等级/趋势/动作 + API 路由测试
- [x] 文档：ADR 0010、roadmap、CHANGELOG、验证记录

## 验收标准

- 重复/重叠/断代/取代异常/孤立各产生对应 deduction，penalty 与封顶生效。
- 归因可解释“哪些文档最近未被引用、哪些问题类型拒答/证据不足偏高”。
- trend 返回含 `level` 的升序时间序列；动作执行后即时重新快照。
- 回归：`tests/test_knowledge_health.py` 29 项、`frontend/tests` 128 项、前端 35 项 + 构建通过。