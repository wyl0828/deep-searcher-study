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

## 本地质量状态

- Fast Gate：**19/19 通过**。
- Python：**1175 passed, 11 skipped**。
- 前端：40 项通过；E2E 2 项通过；Alembic 空库升级至 `20260817_0023`。
- 新增部署配置检查通过，runtime package 不包含 `output/pdf/**`。
- 输出：`tmp/quality-gate/2026-08-20-final-fast-finalsha/summary.json`。

### Local Live isolated preflight

首次启动命令因本地 provider 候选环境变量别名未注入，在评测开始前退出，分类为
`configuration` preflight；通过当前 PowerShell 进程变量补齐别名后，未修改 `.env` 或输出密钥。

最终 SHA 的实际 Live 使用相同的评估数据和精确 collection `eval_workspace_v2`，完成 3 轮、189 次
entailment 检查，请求失败数为 0；但 provider 结果为：

```text
stability = 0.9365
dangerous_false_entailed = 1
selected_threshold = null
release_gate_passed = false
```

结果文件：`tmp/quality-gate/2026-08-20-final-live-finalsha-rerun/live/entailment-calibration.json`。
该失败分类为 `application-runtime`，具体为 provider/evaluation variance；不调整阈值或 Trust 配置。

状态锁定为：

```text
Local Live: FAILED
Local Full: NOT_EXECUTED
Canonical quality baseline: NOT_PASSED
```

Local Full 未执行，因为其前置 Live 已失败；因此不将 Local Full 记为 FAILED。

## ECS 结果

- Commit A 已上传并解压到 `/opt/deepsearcher-study/releases/20260820-01`。
- 远端 `.env.server` 未覆盖；release 关键文件 SHA 与 Commit A 一致；本地部署包已确认不包含
  `output/pdf/**`。远端没有独立 release/package manifest 文件，因此身份以内容哈希和部署命令记录核对。
- Compose 配置通过；磁盘约 59% 使用、内存充足，PyPI/镜像连通性正常。
- 默认 Compose build 和 `--network host` 的同一 release build 均停在 `uv sync --frozen --no-dev` 的大依赖下载/解包阶段；
  两次构建进程均已终止，未修改 Dockerfile、依赖、Compose、部署脚本或应用代码。
- 旧 release `20260818-01` 的运行容器保持 healthy。
- 新 release 未启动，未执行 ECS Live/Full、definition-first 四问或 P0–P5 smoke。
- 该失败分类为 `infrastructure`，未删除 volume、未执行数据库 downgrade、未影响旧 release。

## Evaluator manifest

完整三层身份与 SHA 记录见同目录的 [`2026-08-20-evaluator-manifest.json`](./2026-08-20-evaluator-manifest.json)。

## 结论

代码、PDF、动态部署配置、Live/Full CLI 契约、evaluator collection ownership 和 manifest 机制已实现。
当前本地 Fast 通过，但 canonical quality baseline 为 `NOT_PASSED`，原因是 Local Full 未执行。
ECS 仅留下临时节点构建兼容性失败记录，不把 ECS 节点或 ECS 结果升级为项目质量基线。
