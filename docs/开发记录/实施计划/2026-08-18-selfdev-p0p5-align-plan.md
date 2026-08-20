# 自研阶段 P0–P5 服务器对齐实施计划（精简版 v1）

> 日期：2026-08-18
> 定位：**当前处于自主研发阶段，无真实生产用户**。本计划把本地已实现的 P0–P5 与服务器运行环境对齐，
> 供自用/演示/继续开发。**不引入**企业级重型发布链（immutable image tag、备份-回滚工程、CI/CD、监控告警、
> 密钥体系迁移）——这些在进入真实使用后再启用。
> 给后续会话：本文件是基于 `docs/开发记录/实施计划/2026-08-16-ragent-enterprise-gap-plan.md` 的“发布/对齐
> 执行清单”。本阶段不把 ECS 作为质量基线；本地 Local Full 是唯一 canonical 结果，ECS 仅为可替换的临时执行节点。

---

## 1. 背景与现状

- 应用 Commit A 为 **`cea1eaa0aa5b5ba04ccd5de756baa866a306bea7`**，PDF Commit B 为 **`a6b03b33557aad73e06531237aa83a05bdaa2e32`**，未 push。
- 最终 SHA 的 Fast Gate **19/19 通过**：Python **1175 passed / 11 skipped**，前端 40 项、E2E 2 项、迁移 `0023` 通过。
- 一次 pre-rewrite Live 尝试完成请求但因 entailment stability/release threshold 未通过；最终 SHA 的 Live/Full 未自动重跑。
- 当前 ECS `118.178.234.18` 的旧 release `20260818-01` 仍 healthy；`20260820-01` 已上传解压，但构建卡在 `uv sync`，未启动新 release。
- 不做 DNS/HTTPS；服务器地址只存在于被 Git 忽略的本地配置或环境变量。

## 2. 目标

让临时执行节点能够运行一个已验证的本地 Git SHA，可用完整能力：
反馈闭环（P1-4.2）、多格式解析分块（P2-A）、可编排入库（P2-B）、连接器同步（P3）、运营大盘（P4）、分布式限流（P5，默认关闭），
以及修复后的 RocketMQ 消费链路与 `document-ingest` 心跳。

## 3. 执行清单（自研阶段）

### S1 源码对齐
1. 基于 `b250613` 生成新 release 目录（替换或并列 `releases/20260815-01`）。
2. 确认 Dockerfile 依赖覆盖 P2/P5 新增（openpyxl / python-pptx / lupa / fakeredis 等）。
3. 确认 Alembic 迁移 0021 / 0022 / 0023 齐备。

### S2 构建与启动
4. `docker compose build`（含前端 build）。
5. `.env.server` 确认 RocketMQ endpoint 走 env（默认 `rocketmq-broker:8080`）。
6. `alembic upgrade head`：现有 schema 兼容确认（additive）+ 空库升级。
7. 有序重启：DB 迁移 → consumer（确认 MQ/heartbeat/ingest）→ product-api → nginx/frontend。

### S3 验证
8. 机器级：`/api/health` ready、心跳 `document-ingest` running、RocketMQ 无 `receive_failed`、Alembic head。
9. 业务 E2E：登录 → 建 Workspace/KB → 上传文档（多格式）→ 检索/回答 → 👍/👎 反馈 → connector → `/admin` 运营大盘 → 权限检查。
10. P1–P5 各一个可辨识 smoke case。

### S4 临时节点验证
11. 只验证当前节点的基础服务、migration、Live/Full（按需）和 P0–P5 smoke。
12. 不做 DNS、HTTPS 或公网发布；更换节点只更新本地忽略配置。

## 4. 本轮不做（进入真实使用后再启用）

- immutable image tag / release manifest / compose 分层强制。
- 发布前 pg_dump 备份-回滚工程、定期恢复演练。
- CI/CD、监控告警、日志聚合。
- 密钥体系大迁移（只需：`.env.server` 不进 git、chmod 600、compose 不写明文业务秘密、release 不复制敏感配置）。
- P6 检索智能、MCP、Agent（保持后置）。

## 5. 状态跟踪

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 本地固化（Commit A/B） | ✅ 2026-08-20 |
| S1 | runtime package 排除 PDF、动态节点配置、manifest | ✅ Commit A |
| S2 | 本地 Fast Gate | ✅ 19/19；Python 1175 passed / 11 skipped |
| S3 | 本地 Live/Full | ⚠️ Live provider evaluation 未通过；Full 未重复执行 |
| S4 | 临时 ECS `20260820-01` | ⚠️ 已上传；构建在 uv 依赖同步阶段中止，旧 release 未受影响 |
| — | Commit C 验证记录 | 待本次事实记录提交 |

## 6. 收敛目标

> 本地 Git SHA → 本地 canonical 质量结果；临时 ECS 只提供兼容性结果和 smoke 事实。

当前 Commit A/B 与本地 Fast 已收口；Live/Full 和 ECS smoke 因分别遇到 provider evaluation gate 与 ECS 依赖构建问题尚未完成。后续只在明确授权后排查这两个事实，不扩展发布治理。
