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

## 本地存储层真实验证

使用 Docker Desktop 29.4.1 启动 `infra` 分组，结果如下：

- PostgreSQL 16、Redis 7、MinIO 均进入 healthy；MinIO 初始化任务退出码为 0；
- `deepsearcher-documents` 与 `milvus-bucket` 创建成功；
- PostgreSQL 空库完成全部 Alembic 迁移，当前 revision 为 `20260813_0015`；
- PostgreSQL 真实任务抢占集成测试通过；
- 人工构造落后至 `20260813_0014` 的独立测试库，Schema 校验按预期拒绝；
- MinIO 对象上传、读取、重启后读取和删除通过；
- Redis 数据重启恢复通过，两个竞争者对同一摘要锁仅一个获得，释放后另一方可获得；
- 停止分组后项目容器均已移除，PostgreSQL、Redis、MinIO 命名卷保留。

应用镜像 `deepsearcher-study:local` 已真实构建，并验证生产运行环境可直接导入 PostgreSQL、S3、Redis、
RocketMQ 客户端且包含前端产物。收紧 `.dockerignore` 后构建上下文由约 775 MiB 降至约 55 KiB，镜像
约 356 MiB；运行命令直接使用构建期虚拟环境，不在容器启动时重新安装项目。

## 尚未完成

- RocketMQ 消息分组的真实启动与 Compose 链路验收；
- Milvus 向量分组与双 API 分项集成验收；
- 本地短时完整冒烟；
- Linux 服务器反向代理覆盖与完整多实例验收。

后续记录必须基于真实执行结果更新，不把静态 Compose 校验写成运行时能力证明。
