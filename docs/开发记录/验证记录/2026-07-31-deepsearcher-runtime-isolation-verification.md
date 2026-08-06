# DeepSearcher 运行时并发与多租户隔离验证

日期：2026-07-31

对应优化项：`S-01 全局配置不适合并发和多租户`

## 验收结论

S-01 已完成。FastAPI 请求不再直接依赖 `deepsearcher.configuration` 的模块级活动对象，而是从
共享控制面解析租户活动版本，并从当前 worker 的 `RuntimeRegistry` 获取不可变 runtime 租约。
两个并发租户可使用不同 LLM 配置和 Collection 白名单；配置发布和回滚对同一部署的多个
Uvicorn worker 一致可见，旧请求结束前不会关闭旧 runtime。

## 实现边界

### 共享控制面

`deepsearcher/runtime_registry.py` 新增：

- `RuntimeControlStore`：使用 SQLite WAL 保存 `runtime_versions` 和
  `tenant_runtime_bindings`。
- `RuntimeRegistry`：每个 worker 按版本构建并缓存 runtime，通过租约引用计数管理生命周期。
- `RuntimeRequestContext`：保存 `tenant_id`、`runtime_version`、`binding_revision`、
  `model_policy` 和 `allowed_collections`。
- 版本发布：候选 runtime 先完整构建，再用 `expected_version` 做事务 CAS；失败候选会关闭且
  不会改变活动绑定。
- 版本回滚：配置 patch、模型策略和 Collection 权限原子恢复；新租户首版本不允许回滚到公共
  基线。

SQLite 文件默认位于 `data/runtime/deepsearcher-runtime.db`。同一主机上的多个 worker 必须
指向同一文件；跨主机部署应把相同事务模型迁移到共享关系数据库，而不是把本地 SQLite 当作
分布式数据库。

### 请求隔离与权限

`main.py` 的 `get_runtime` 是异步 yield 依赖：

1. 服务端解析租户头和服务令牌；
2. 从共享控制库读取该租户当前活动版本；
3. 获取并增加本 worker 对应 runtime 的租约；
4. 把租户策略写入 `request.state.runtime_context`；
5. 响应结束后释放租约，并只回收“引用为零且不再活动”的 runtime。

以下入口都执行同一 Collection 权限校验：

- `/query/`
- `/load-files/`
- `/load-website/`
- `/collections/{collection}/manifest`
- 文档向量删除
- Collection 重建与删除

产品后端通过 `frontend/product/backend.py` 为查询、处理文档、删除文档、重建和删除知识库统一
附加 `X-DeepSearcher-Tenant`；非默认租户还必须使用与 API 相同的
`DEEPSEARCHER_SERVICE_TOKEN`。

### 配置发布安全

- `POST /runtime/tenants/{tenant_id}/versions` 与回滚接口要求
  `DEEPSEARCHER_ADMIN_TOKEN`。
- 非默认租户请求必须同时提供匹配的服务令牌。
- `api_key`、`token`、`password`、`secret` 等敏感字段不能以明文写入版本 patch，只允许
  `{"$env": "TENANT_A_API_KEY"}` 形式；解析后的密钥只存在于构建 runtime 的 worker 内存。
- API 响应只返回版本、策略和 Collection 规则，不回显 provider config。

## 自动化验证

### Python 全量

```text
628 passed, 9 skipped in 20.37s
```

9 个 skip 为显式选择的外部依赖/真实服务用例。本次全量没有失败。

### S-01 聚焦验证

`tests/test_runtime_registry.py` 覆盖：

- 发布新版本时，有活动租约的旧 runtime 不会提前关闭；
- 最后一个旧请求结束后旧向量客户端才关闭；
- 两个 registry 共享同一 SQLite 控制库；
- 两个租户分别使用 `model-a/kb_tenant_a` 与 `model-b/kb_tenant_b`；
- 跨租户 Collection 访问被拒绝；
- 另一 worker 在下一请求观察到发布与回滚；
- 明文密钥被拒绝，环境变量引用在构建时解析。
- runtime 回收会去重关闭 LLM、Embedding、VectorDB 等组件的同步或异步客户端。

`tests/test_query_api.py` 进一步通过真实 FastAPI 依赖链验证：

- 两个线程并发请求分别返回各自模型和 Collection；
- 缺失服务令牌返回 `RUNTIME_TENANT_UNAUTHORIZED`；
- 越权 Collection 返回 `RUNTIME_COLLECTION_ACCESS_DENIED`；
- 两个独立 app/registry 共享控制库后，发布与回滚对观察者可见；
- 回滚同时恢复旧模型、旧 `model_policy` 和旧 Collection 白名单。

运行时注册表与 API 聚焦套件最终结果：

```text
32 passed in 3.94s
```

### 前端与产品后端

```text
Vitest: 3 files passed, 19 tests passed
Vite production build: 585 modules transformed, build succeeded
Ruff scoped check: All checks passed
```

产品服务的 Python 测试包含在 Python 全量中；单独聚焦运行结果为 `22 passed`。

## 真实双 worker HTTP 验证

临时启动：

```text
uvicorn main:app --host 127.0.0.1 --port 8660 --workers 2
```

使用独立控制库和临时服务/管理令牌，依次发布：

1. v2：`live-policy-v1`，Collection 为 `kb_live_a`；
2. v3：`live-policy-v2`，Collection 为 `kb_live_b`。

对 `/runtime/context` 发起 40 次强制新连接请求，实际命中两个 worker：

```text
worker_pid: 68156, 71192
observed runtime versions: 3
observed policies: live-policy-v2
observed collections: kb_live_b
```

同时验证：

```text
使用 tenant-live 请求 kb_live_a: 403 RUNTIME_COLLECTION_ACCESS_DENIED
选择 tenant-live 但缺失服务令牌: 403 RUNTIME_TENANT_UNAUTHORIZED
```

随后以 `expected_version=3` 回滚，另外 40 次请求仍命中上述两个 worker，且结果统一为：

```text
observed runtime versions: 2
observed policies: live-policy-v1
observed collections: kb_live_a
```

临时父进程和两个 worker 已停止，端口 8660 不再提供服务。正式本地环境保留运行，最终状态为
Docker、Milvus、DeepSearcher API `8650` 和用户工作台 `8600` 全部 ready。

## 诚实边界

- 这是同一部署共享事务控制库的多 worker 一致性，不等于跨地域配置系统；多主机应使用共享
  PostgreSQL 等控制库并配置连接级容错。
- 每个 worker 仍有自己的 LLM/Milvus 客户端和连接池，这是进程隔离下的预期行为；共享的是
  版本事实和租户策略。
- 默认 `local` 租户保留 `*` Collection 权限，以兼容当前单用户工作台。开启多租户时应创建
  显式白名单租户，不应复用 `local`。
- 旧的 `init_config()` 仍为 CLI/库调用保留兼容性；FastAPI 在线请求已不再依赖该全局发布
  方式。
