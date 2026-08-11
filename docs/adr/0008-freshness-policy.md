# ADR 0008：Freshness Policy

## 状态

Accepted，首版实现于 2026-08-11。

## 问题

文档已经具备 `published_at/effective_at/superseded_at`，但仅保存日期不能回答“这是不是当前有效版本”
或“引用的是否为本次证据中最新版本”。如果仍把带有效 Citation 的旧制度直接交付，Trust Layer 只
证明“这句话曾经写在某份文档里”，不能证明它在查询日期仍然成立。

## 决策

请求入口使用确定性 Freshness Classifier 解析原始问题，只保留不含原文的安全 Profile：

| mode | 触发语义 | 排序/有效性依据 |
| --- | --- | --- |
| `current` | 当前、现行、目前有效、正在执行 | `effective_at <= reference_date < superseded_at` |
| `latest_effective` | 最新政策、最新制度、最新有效版本 | 当前有效候选中的最大 `effective_at` |
| `latest_published` | 最新发布、最近公布、最新公告 | 未失效且不晚于查询日期的最大 `published_at` |
| `recent` | 近期、近 N 天/月但尚未定义窗口 | `unknown`，请求用户给出窗口 |
| `none` | 普通事实问题 | 不执行 Freshness Check |

缺少 `superseded_at` 表示尚未声明失效；缺少 `effective_at` 不能证明文档当前有效。对于“最新”问题，
被引用 Evidence 必须且只能解析出一个可信 `version_family`，并且只在最终模型看到的同系列知识库
Evidence 中排序；同系列任一候选缺少日期时统一 `unknown`，避免把“能排序的子集里最大”冒充完整结论。
其他系列不参与比较，避免无关新文档导致误拒绝。Web Evidence 当前没有同等业务日期契约，不能单独证明企业
制度的当前或最新状态。

Freshness Check 作为 `consistency_checks.kind=freshness` 进入已有状态机：

- `consistent`：当前有效或在本次完整 Evidence 快照中确认为最新；
- `inconsistent`：引用尚未生效、已经失效，或本次快照存在更新的有效/发布版本；
- `unknown`：缺少请求时钟、有效性/排序日期，或“近期”没有明确窗口。

`inconsistent` 和 `unknown` 都把 Claim 降为 unsupported，并进入既有 downgrade/refuse 策略。原始
Claim、mode、查询参考日期、比较结果和 reason code 保留在 Trust Trace；原始问题不进入 Profile、
Provenance 或持久化审计摘要。

Grounding Prompt 会把经过白名单验证的业务日期作为 Evidence 标签属性提供给模型，同时明确禁止
根据上传时间或缺失字段推断日期。Evidence 正文快照仍保持原文，日期由独立 Provenance 时间指纹
绑定。

## 可观测性与评测

Trust Metric 1.6 输出：

- `freshness_required_rate`；
- `freshness_unknown_rate`；
- `freshness_rejection_rate`；
- 每个样本的 `freshness_mode`。
- 知识库 Evidence 的 `version_family` 绑定数量、分母与覆盖率。

Trust Consistency Gold v1.6 共 58 例，其中 13 例覆盖当前有效、已失效、尚未生效、缺少有效日期、
最新生效、最新发布、存在同系列更新证据、系列缺失、跨系列干扰、系列歧义、排序覆盖不完整和近期
窗口未定义。Provenance 23 项不变量额外固定 Freshness Classifier 契约与版本，并证明系列身份变化
会改变 Evidence 摘要但不泄露系列原值。

## 已知边界

- “最新”只表示最终 Evidence 快照内可验证的最新版本，不证明检索召回了知识库中所有相关文档；
  Retrieval Recall 与数据同步完整性仍需独立门禁。
- `version_family` 由人或连接器显式声明，不依据文件名、标题相似度或模型猜测。错误归组仍可能造成
  错误排序，后续 Knowledge Health 应检测系列内冲突、断代和异常聚类。
- “近期”需要产品定义或用户给出明确窗口；首版不擅自把它解释成 7 天、30 天或一个自然月。
- Freshness Policy 不替代语义冲突检测。两份同时有效但内容不同的文档仍需 Conflict Detection/NLI。
