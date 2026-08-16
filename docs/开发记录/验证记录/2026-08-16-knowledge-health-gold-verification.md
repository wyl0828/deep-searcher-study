# 2.1 知识健康金标评测验证记录

日期：2026-08-16
提交：见本批提交（test: knowledge health formula 1.1 gold dataset）
来源证据：`evaluation/datasets/knowledge_health_v1.json`、`tests/evaluation/test_knowledge_health_gold.py`、
`frontend/product/services/knowledge_health.py`（aggregate_overall_score）。

## 范围

验证知识健康公式 1.1 的纯规则金标：数据集契约、覆盖率、逐 case 公式契约与顺序不变性。

## 自动回归

- `tests/evaluation/test_knowledge_health_gold.py` 4 项通过：
  - dataset contract：schema/版本/id 唯一/五类各 ≥2/boundary ≥4/expected 合法性；
  - coverage gate：18 cases ≥16；
  - 逐 case formula contract：三维分数 approx、分维度必需/禁止扣分、动作 containment、overall level；
  - permutation 与固定种子 shuffle 顺序不变性（分数、扣分、动作、等级一致）。
- 18 cases 全部通过，覆盖：健康 100、failed、index unverified、duplicate series、overlap、
  `end==next.start` 不 overlap、当前有效版本、series cap、contradicted、invalid citation、
  insufficient evidence、正确拒答不算失败、空库、retrieval/trust 样本不足、adversarial
  warning/critical、permutation。
- `aggregate_overall_score` 提取后 `assemble_health_payload` 行为不变；`tests/test_knowledge_health.py`
  29 项与 `frontend/tests` 全绿。

## 结论

公式 1.1 契约由金标长期锁定；测试走 production 入口、无 DB/时间/模型依赖、顺序无关。若未来
`compute_*` 打红金标，按契约登记缺陷修复，不修改 expected 迎合实现。