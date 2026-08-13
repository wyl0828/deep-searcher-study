# Compose 工程环境基线验证记录

日期：2026-08-13

## 本阶段范围

本阶段只建立可重复的本地分项验证入口，不宣称已完成服务器完整验收。拓扑覆盖：

- PostgreSQL、Redis、MinIO；
- RocketMQ NameServer、Broker/Proxy 与初始化任务；
- etcd、Milvus；
- Alembic 一次性迁移任务；
- Core API、两个产品 API、两个 RocketMQ Consumer。

保留现有 SQLite、本地文件与租约 Worker 的轻量启动方式；Compose 工程模式使用 PostgreSQL、S3、
RocketMQ 和共享 Redis，不引入 Outbox、Kubernetes、管理后台或额外消息状态平台。

## 已完成验证

```text
docker compose --env-file env.compose.example \
  -f compose.yaml -f compose.local.yaml config --quiet
```

结果：通过。

```text
uv run --frozen pytest tests/test_compose_contract.py -q
```

结果：5 passed。

契约检查覆盖：

- 完整服务清单与持久卷存在；
- 基础 Compose 不向宿主机发布端口；
- 本地覆盖文件仅绑定 `127.0.0.1`；
- 产品 API 和 Consumer 等待 Alembic 成功；
- 双 API 模式显式启用共享 Redis 摘要锁。

Fast Gate 结果：通过，包括 991 项 Python 测试、35 项前端测试、2 项浏览器 E2E、Alembic 全量升级、
Trust/风险/Provenance 报告门禁与 MkDocs 构建；另有 11 项依赖外部服务的测试按既有条件跳过。

## 尚未完成

- 应用镜像完整构建（本次执行时 Docker Desktop daemon 未运行）；
- 各分组真实启动、健康检查和停止清理；
- 本地存储、消息、向量与双 API 分项集成验收；
- 本地短时完整冒烟；
- Linux 服务器反向代理覆盖与完整多实例验收。

后续记录必须基于真实执行结果更新，不把静态 Compose 校验写成运行时能力证明。
