# ADR 0011：Team Trust Access Control

## 状态

Accepted，首版实现于 2026-08-16（v0.5.0，迁移 `20260816_0018`）。

## 问题

系统只有单层所有权（`KnowledgeBase.owner_id` / `Conversation.owner_id` 相等校验），无法让多个用户
共享同一知识空间，也无法在共享后保证“未授权知识在检索前不可见”。单纯扩展 owner 判断无法解决撤权、
资源隐藏和跨知识库缓存泄漏问题。

## 决策

引入工作区级共享与检索前实时授权：

- 数据模型：`Workspace` + `WorkspaceMember(role)`；`KnowledgeBase.workspace_id` 最终 `NOT NULL`，
  名称唯一性迁移到工作区级（`(workspace_id, name)`）。会话不挂工作区，经
  `conversation.knowledge_base_id` 继承权限域，`owner_id` 表示私有归属。
- 三角色：`viewer = {read}`、`editor = {read, write}`、`owner = {read, write, admin}`；
  权限用集合表达，不做字符串比较。
- 三层 access service：`user_workspace_role`（实时查当前成员）、`require_workspace_access`
  （工作区级入口：创建 KB=write、成员管理=admin）、`require_accessible_knowledge_base`
  （KB 级入口，按 `kb_id + workspace_members.user_id` 联合查询）。
- **实时授权**：每次触发知识库读取的请求（普通/流式问答、会话创建、预览/下载、健康、重读 KB 的
  引用/证据入口）在读取 Cache、构造 Retriever、取得 `collection_name` 之前，以请求开始时读到的
  当前 `WorkspaceMember` 状态重新执行 read 授权；会话创建时授权不是后续凭据。已授权运行中的流
  不中途再次鉴权。
- 403/404 语义：KB 不存在或当前用户非成员 → 404（不泄露存在性）；成员但角色不足 → 403。
- 单 owner 不变量：`Workspace.owner_id` 为权威 owner 且存在对应 `WorkspaceMember(role=owner)`；
  PostgreSQL 增加 partial unique index（每工作区最多一个 owner）；成员 API 只允许添加/修改
  `editor/viewer`，owner 不可 PATCH/DELETE、不能自我降级；v0.5.0 不做所有权转移。
- Cache 隔离契约：当前与 ragent 参考提交均无跨请求答案缓存；未来若引入，key 必须包含不可混淆的
  `collection_name`/`kb_id`，禁止仅按 query 文本跨 KB 命中，授权始终在缓存读取之前。
- legacy 归属：非 legacy 用户自动获得个人工作区（name=用户名，本人 owner）；legacy 数据在存在
  active admin 时归入排序后首个 admin 的个人工作区，否则使用真实 user row
  `usr_legacy_owner`（username=`__legacy__`），禁止静默给普通用户；`claim_legacy_data`
  同时迁移 `owner_id` 与 `workspace_id`。

## 与 ragent 的关系

ragent 参考提交（`020e5c3`）没有 Workspace/Member/RBAC 实体，只有 `LoginUser(role)` +
`UserContext`（TTL 线程上下文）+ 18 处业务层 `UserContext.getUserId()` 实时数据过滤。
本 ADR 不照搬其模型，采用其“每次请求实时取当前用户并按数据域过滤”的机制：授权总是查当前
`WorkspaceMember`，不缓存会话级权限凭据。

## 已知边界

- 角色粒度为工作区级；per-KB 角色、Group、Organization、所有权转移、工作区删除/重命名留待 v0.5.1。
- 会话始终按创建者私有，不共享会话；共享对象仅限知识库。
- 权限校验集中在 API 层入口，不修改 Milvus/检索核心；检索前隔离依赖“集合随机名 + 授权白名单”。
