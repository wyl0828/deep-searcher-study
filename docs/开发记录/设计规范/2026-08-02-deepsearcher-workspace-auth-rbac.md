# DeepSearcher 用户认证与 RBAC 设计方案

日期：2026-08-02  
状态：等待产品身份源决策  
范围：用户工作台 `/api`、知识库/文档/对话归属、浏览器会话、核心租户映射与审计。

## 1. 当前边界

当前工作台是绑定 `127.0.0.1` 的单用户本地产品：

- 数据库没有用户、工作区、成员或登录会话。
- 知识库、文档和对话都是实例级共享资源；`KnowledgeBase.is_current` 是全局偏好。
- 浏览器不持有核心服务令牌；工作台 BFF 使用进程内临时令牌访问核心 API，这一层已经正确，
  但它解决的是服务间鉴权，不是终端用户鉴权。
- 所有 `/api` 路由都能被同机调用；资源 ID 不能作为权限凭证。

因此当前版本可以作为本机学习工具使用，不能在没有额外访问边界时暴露到局域网或公网。

## 2. 目标数据模型

身份提供商可以替换，但业务授权模型保持一致：

| 实体 | 关键字段 | 约束 |
| --- | --- | --- |
| User | `id, issuer, subject, email, display_name, status` | `(issuer, subject)` 唯一；不以可变邮箱作为身份主键 |
| Workspace | `id, name, created_by` | 所有业务资源必须属于一个 Workspace |
| Membership | `workspace_id, user_id, role` | 角色仅允许 `owner/admin/member/viewer`，联合唯一 |
| AuthSession | `id_hash, user_id, expires_at, revoked_at` | 数据库只保存随机会话 ID 的哈希，不保存明文 Cookie |
| UserPreference | `user_id, workspace_id, current_knowledge_base_id` | 替代全局 `KnowledgeBase.is_current` |
| AuditEvent | `actor, workspace, action, resource, request_id, outcome, created_at` | 追加写；不记录问题正文、文档正文或凭据 |

KnowledgeBase 新增非空 `workspace_id` 与 `created_by`；Conversation 新增 `created_by`。Document、
IngestJob、Message 和 Citation 通过父级资源继承工作区，不允许只按裸 ID 查询。

## 3. 权限矩阵

| 能力 | owner | admin | member | viewer |
| --- | --- | --- | --- | --- |
| 查看知识库、文档与引用 | 是 | 是 | 是 | 是 |
| 创建对话并发起查询 | 是 | 是 | 是 | 可配置，默认否 |
| 上传/重试文档 | 是 | 是 | 是 | 否 |
| 删除文档、重建索引 | 是 | 是 | 否 | 否 |
| 创建/删除知识库 | 是 | 是 | 否 | 否 |
| 管理成员与角色 | 是 | 否 | 否 | 否 |
| 删除 Workspace | 是，需重新认证 | 否 | 否 | 否 |

授权必须在数据库查询条件中包含 `workspace_id`；先按 ID 查出对象、再在 Python 中比较 owner
容易形成越权旁路，不作为主实现方式。所有写操作记录安全审计事件。

## 4. 浏览器与接口契约

- `GET /api/auth/session`：返回安全用户 DTO、工作区与角色，不返回 Provider Token。
- 登录完成后使用随机 HttpOnly、Secure（HTTPS）、SameSite=Lax/Strict Cookie；服务端保存会话哈希，
  支持撤销、绝对过期和空闲过期。
- 所有非 GET/HEAD 请求校验同源 `Origin`，并使用 CSRF Token；OIDC 回调校验 state、nonce 与 PKCE。
- 未登录统一 `401/AUTH_REQUIRED`，无权限统一 `403/PERMISSION_DENIED`，仍保留现有
  `request_id/retryable` 错误信封。
- SSE 在建立连接前完成身份和 Workspace 授权；断线后继续使用当前协作取消机制。
- Worker Job 必须携带 workspace 上下文；工作台映射到核心 `X-DeepSearcher-Tenant`，Collection
  allowlist 继续作为第二层防线。

## 5. 迁移方案

1. 创建默认 Workspace，把现有全部知识库迁入；创建首位 owner。
2. 为 KnowledgeBase/Conversation 添加可空归属字段，回填并建立索引后再改为非空。
3. 新建 UserPreference，把全局 `is_current` 迁移为首位用户偏好；兼容窗口结束后删除全局列。
4. 所有 Repository 改为 `workspace_id + resource_id` 查询，并增加跨 Workspace 负向测试。
5. 路由接入 Principal/Permission dependency，再启用登录 UI；最后才允许绑定非 loopback 地址。
6. 验收跨用户读取、下载、查询、删除、重建、SSE 和审计均不能越权。

## 6. 身份源选项

### A. 本地管理员密码

适合保持单机离线体验。密码使用 Argon2id 哈希，首次启动通过受控 CLI 创建管理员，不提供默认
密码。实现量较小，但不适合组织级 SSO 和多用户生命周期。

### B. OIDC（部署型产品推荐）

对接支持 OIDC 的身份平台，应用只保存 issuer/subject 和业务角色。支持组织 SSO、MFA 与集中
停用，但需要确定回调域名、HTTPS、Provider 和部署环境。

### C. 可信反向代理身份

由企业网关完成认证，工作台只接受来自固定代理且经过签名/相互 TLS 保护的身份头。适合已有
零信任网关的环境，不能直接信任公网请求传入的普通 `X-User-*` 头。

## 7. 当前已落地的基础安全头

身份源决策前，工作台已统一启用：

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY` 与 CSP `frame-ancestors 'none'`
- `Referrer-Policy: no-referrer`
- 最小 `Permissions-Policy`
- `Cross-Origin-Opener-Policy/Cross-Origin-Resource-Policy: same-origin`
- HTML 同源 CSP；普通 `/api` 默认 `Cache-Control: no-store`
- 默认只接受 loopback Host，阻止 DNS rebinding 使用任意 Host 访问本地工作台
- POST/PUT/PATCH/DELETE 校验浏览器 Origin 与 Fetch Metadata，拒绝跨站写请求

这些是纵深防御，不构成认证或 RBAC。
