# DeepSearcher 健康契约补充收口验证记录

日期：2026-08-02  
范围：O-01 补充审计；深度探针结果、核心到工作台健康契约、状态脚本与 Worker 进程识别。

## 1. 验收结论

O-01 在本地产品运行范围内完成补充收口。健康状态不再仅由“HTTP 可访问”“调用没有抛异常”
或“PID 仍存在”推导；核心、工作台、启动脚本和 Worker 现在使用一致且可验证的
`alive / ready / degraded / not_ready` 语义。

## 2. 本轮修复

- LLM 深度探针必须得到非空文本；Embedding 必须得到非空、全部为有限数值且与声明维度一致
  的向量。空内容、空向量、NaN/Infinity 或维度错误返回
  `not_ready/DEPENDENCY_INVALID_RESPONSE/retryable=true`。
- 工作台 BFF 校验核心健康响应的 `version`、`service`、`mode`、HTTP 状态与顶层状态一致性、
  请求 ID、四个必需组件、组件状态、错误码格式和 `retryable` 类型。契约错配不会被当成 ready，
  而是返回 `BACKEND_INVALID_HEALTH_RESPONSE`。
- PowerShell 新增 JSON 健康结果解析；`status.ps1` 只把精确 `ready` 当作就绪。工作台
  `200/degraded` 会保持 degraded 并使脚本退出 1，不能再被普通 2xx 检查吞掉。
- Worker 状态同时要求进程与数据库心跳均健康。进程校验使用 PID、虚拟环境 Python 绝对路径、
  项目根目录、模块名和启动时间元数据，不再调用可能无界阻塞的 WMI/CIM。
- 进程启动时间新增 UTC ticks，避免 PowerShell 7 将 ISO UTC 字符串转成无时区 DateTime 后产生
  8 小时偏移；旧字符串元数据与旧纯 PID 文件仍可安全迁移。
- PowerShell Worker 启停测试改用文件捕获，默认 pytest quiet 模式不再卡住或遗留测试 Worker。

## 3. 自动化结果

```text
健康/API/PowerShell 定向回归
71 passed

Python 全量回归（默认 quiet 模式）
725 passed, 10 skipped, 1 warning

uv run ruff check .
All checks passed

uv lock --check
passed

git diff --check
passed
```

O-01 验收时观察到的既有 Crawl4AI mock 协程告警已在后续同日清理，并将 warning-as-error
固化为默认 pytest 门禁；详见
`2026-08-02-deepsearcher-warning-free-test-gate-verification.md`。

## 4. 真实故障与恢复

正常深度诊断：

```text
HTTP                    200
status                  ready
request_id              o01.deep.20260802
runtime / Milvus        ready / ready
LLM / Embedding         ready / ready
document worker         ready
```

停止文档 Worker：

```text
工作台 HTTP             200
工作台状态              degraded
Worker 状态/错误码      not_ready / INGEST_WORKER_UNAVAILABLE
status.ps1 退出码       1
```

继续停止核心 API、保留工作台：

```text
工作台 HTTP             503
工作台状态              not_ready
FastAPI 状态/错误码     not_ready / BACKEND_OFFLINE
Milvus 状态             unknown（不伪报 Milvus 故障）
status.ps1 退出码       1
```

完整重启后：

```text
Docker / Milvus         ready / ready
核心 API / 工作台       ready / ready
文档 Worker             ready（进程 + heartbeat）
深度诊断                runtime、Milvus、LLM、Embedding、Worker 全部 ready
请求 ID                 o01.recovered.20260802
Worker UTC ticks        已写入并通过进程校验
```

## 5. 保留边界

- readiness 验证默认租户运行时和必需向量库；LLM/Embedding 真实调用只在受控深度诊断中执行，
  默认缓存 300 秒，避免普通状态刷新产生模型费用。
- Worker 心跳默认允许 15 秒新鲜度窗口，因此进程非正常退出后最多需要该窗口才能仅凭心跳判定过期；
  本地状态脚本还会同时检查受控进程元数据，可立即识别进程消失。
- 多节点生产环境仍应由编排平台分别配置 liveness、readiness 和启动探针，并使用集中式 Worker
  指标；本记录证明的是当前单机工作台闭环。
