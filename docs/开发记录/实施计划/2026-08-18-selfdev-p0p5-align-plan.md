# 自研阶段 P0–P5 服务器对齐实施计划（精简版 v1）

> 日期：2026-08-18
> 定位：**当前处于自主研发阶段，无真实生产用户**。本计划把本地已实现的 P0–P5 与服务器运行环境对齐，
> 供自用/演示/继续开发。**不引入**企业级重型发布链（immutable image tag、备份-回滚工程、CI/CD、监控告警、
> 密钥体系迁移）——这些在进入真实使用后再启用。
> 给后续会话：本文件是基于 `docs/开发记录/实施计划/2026-08-16-ragent-enterprise-gap-plan.md` 的“发布/对齐
> 执行清单”。`b250613` 是 8 月 18 日的部署基线；本次刷新已核对当前本地与服务器的实际版本。

---

## 1. 背景与现状

- 本地当前 HEAD 为 **`8163000`**（`feat(answer): enforce definition-first strategy`），比 `origin/study-baseline` 领先 9 个提交，尚未推送。
- 当前本地质量结果：全量 pytest **1169 passed / 11 skipped**；前端 40 项测试、TypeScript 检查和生产构建通过。
- 服务器 `118.178.234.18` 当前运行 **`20260818-01` release**；core-api、两个 product-api、两个 consumer 及依赖容器均为 healthy，三个回环健康接口均返回 HTTP 200。
- 服务器已不再是 8/15 的旧 release，但本地最新 `8163000` 尚未部署；当前服务器版本与本地 HEAD 的精确 SHA 仍需在下一次发布时固化到 release manifest。
- 域名 `deepsearcher.aimianshi.xyz` 尚未配置 DNS A 记录。

## 2. 目标

让服务器运行版本与一个已验证的本地 Git SHA 一致，可用完整能力：
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

### S4 对外访问（可选但推荐）
11. DNS A 记录 `deepsearcher` → `118.178.234.18`（需解析商操作；自研阶段亦可先保留本机 hosts）。
12. HTTPS：`80 → 301/443`（自研阶段可先 HTTP，对外稳定访问再上证书）。

## 4. 本轮不做（进入真实使用后再启用）

- immutable image tag / release manifest / compose 分层强制。
- 发布前 pg_dump 备份-回滚工程、定期恢复演练。
- CI/CD、监控告警、日志聚合。
- 密钥体系大迁移（只需：`.env.server` 不进 git、chmod 600、compose 不写明文业务秘密、release 不复制敏感配置）。
- P6 检索智能、MCP、Agent（保持后置）。

## 5. 状态跟踪

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 本地固化（历史部署基线=`b250613`；当前 HEAD=`8163000`） | ✅ 2026-08-19 |
| S1 | 源码对齐到服务器 release `20260818-01` | ✅ 已有运行镜像；尚未与当前 HEAD 建立 SHA 清单 |
| S2 | 构建与启动 | ✅ `20260818-01` 容器 healthy，三个健康接口 HTTP 200 |
| S3 | 验证（机器级 + 业务 E2E + P1–P5 smoke） | 部分完成：健康与 LLM 路由已验证；当前 HEAD 的完整 E2E 未重跑 |
| S4 | DNS / HTTPS | 待办 |
| — | 验证记录落盘 | 部分完成：已有分项记录；待补本次版本对齐汇总 |

## 6. 收敛目标

> 一个 Git SHA → 一个镜像 → 一套确定配置 → 一个 migration head → 一份验证记录。

当前尚未完全收敛：服务器 `20260818-01` 已稳定运行，但本地 `8163000` 尚未部署，且服务器 release 仍需补充可追溯 SHA。下一步只做已验证提交的 release 固化、完整业务 E2E/P1–P5 smoke 复核，以及按需处理 DNS/HTTPS。
