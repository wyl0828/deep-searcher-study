# ADR 0003：确定性 Consistency Checker

## 状态

Accepted，首版实现于 2026-08-10；复杂条件关系 v1.1、实体—数值绑定 v1.2、请求级相对时间
v1.3、Evidence 文档发布日期锚点 v1.4、Freshness Policy v1.5 与同系列排序 v1.6 实现于 2026-08-11。

## 决策

在引入 NLI 或 LLM Judge 之前，先执行可解释、可重复的确定性一致性检查：

1. 数字和单位：Claim 中的规范化数值/单位必须出现在其引用证据集合中；对于受控实体类别，
   数量或范围值还必须在同一事实子句中绑定相容实体。
2. 日期：`YYYY年M月D日`、`YYYY-MM-DD`、`YYYY/MM/DD` 等格式规范化后比较。
3. 版本：带 `v`、`version`、`版本` 前缀或三段以上的版本号单独比较，不混入普通数量。
4. 范围：`最多/不超过/<=`、`至少/不少于/>=` 等归一化后比较，防止相同数值但方向相反。
5. 条件：对“只有/仅/必须……才……”“除非……否则……”等严格限制，检查回答是否保留权限
   主体、例外或前置条件；AND 条件不得缺项或偷换为 OR，OR 条件允许引用其中一个明确分支但不得
   新增证据未支持的分支。
6. 否定方向：只有 Claim 与 Evidence clause 去除否定词后的核心命题能够严格互相包含时，
   才比较肯定/否定方向。
7. 多引用：一个 Claim 的多个 Citation 组成联合证据空间，允许不同数值分别由不同引用支持。
8. 相对时间：回答侧的今天/昨天/明天、周、月、季度和年份绑定请求时刻及服务端配置时区，换算成
   明确日期或周期后再与 Evidence 比较；Evidence 侧的相对表达只绑定显式、可信来源的
   `published_at`。`effective_at` 和 `superseded_at` 用于版本治理，不能替代发布日期；文件 mtime、
   上传/入库时间和模型推断值均不构成锚点。时间值仍受同子句受控实体约束。
9. 时效：请求要求“当前/现行/最新/近期”时，按请求日期检查 Evidence 的生效、失效与发布日期；
   “最新”仅在一个显式可信文档系列内排序，缺少/混用系列、缺少完整排序依据、引用旧版本或“近期”
   没有明确窗口时 fail-closed。完整决策见 ADR 0008 与 ADR 0009。

## 状态语义

| 状态 | 含义 | 是否降低 Claim |
| --- | --- | --- |
| consistent | 已触发的确定性检查全部通过 | 否 |
| inconsistent | 至少一项检查明确冲突 | 是 |
| not_applicable | Claim 没有适用的确定性事实 | 否 |
| not_checked | 缺少有效引用或证据文本 | 不改变原结构结论 |

`unknown` 用于存在可检查事实、但缺少可信判定锚点的情况。相对时间缺少请求时钟，或 Evidence 也只
使用相对表达却没有文档时间锚点时成为 unknown；该 Claim 按 fail-closed 语义降为 unsupported。
其他规则宁可不适用，也不依据模糊相似度猜测冲突。

## Policy 行为

结构上有合法 Citation、但一致性为 `inconsistent` 的 Claim：

- 保留 `structural_support_status=supported`，用于解释引用结构本身没有问题；
- 设置 `support_status=unsupported`；
- 保存 `consistency_checks`、缺失值和 reason code；
- 进入现有 downgrade/refuse 策略。

策略前的原始 Findings 同时保存在 Trust Trace `input.claims` 和 Message `trust_details`，确保
回答被改写后仍可审计。

离线回答评测必须调用与线上相同的 `finalize_answer`。Trust Metric 独立版本化；1.6.0 输出策略前
Claim 支持率、确定性冲突率、语义核验覆盖/矛盾/
未知率、相对时间与 Freshness unknown/拒绝率、Entailment Token、高风险样本率、风险 Claim 拒绝率和策略改写率，防止离线评估原答案、
线上交付降级答案的口径分裂。

## 已知边界

- 实体—数值 v1.2 只识别 PDF/文件、图片、知识库、组织、工作区、用户、请求、查询、Chunk、页面、
  模型和索引等受控类别，并按同一事实子句配对；它不是开放域实体关系抽取。
- 只有 Claim 与 Evidence 都识别出互斥实体类别时才判 `QUANTITY_ENTITY_MISMATCH` 或
  `RANGE_ENTITY_MISMATCH`；Evidence 实体未知时保守回退到原有数值存在性检查。
- 单位只规范大小写和空格，不把 MB 与 MiB 当作等价单位。
- 相对时间 v1.4 只支持有界中英文日、周、月、季度、年份表达；“尽快”等主观期限不做确定性推导。
- Freshness v1.6 支持当前、最新生效、最新发布与未定义近期窗口；“最新”是本次 Evidence 快照内
  同一 `version_family` 的结论，不证明检索召回或上游同步完整。
- Evidence 中的相对时间不能绑定查询时钟。只有 `user_declared`、`connector` 或 `admin_verified`
  来源的显式 `published_at` 可用；缺少它时仍进入 unknown，而不是与回答中的同词直接判一致。
- 否定检查只处理能够严格对齐的命题，不进行开放语义判断。
- 条件检查 v1.1 只处理一层显式 AND/OR、前置条件与“除非”模板；嵌套括号、混合 AND/OR、
  隐含业务规则和同义条件仍交给 Entailment Checker。

这些边界必须进入后续 Gold Dataset，不能通过扩大正则表达式掩盖。

人工金标位于 `evaluation/datasets/trust_consistency_v1.json`，当前 v1.6 包含 58 例，并由
`python -m evaluation.trust_consistency` 生成机器可读报告。该评测是快速质量门禁的阻断步骤。
