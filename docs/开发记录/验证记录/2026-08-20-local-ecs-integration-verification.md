# 2026-08-20 本地基线与临时 ECS 集成验证

## 身份

| 对象 | 值 |
|---|---|
| Commit A / application | `cea1eaa0aa5b5ba04ccd5de756baa866a306bea7` |
| Commit B / PDF corpus | `a6b03b33557aad73e06531237aa83a05bdaa2e32` |
| Commit C | 本文档及同目录 manifest |
| ECS execution node | `118.178.234.18` |
| ECS role | `temporary ECS integration node` |
| release | `20260820-01` |
| evaluator collection | `eval_workspace_v2` |

## 本地 canonical 结果

- Fast Gate：**19/19 通过**。
- Python：**1175 passed, 11 skipped**。
- 前端：40 项通过；E2E 2 项通过；Alembic 空库升级至 `20260817_0023`。
- 新增部署配置检查通过，runtime package 不包含 `output/pdf/**`。
- 输出：`tmp/quality-gate/2026-08-20-final-fast-finalsha/summary.json`。

### Live 尝试

Live 尝试使用了相同的评估数据和 `eval_workspace_v2`，实际完成 189 次 entailment 检查，
请求失败数为 0；但 provider 结果为：

```text
stability = 0.9365
dangerous_false_entailed = 1
selected_threshold = null
release_gate_passed = false
```

该尝试发生在 Commit A/B 最终重建前，manifest 身份已标记为 superseded，
没有把它重新标记为最终 SHA。按照“不自动重试昂贵失败项”的约束，最终 SHA 的 Live/Full 未重复执行。
该失败分类为 `application-runtime`，具体为 provider/evaluation variance，不调整阈值或 Trust 配置。

本地启动初次还发现 `.env` 有 dotenv 格式警告及候选环境变量别名问题；通过当前进程变量别名启动服务，
未修改或输出任何密钥，也未写回 `.env`。

## ECS 结果

- Commit A 已上传并解压到 `/opt/deepsearcher-study/releases/20260820-01`。
- 远端 `.env.server` 未覆盖，旧 release `20260818-01` 的运行容器保持 healthy。
- 新 release 构建停在 `uv sync --frozen --no-dev`，超过有限等待窗口无进展；本次构建进程已终止。
- 新 release 未启动，未执行 ECS Live/Full、definition-first 四问或 P0–P5 smoke。
- 该失败分类为 `infrastructure`，未删除 volume、未执行数据库 downgrade、未影响旧 release。

## Evaluator manifest

完整三层身份与 SHA 记录见同目录的 [`2026-08-20-evaluator-manifest.json`](./2026-08-20-evaluator-manifest.json)。

## 结论

代码、PDF、动态部署配置、Live/Full CLI 契约、evaluator collection ownership 和 manifest 机制已实现。
当前唯一 canonical 通过结果是本地 Fast Gate；Local Full、四问和 ECS smoke 仍待后续明确授权后的单独执行，
不把 ECS 节点或本次失败结果升级为项目质量基线。
