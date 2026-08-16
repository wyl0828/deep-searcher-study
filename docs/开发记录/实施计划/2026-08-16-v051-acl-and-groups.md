# v0.5.1 per-KB 覆盖与成员组实施计划

> 日期：2026-08-16
> 状态：已实现并提交（验证见 `docs/开发记录/验证记录/2026-08-16-v051-acl-and-groups-verification.md`）
> 前置：`2943947` v0.5.0 Team Trust

## 目标

在 v0.5.0 工作区权限之上补两个能力，并把权限模型冻结为四层优先级 + 四条数据规则：

1. per-KB 差异化角色（KB override，可升可降）；
2. 成员组批量授权（Group，仅补充 read/write）。

## 决策摘要（契约）

- 权限优先级：非成员 404 → owner 全权限（不参与 override/group）→ 普通成员
  max(个人, 组) → KB override 覆盖。
- admin 只来自 personal WorkspaceMember.role == owner。
- 实时重算；已入队异步 mutation 由系统身份完成。
- 四条数据规则：owner 永无 ACL；非成员永无 ACL；KB 成员与 KB 同 workspace（复合 FK）；ACL mutation
  先锁 WorkspaceMember 行。

## 实现清单

- [x] 模型：KnowledgeBaseMember / MemberGroup / GroupMember + KnowledgeBase UNIQUE(workspace_id, id)
- [x] 迁移 `20260816_0019`（复合 FK + cascade + 热路径索引）
- [x] access service：effective_workspace_role / effective_kb_role / has_workspace_admin + ACL 行锁
- [x] API：KB members 与 Groups（均 admin-only）
- [x] 前端：KB 详情成员覆盖区 + 工作区成员组管理
- [x] E.1 verify-failover.ps1/.sh + E.3 能力矩阵刷新
- [x] 测试：tests/test_team_trust.py 21 项 + API 端到端 2 项
- [x] 文档：ADR 0011、roadmap、CHANGELOG、本计划、验证记录

## 验收标准

- KB override 升降级矩阵、owner/非成员不可入 ACL、跨 workspace DB 拒绝、组角色提升、组不授予
  admin、移除/晋升清理 ACL、脏数据无访问全部通过。
- 全量 pytest 与 frontend/tests 全绿；前端 typecheck + 35 项 + build；mkdocs build 通过。