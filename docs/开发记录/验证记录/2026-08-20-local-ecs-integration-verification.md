# 2026-08-20 本地基线与临时 ECS 集成验证

## 身份

| 对象 | 值 |
|---|---|
| Commit A / application | `cea1eaa0aa5b5ba04ccd5de756baa866a306bea7` |
| Commit B / PDF corpus | `a6b03b33557aad73e06531237aa83a05bdaa2e32` |
| V1 verification commit | 本文档及同目录 manifest |
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

### Targeted diagnostic

此前已观察到 provider alias/base URL host 配置不一致；修复仅作用于当前 PowerShell 进程，未修改 `.env`。

一次 targeted diagnostic 捕获到真实 provider request、raw response 和 parser 结果：

```text
diagnostic_classification = EVALUATOR_PROVIDER_VARIANCE
environment_remediation_applied = true
mismatch_fields = provider_alias, base_url_host
evaluator_batch_request_sha256 = 4e01c97639248be98f7e25c270597f095729cf0167cb91a6917911342ac2db51
ambiguous_entity_request_sha256 = 7e1e5ee70d903e95ef8e44dcf9b73266aadf1ef08c23ec8113358fd63f87ffb0
batch_case_count = 8
ambiguous_entity_batch_index = 7
provider_raw_response = available
raw_label = entailed
parser_status = entailed
endpoint_host = api.deepseek.com
```

取证材料保留在 ignored 目录：

```text
tmp/quality-gate/diagnostic/20260820-v1-targeted/
```

wrapper 源码与 hook 已清理；request、raw response、parser result、hash 和 environment snapshot 保留。

该 diagnostic 仅用于归因，不构成 Live PASS。

### Pre-Full Local Live

在 wrapper 完全退出后，最终 SHA 的正常环境 Local Live 完成 3 轮、189 次 entailment 检查，请求失败数为 0；
但 provider 结果为：

```text
stability = 0.873
dangerous_false_entailed = 6
selected_threshold = null
release_gate_passed = false
```

`ambiguous-entity` 三轮均为 `entailed`、置信度 `1.0`。结果文件：
`tmp/quality-gate/v1-final-live/live/entailment-calibration.json`。

该最终 Live 失败分类为 `application-runtime`，不调整阈值或 Trust 配置。

状态锁定为：

```text
Pre-Full Local Live: FAILED
Full/Fast: NOT_EXECUTED
Full/Live: NOT_EXECUTED
Full: NOT_EXECUTED
Canonical quality baseline: NOT_PASSED
```

Full 未启动，因此不将 Full 记为 FAILED。

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
本次 V1 已形成 evaluator diagnosis 与 Local canonical 事实；ECS 仍属于后续 V2，
不把 ECS 节点或 ECS 结果升级为项目质量基线。
