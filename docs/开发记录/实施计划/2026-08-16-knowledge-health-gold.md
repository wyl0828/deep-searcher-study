# 2.1 知识健康金标评测实施计划

> 日期：2026-08-16
> 状态：已实现并提交（验证见 `docs/开发记录/验证记录/2026-08-16-knowledge-health-gold-verification.md`）
> 前置：`eef82fe` v0.5.1

## 目标

为知识健康公式 1.1 建立纯规则金标，长期锁定评分契约：

1. `evaluation/datasets/knowledge_health_v1.json`（18 cases）覆盖五类业务场景与公式边界；
2. 测试逐 case 走 production 入口，精确断言分数、分维度扣分、动作与等级；
3. 验证顺序不变性，保证公式契约不因输入排列变化而漂移。

## 决策摘要（契约）

- `dimension` 仅标注主验证维度，不控制 compute 运行；三个 compute 都执行。
- `_score = null` 表示该维度真实不可计算/partial，与 dimension 无关。
- deduction 按 `data/retrieval/trust` 分维度断言（必需 + 禁止）；action 为整体系统建议（扁平）。
- overall/level 走 production 聚合入口 `aggregate_overall_score`（提取自 `assemble_health_payload`，
  行为不变）；`overall is None` 时 level 直接为 None，不调用 `get_health_level(None)`。
- 不修改 `compute_*` 评分行为；金标打红即按契约登记缺陷修复，不修改 expected 迎合实现。

## 实现清单

- [x] 提取 `aggregate_overall_score`（生产/测试共享，行为不变）
- [x] `evaluation/datasets/knowledge_health_v1.json`（18 cases：五类 + series 边界 + permutation）
- [x] `tests/evaluation/test_knowledge_health_gold.py`（dataset contract + coverage gate +
  formula contract + 顺序不变性）
- [x] 文档：roadmap、CHANGELOG、本计划、验证记录

## 验收标准

- 18 cases 逐 case 通过（分数 approx、分维度必需/禁止扣分、动作、overall level）。
- dataset contract 与 coverage gate（≥16、五类各 ≥2、boundary ≥4）通过。
- permutation 与固定种子 shuffle 等价。
- 全量 pytest 与 frontend/tests 全绿；mkdocs build 通过。