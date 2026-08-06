# DeepSearcher 浏览器安全基线与认证边界验证记录

日期：2026-08-02

范围：用户工作台浏览器响应头、API 缓存策略、PDF 原文缓存兼容性，以及终端用户认证/RBAC
现状审计。

## 1. 审计结论

当前产品工作台仍是绑定 `127.0.0.1` 的单用户本地应用，不具备终端用户认证或资源级授权：

- 数据模型没有 User、Workspace、Membership、AuthSession，也没有知识库/对话的 owner 或
  workspace 归属。
- 浏览器经工作台 BFF 访问核心 API，核心服务令牌不会下发给浏览器；但该令牌只解决服务间
  鉴权，不能代表最终用户身份。
- `/api` 不校验最终用户，资源 ID 不能作为授权凭证。因此在认证/RBAC 实施前，工作台不得直接
  暴露到局域网或公网。

目标模型、角色矩阵、会话/CSRF 契约和迁移顺序见
`docs/开发记录/设计规范/2026-08-02-deepsearcher-workspace-auth-rbac.md`。

## 2. 已落地的无争议安全基线

工作台中间件为全部响应增加：

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- 最小 `Permissions-Policy`
- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Resource-Policy: same-origin`

HTML 额外增加仅允许同源脚本、字体、样式和连接的 CSP，并显式禁止 base、object、frame 与跨源
表单提交。普通 `/api` 默认使用 `Cache-Control: no-store`；端点显式声明的缓存策略优先，因此
PDF 原文仍保持 `private, max-age=3600`。

本地请求边界还包括：

- 默认只接受 `localhost`、`127.0.0.1` 和 `::1` Host，其他 Host 在路由执行前返回
  `400/INVALID_HOST`，降低 DNS rebinding 风险。
- POST/PUT/PATCH/DELETE 同时检查 Origin 和 `Sec-Fetch-Site`；跨站写请求在读取请求体和执行
  业务逻辑前返回 `403/CROSS_SITE_REQUEST_BLOCKED`。
- 不带浏览器来源头的本地 CLI 保持兼容。可信反向代理部署需要显式配置允许的 Host 与唯一公开
  Origin，不根据任意转发头自动放宽。

这些响应头属于纵深防御，不等同于登录、会话、CSRF 或 RBAC。

## 3. 自动化验证

```text
uv run pytest -q
732 passed, 10 skipped

uv run ruff check .
All checks passed

uv lock --check
passed

git diff --check
passed

npm test -- --run
4 test files passed, 25 tests passed

npm run build
585 modules transformed; production build passed
```

Python 测试默认把 warning 当作错误。本轮新增测试覆盖 API 的 `no-store` 和通用安全头、SPA
深链接 HTML 的 CSP、可信与恶意 Host、Origin/Fetch Metadata、CLI 兼容和显式反向代理配置。

## 4. 真实服务验证

使用 `stop.ps1 -KeepMilvus` 后重新启动全套服务，`status.ps1` 显示 Docker、Milvus、核心 API、
Document Worker 和 User Workspace 全部 ready。

在 `http://127.0.0.1:8600` 实测：

- `GET /chat/new` 返回 `200`，包含同源 CSP、`X-Frame-Options: DENY`、`nosniff` 与 COOP。
- `GET /api/health/live` 返回 `200/alive`，包含 `Cache-Control: no-store` 和通用安全头，且不会把
  HTML CSP 错加到 JSON 响应。
- `GET /api/documents/doc_d8555f3a34b741d788fe88793d489fce/content` 返回 `200`、
  `application/pdf`、inline 下载名和 `57,338` 字节；缓存仍是 `private, max-age=3600`，同时带有
  通用安全头。
- 使用 `Host: attacker.example` 请求 liveness 返回 `400/INVALID_HOST`，并保留请求 ID 与安全
  响应头。
- 使用恶意 Origin，或伪造同源 Origin 但声明 `Sec-Fetch-Site: cross-site`，POST 查询均返回
  `403/CROSS_SITE_REQUEST_BLOCKED`。
- 使用真实 `Origin: http://127.0.0.1:8600` 和 `Sec-Fetch-Site: same-origin`，以及不带浏览器来源
  头的 CLI 请求，均继续进入路由并按预期返回参数校验结果 `422/INVALID_REQUEST`。

结论：安全响应头和本地请求边界已经进入真实运行服务，没有覆盖 PDF 缓存或正常同源/CLI
契约；终端用户认证与 RBAC 仍需先确定身份源后再进入数据迁移和界面实现。
