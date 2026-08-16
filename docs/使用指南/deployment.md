# 🌐 部署

本指南介绍 DeepSearcher Study 的部署方式。本项目是独立维护的可信 RAG 工作台，不是上游的
单容器 FastAPI 服务；请按下面三种方式之一部署。

## 一、本地一键启动（推荐）

Windows 下运行 `\start.ps1`（或 `start.bat`），脚本会检查依赖、启动 Milvus、构建前端，并依次
运行核心 API、文档入库 Worker 与用户工作台。默认地址 `http://127.0.0.1:8600`；若端口冲突，
脚本会自动回退到可用端口，实际地址以 `status.ps1` 输出为准。

```powershell
.\start.ps1
.\status.ps1   # 查看服务状态与实际地址
.\stop.ps1     # 停止工作台、API 与 Milvus
```

详细说明见根目录 [README.md](../../README.md) 的“快速入门”。

## 二、Compose 工程模式（本地/服务器同构）

完整拓扑（PostgreSQL / Redis / MinIO / RocketMQ / Milvus / 双 API / 双 Consumer）通过
`docker compose --env-file <env> -f compose.yaml -f compose.server.yaml` 管理；本地覆盖用
`compose.local.yaml`。

```powershell
# 配置校验
docker compose --env-file env.compose.example -f compose.yaml -f compose.local.yaml config --quiet
# 契约检查（服务清单/端口/等待 Alembic/双 API 摘要锁）
uv run --frozen pytest tests/test_compose_contract.py -q
# 分组启动
docker compose --env-file env.compose.example -f compose.yaml -f compose.local.yaml up -d infra
docker compose --env-file env.compose.example -f compose.yaml -f compose.local.yaml up -d vector
docker compose --env-file env.compose.example -f compose.yaml -f compose.local.yaml up -d messaging
docker compose --env-file env.compose.example -f compose.yaml -f compose.local.yaml up -d app
```

## 三、服务器部署

完整步骤、分组验收、故障恢复与 Nginx 接入见 [服务器部署与验收](../部署/服务器部署与验收.md)。
当前服务器目标 `root@47.96.40.156`（公网 IP 可能因 ECS 重启变化，详见该文档“运维要点”第 1 条）；
服务器提供 `deploy/server/` 下的打包、上传、构建、分组启停、备份、恢复、观察与部署验证脚本。

> 注意：上游模板中的 `python main.py` / `docker run` 单容器方式仅适合快速体验上游功能，
> 不包含本项目的 Trust Layer、用户工作台、双 API/双 Consumer、RocketMQ 事务入库与操作审计等能力。
