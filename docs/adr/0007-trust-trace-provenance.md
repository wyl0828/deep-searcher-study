# ADR 0007：Trust Trace Provenance

## 状态

Accepted，首版实现于 2026-08-11；Evidence 身份 v2.0、请求时间身份 v2.1、文档时间身份与
Freshness Classifier 身份 v2.2、文档系列身份 v2.3
同日完成。

## 问题

Claim、Citation、Checker 和 Answer Policy 已能说明“系统判了什么”，但此前不能稳定回答：这次
结论由哪一版运行时、模型、Embedding、索引、Prompt、Checker 与 Policy 产生。只有评分而没有
谱系，模型切换、索引重建或规则升级后的质量变化无法复现，也无法形成可靠审计。

## 决策

Trace v6 的 `trust.provenance` 当前使用独立 v2 契约，并继续读取 v1 记录。v2 绑定：

- Tenant 的不可逆指纹、运行时版本、Binding revision、去密配置摘要和模型策略；
- 生成模型 provider/model/version 与身份指纹；
- Embedding provider/model/version/dimension/normalization 与既有 Embedding 指纹；
- 显式选择知识库时的 Collection Manifest、Data、Document、Embedding 和 Chunk 配置指纹；
- Grounding Prompt 指纹，以及 Entailment Prompt、Claim Extractor、Citation Validator、Citation
  Span、Consistency、Entailment、Risk 和 Answer Policy 版本；
- 模型最终看到的 Evidence 顺序、内容指纹、来源指纹、定位指纹、来源类型、Web Provider 和
  trusted 状态，以及去密的文档业务时间指纹、`publication_anchor_bound`、文档系列指纹与
  `version_family_bound` 状态；
- 相对时间 Checker 实际使用的本地参考日期、IANA 时区与身份指纹；精确请求时刻保留在 Trust
  Trace 的 `temporal_context`，不直接进入谱系字段；
- Freshness Classifier 契约与版本；时效 mode 保留在 Trust Report，不保存原始问题；
- 整份规范化谱系的 SHA-256 摘要。

产品消息单独保存 `provenance_contract_version` 和 `provenance_digest` 以支持审计检索，完整白名单
谱系保存在 `trust_details.provenance`。线上同步 Trace、SSE、库调用和离线 Benchmark 共用同一个
构建器。

## 安全边界

谱系不保存原始问题、对话历史、Evidence 正文、URL、Document ID、完整 Prompt、Collection 名称、原始 Tenant ID、
API Key、Token、Password 或其他 Provider 配置。配置摘要复用既有 secret redaction；密钥轮换不
改变谱系身份，模型或版本变更必须改变摘要。

显式 Collection 只记录 Manifest 指纹并按规范化内容排序，不保存名称。动态路由在选库前明确写为
`dynamic_unbound`，不枚举全部知识库；Agent 实际选库后，请求级 Provenance Session 合并各轮真正
访问的 Collection 并重新生成摘要。`selection_mode` 仍保持 `dynamic`，而
`snapshot_status=complete|partial` 表示实际路由是否已经绑定完整 Manifest。

Collection 名称仅保存在请求生命周期内的 Session 集合中，用于读取 Manifest；Trace 只接收重新
签名后的白名单谱系。DeepSearch 的并发子查询可命中不同 Collection，Session 使用集合合并且对
Manifest 排序，因此完成顺序不会改变最终摘要。

`record_grounding_evidence` 收到的是 `format_grounding_evidence` 真正交给最终回答模型的有界文本，
Provenance Session 当场计算哈希，只保留安全身份，不长期持有正文。Evidence 顺序是 Prompt 语义的一
部分，因此不会排序；内容或顺序任一变化都会改变 Evidence snapshot fingerprint 和总 Digest。无效
Web URL 不会生成虚假来源身份，而会使快照降为 `partial`。

持久化前使用固定字段形状重新规范化并验签。未知嵌套字段会被删除；模型、版本或指纹等有意义字段
被篡改但摘要未同步时，整份谱系被拒绝。Digest 是篡改证据与身份键，不是数字签名；需要跨信任域
防抵赖时，后续应接入服务端签名或不可变审计存储。

## 评测

`evaluation/datasets/trust_provenance_v1.json` 固定二十三项不变量：集合顺序稳定、密钥轮换稳定、
模型变更可检测、动态范围披露、动态路由后绑定、多轮路由合并、Evidence 绑定、Web 信息去泄露、
内容/顺序变化检测、Evidence 篡改拒绝、原始身份不泄露、嵌套注入清除、有意义篡改拒绝，以及
请求时间身份绑定、日期变化检测和时间身份篡改拒绝；文档发布日期与版本系列身份必须分别进入
Evidence 指纹，且任一变化必须改变最终摘要而不暴露原值；Freshness Classifier 契约和版本必须进入 Checker 身份。
`python -m evaluation.provenance` 已进入快速质量门禁，不调用外部模型。

## 已知限制

Web Evidence v2 绑定的是搜索 Provider 返回、且最终进入模型 Prompt 的 snippet，不代表网页全文或
网页未来仍可访问。Digest 是未加密的身份摘要而非服务端签名；完整网页归档和跨信任域防抵赖仍是
后续能力。
