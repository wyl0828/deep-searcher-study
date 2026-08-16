# v0.5.1 per-KB 覆盖与成员组验证记录

日期：2026-08-16
提交：见本批提交（feat: v0.5.1 per-KB overrides and member groups）
来源证据：`frontend/product/models.py`、`frontend/product/migrations/versions/20260816_0019_kb_and_group_acls.py`、
`frontend/product/services/access.py`、`frontend/product/routes.py`、`tests/test_team_trust.py`、
`frontend/tests/test_product_api.py`、`frontend/src/App.tsx`、`deploy/server/verify-failover.ps1|.sh`。

## 范围

验证 per-KB 覆盖、成员组、四层权限优先级与四条 ACL 数据规则。

## 自动回归

- `tests/test_team_trust.py` 21 项通过：KB 覆盖升降级与即时生效、owner 不可入 ACL、非工作区成员
  不可入 ACL（service 404 + DB 复合 FK 拒绝跨 workspace）、组角色提升（个人 viewer + 组 editor）、
  组不授予 admin、owner 不可入组、移除工作区成员同事务清理 GroupMember 与 KnowledgeBaseMember、
  提升为 owner 清理 ACL、脏数据 GroupMember 无访问。
- `frontend/tests` 通过（含新增端到端 2 项）：KB members API admin-only（viewer GET/POST 403，
  owner 完整增删改）、Groups API admin-only 与完整流程。
- 前端：typecheck 通过、vitest 35 项通过、生产构建通过。

## 迁移验证（真实执行）

- 空 SQLite 库 `alembic upgrade head` 至 `20260816_0019` 成功（三表 + 复合 FK + 索引）。
- 复合 FK 拒绝跨 workspace 组合（启用 PRAGMA foreign_keys 的 SQLite 下 IntegrityError）。

## E.1/E.3

- 新增 `deploy/server/verify-failover.ps1|.sh`：六场景（跨节点身份证据、单 API/Consumer 停止接管、
  幂等逻辑身份集合断言、事务回查拆分自动+compose、全进程重启持久化），待 Docker 环境执行并记录。
- 能力矩阵刷新：已由 `4d0cf90` 关闭的缺口移出"已知缺口"，补充 v0.5.0/v0.5.1 权限行。

## 结论

per-KB 覆盖与成员组在单元、API 端到端、前端与迁移层面验证通过；四条 ACL 数据规则由 DB 复合 FK +
cascade + service 校验 + 行锁双保险。Organization、所有权转移与 Milvus 侧 ACL 留待后续。