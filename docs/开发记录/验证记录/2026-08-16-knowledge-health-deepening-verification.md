# 知识健康深化（1.1–1.3）验证记录

日期：2026-08-16
提交：见本批提交（feat: deepen v0.4 knowledge health）
来源证据：`frontend/product/services/knowledge_health.py`、`frontend/product/routes.py`、
`frontend/src/App.tsx`、`tests/test_knowledge_health.py`、`frontend/tests/test_product_api.py`。

## 范围

验证公式 1.1 的 series 检测、检索归因、等级/趋势/动作闭环，以及前端健康面板的新增交互。

## 自动回归

- `tests/test_knowledge_health.py` 29 项通过：series 重复分组一条、`end == next.start` 连续不扣、
  90/91 天断代阈值、future effective 不触发多当前、非法区间排除、重复组与断代共存的局部抑制、
  overlap 相邻扫描、orphan、penalty 封顶 40；归因 unknown/样本数/分母；等级边界 39/40/59/60；
  趋势升序含 level；动作 UPLOAD/未知 code/RETRY（mock service）。
- `frontend/tests` 128 项通过（含新增 health API 路由测试：trend 返回含 level 字段、
  actions/run 返回 requires_user_action 与 snapshot/delta、未知动作返回 400）。
- 前端：typecheck 通过、vitest 35 项通过、生产构建通过。

## 结论

公式 1.1 契约（series penalty 数值与封顶、等级阈值、归因结构、动作 delta 语义）在单元与 API 层
验证通过；前端完整闭环（等级徽标、趋势等宽 bar、动作按钮）。动作执行后的 delta 为即时重新评估值，
异步 Worker 完成后的真实变化由后续快照反映，未在本轮做异步压测。