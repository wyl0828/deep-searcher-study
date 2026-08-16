# v0.5.0 Team Trust 最小闭环验证记录

日期：2026-08-16
提交：见本批提交（feat: v0.5.0 team trust workspace sharing）
来源证据：`frontend/product/models.py`、`frontend/product/migrations/versions/20260816_0018_workspace_team_trust.py`、
`frontend/product/services/access.py`、`frontend/product/routes.py`、`tests/test_team_trust.py`、
`frontend/tests/test_product_api.py`、`frontend/src/App.tsx`。

## 范围

验证工作区共享、检索前实时授权、403/404 语义、单 owner 不变量与自动迁移。

## 自动回归

- `tests/test_team_trust.py` 9 项通过：权限矩阵（viewer/editor/owner × read/write/admin）、
  非成员与不存在 KB 相同 404、成员管理需 admin、成员只能加 editor/viewer、owner 不可 PATCH/DELETE、
  撤权后后续访问 404、editor→viewer 后写 403、create_workspace 同事务建 owner membership、
  ensure_personal_workspaces 幂等且 workspace_id 全非空。
- `frontend/tests` 通过（含团队 API 端到端 3 项）：撤权后旧会话消息请求返回 404（未触达 Retriever）、
  非成员创建会话 404、viewer 重建索引 403。
- 前端：typecheck 通过、vitest 35 项通过、生产构建通过。

## 迁移验证（真实执行）

- 空 SQLite 库 `alembic upgrade head` 至 `20260816_0018` 成功。
- 0017 库预置 admin/bob/legacy 用户与 KB → 升级 0018 后：
  kb_a→admin 个人工作区、kb_b→bob 个人工作区、kb_legacy→`__legacy__`（owner=首个 active admin）；
  `workspace_id IS NULL` 数量为 0；每个工作区恰一个 owner membership。
- `claim_legacy_data` 同步迁移 owner 与 workspace（auth 测试通过）。

## 结论

v0.5.0 最小闭环在单元、API 端到端、前端与真实迁移层面验证通过。实时授权在每次请求入口生效，
已授权运行中的流不中途鉴权；per-KB 角色、Group、Organization 与 Milvus 侧 ACL 留待 v0.5.1。