# RoutingLLM candidate-level 凭据与三级故障切换验证记录

## 基本信息

- 日期：2026-08-19
- 本地提交：`285985a`（部署配置与环境变量接线）
- RoutingLLM 实现提交：`2e9832e`
- 服务器 release：`20260818-01`
- 验证范围：DeepSeek 官方、OpenCode Go、阿里云百炼三级 LLM 路由
- 安全边界：未记录或输出任何 API Key；未修改数据库；未执行镜像构建

## 配置核验

容器内实际解析到的候选为：

1. `DeepSeek / deepseek-v4-flash` → `https://api.deepseek.com`
2. `DeepSeek / deepseek-v4-flash` → `https://opencode.ai/zen/go/v1/`
3. `Aliyun / qwen3.7-plus` → 百炼兼容模式 endpoint

`core-api`、Product API、Consumer 均为 healthy，runtime、Milvus readiness 通过。

## 故障切换结果

使用容器内临时路由实例注入本机无服务 endpoint，不改线上配置：

| 场景 | 实际模型 | 结果 |
| --- | --- | --- |
| 官方失败 | `deepseek-v4-flash`（OpenCode Go） | 通过 |
| 官方与 OpenCode Go 失败 | `qwen3.7-plus`（百炼） | 通过 |
| 恢复后正常请求 | `deepseek-v4-flash`（官方） | 通过 |

## 知识库问答结果

使用 collection `kb_b43772028bcc410282bea784c085479a`，manifest 与 runtime embedding profile compatible。

以下问题均返回 HTTP 200，并生成带 `[E1]` 引用的回答：

- `DeepSearcher是什么`
- `DeepSearcher 是什么`
- `deepsearcher是什么`
- `什么是DeepSearcher`

当前质量问题：答案稳定回答了“资料解析 → 切分 → 向量化 → Milvus → LLM”的流程，但没有优先回答“DeepSearcher 是一个基于 RAG 的文档问答项目”。

Trace 定位：第 3 页 definition 已进入检索证据，但 support filter 最终只选中了流程句子；最终 Trust 状态为 `fully_grounded`，但输入阶段的 definition claim 因条件信息缺失被拒绝。

## 结论与后续

- 三级 LLM 凭据绑定、Provider 容灾与恢复已验证通过。
- 当前剩余问题属于检索证据选择/答案排序，不属于 Provider 路由。
- 下一批只针对 definition evidence selection 做 P0 回归：先固定 Gold case 与 trace 断言，再评估 support filter 或候选排序修复；不先调整 Trust threshold、Prompt 或 evidence admission。

## P0 后续实验结论

2026-08-19 对定义类答案做了两种临时实验，均未作为产品改动保留：

- 仅在中间答案和最终答案提示中增加“概念问题先给定义”：真实结果仍可能优先输出流程，不能形成确定性收益。
- 定义类问题最终答案移除 ChainOfRAG 中间答案：一次请求出现“证据不足”，另一次请求又恢复为流程答案，说明会破坏现有 Trust 输入/输出稳定性。

两种实验均已回滚，服务器恢复到本地 Git 基线。当前结论是：definition 已进入召回结果和 support evidence，剩余问题是最终答案组织契约，不应继续用临时正则、字符串前缀或未版本化 Prompt 叠加修补。下一步应单独定义并版本化“概念解释先给定义”的完整答案策略，再配套确定性 Gold/Trust 回归后实施。
