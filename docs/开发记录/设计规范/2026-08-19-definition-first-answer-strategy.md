# Definition-first 答案策略 v1 设计与评审

日期：2026-08-19
状态：设计评审通过，尚未实施
范围：ChainOfRAG 最终答案的概念解释排序
当前 release：`20260818-01`

## 1. 问题边界

当前 `ChainOfRAG` 已经把真实 PDF 第 3 页的定义片段召回，并且该片段已经进入最终证据范围。问题不在于检索分数、P0-3 候选扩展、support filter 的证据准入或 Trust 判定，而在于最终答案阶段把“已验证的中间答案”当成了可直接复用的叙事。中间答案偏向“解析 → 切分 → 向量化 → Milvus → LLM”时，最终模型会沿着流程先答；Trust 只判断声明是否有依据，不判断答案是否先回答了用户真正问的概念。

现有关键链路为：

```text
retrieve
  -> support_filter
  -> _build_trusted_context(answer + citations)
  -> FINAL_ANSWER_PROMPT(documents + intermediate_context + query)
  -> TraceCollector.finalize_answer / Trust
```

因此，本策略只改变最终答案的组织契约和必要的安全兜底，不改变检索候选、检索分数、支持证据准入、Trust 阈值或 Entailment 开关。

## 2. 对照 ragent 的可复用结论

对照 `D:\code\reference\ragent`：

- `RAGPromptService` 将 system prompt、KB evidence 和用户问题作为独立部分组装；
- `DefaultContextFormatter` 将 KB 片段去重、按文档组织后放入 `<documents>`；
- 内置 `KB_ANSWER` 明确规定“结论先行”，并规定“概念解释：先给定义或核心解释，再补特点与适用范围”。

这些规则说明最终上下文需要有职责边界，但它们本身仍是模型提示，不能作为确定性排序保证。本项目采用同样的“证据与问题分区”思路，并额外增加可审计的策略版本和输出后置门。

## 3. 策略契约

策略标识固定为：`definition-first.v1`。

### 3.1 触发范围

只对请求级问题分类为 `concept_definition` 的查询触发。第一版只覆盖定义型问法的确定性词法集合：

- 中文：`什么是`、`是什么`、`指什么`、`是何`；
- 英文：`what is`、`what's`、`define`、`definition of`。

分类器只读取用户原始问题，不读取文档内容；使用 NFKC、大小写折叠和空白归一化。若同一问题同时要求定义和操作说明，仍先进入定义段，再允许追加资料中直接支持的操作内容。若没有明确的定义信号，继续使用现有默认答案策略。

### 3.2 主要回答顺序

当分类为 `concept_definition` 且存在直接定义证据时，最终答案必须满足：

1. 第一条实质性句子直接回答“它是什么”，不得先描述处理流程、实现步骤或系统架构；
2. 第一条实质性句子必须引用主要定义证据；
3. 后续句子才可以补充资料中直接支持的特点、用途或范围；
4. 中间答案只作为辅助线索，不能替代主要定义证据，也不能改变第一条实质性句子的职责；
5. 资料没有直接定义时，不得根据标题、流程或常识补定义，回退现有默认策略并在 Trace 中记录原因。

“第一条实质性句子”忽略空行、Markdown 标题和只含引用标记的行，但不忽略任何事实句。

### 3.3 直接定义证据

直接定义证据只能来自现有最终结果集，不新增检索或证据准入。第一版定义证据候选必须同时满足：

- 文本包含由用户问题归一化得到的主题锚点；
- 同一短句或相邻句中存在已版本化的定义关系：中文 `是`、`指`、`属于`、`是一种`、`是一个`，或英文 `is`、`refers to`、`is a`；
- 候选仍位于当前最终答案证据快照范围内。

候选按原始检索顺序稳定保留，最多取前 2 个；不修改向量分数、BM25 分数、RRF 分数、support filter 结果或 `final_results` 的语义。此规则属于 `definition-first.v1` 的可测试证据角色分类器，不作为未版本化的临时前缀/正则补丁。

## 4. 执行流程

```text
现有 retrieve/support_filter
        |
        v
AnswerStrategy.plan(query, final evidence snapshot)
        |
        +-- default.v1 ----------------------+
        |                                    |
        +-- definition-first.v1              |
             primary definition refs         |
             supporting evidence refs        |
             intermediate role=secondary     |
        |                                    |
        +------------------------------------v
  结构化 final prompt
        |
        v
  answer-order gate
        |
        +-- pass -> 原答案进入既有 Trust
        |
        +-- fail + 可提取原句 -> 仅以主要定义证据原句安全兜底
        |
        +-- fail + 无可提取原句 -> 保留原答案，由既有 Trust 决定
```

### 4.1 结构化最终上下文

最终 prompt 不再把中间答案和原始证据拼成同一优先级的自由文本，而是使用明确职责区：

```text
<AnswerStrategy version="definition-first.v1" query_class="concept_definition">
  <PrimaryDefinitionEvidence refs="E2" />
  <SupportingEvidence refs="E1,E3" />
  <IntermediateEvidence role="secondary_context">...</IntermediateEvidence>
  <Question>DeepSearcher 是什么</Question>
</AnswerStrategy>
```

实际实现中证据正文仍只保留一份，并沿用现有 `Evidence` 编号；上述 refs 是组织信息，不创建第二份证据，也不改变 Trust 的证据快照。主要证据缺失时，策略版本和回退原因必须显式记录。

### 4.2 输出后置顺序门

仅靠 prompt 已经验证不稳定，因此 `definition-first.v1` 需要一个生成后门：

- 检查第一条实质性句子是否带主要定义证据引用，并能在对应证据中找到定义关系；
- 通过则原样交给现有 `TraceCollector.finalize_answer`；
- 不通过但主要证据中存在可定位的定义句时，生成安全兜底答案：只返回该定义句并附正确 Evidence 标记，不保留未通过顺序门的流程型前导；
- 没有可定位定义句时，不做字符串前缀拼接、不重写证据、不修改 Trust 输入，沿用现有答案和 Trust 策略。

这不是新增 Trust 判定。顺序门只解决“先回答什么”，事实是否有依据仍由现有 grounding、consistency、risk 和 Trust policy 处理。

## 5. Trace 契约

新增请求级 `answer_strategy` 对象。因为 Trace 顶层结构增加字段，实施时将 Trace schema 从 v7 升为 v8，保留现有字段和事件语义：

```json
{
  "version": 8,
  "answer_strategy": {
    "version": "definition-first.v1",
    "query_class": "concept_definition",
    "decision": "definition_first",
    "primary_evidence_ids": ["E2"],
    "supporting_evidence_ids": ["E1", "E3"],
    "intermediate_context_role": "secondary_context",
    "fallback_used": false,
    "fallback_reason": null
  }
}
```

若未触发定义策略，`version` 为 `default.v1`；若触发但没有直接定义证据，`decision` 为 `fallback_default`，`fallback_reason` 为 `definition_evidence_not_found` 或 `definition_evidence_outside_snapshot`。同时记录两个 selection event：`answer_strategy.query_classification` 和 `answer_strategy.context_partition`。Trace 不保存隐藏推理，只保存分类、证据编号、角色和回退原因。

## 6. Gold Case 与回归契约

新增独立数据集 `evaluation/datasets/answer_order_v1.json`，而不是修改现有 retrieval/entailment Gold。固定四个同义查询：

1. `DeepSearcher是什么`
2. `DeepSearcher 是什么`
3. `deepsearcher是什么`
4. `什么是DeepSearcher`

每个 case 固定以下证据角色：

- 标题片段：`DeepSearcher`；
- 主要定义片段：`DeepSearcher 是一个基于 RAG 的文档问答项目，解决的是通用大模型不了解用户私有文档、回答缺少可核验依据的问题。`；
- 流程片段：`文档清洗后会切分成片段并转为向量写入 Milvus。`。

自动化断言分三层：

### 6.1 策略断言

- 四个查询均分类为 `concept_definition`；
- `answer_strategy.version == definition-first.v1`；
- 主要证据包含定义片段，流程片段只能属于 supporting evidence；
- `intermediate_context_role == secondary_context`；
- 证据原始顺序和分数快照不变。

### 6.2 答案断言

- 四个答案第一条实质性句子均包含定义锚点 `DeepSearcher 是一个基于 RAG 的文档问答项目`；
- 该句带有效 `[E#]`，且引用 span 命中主要定义片段；
- 流程锚点若存在，只能出现在定义句之后；
- 不接受“先流程、后定义”、无引用定义或拒答作为通过结果。

### 6.3 Trust/回归边界断言

- Trust threshold、Checker `enabled`、检索分数和 P0-3 去重行为不变；
- 有定义证据时不得因为策略组织而降为拒答；
- 无定义证据的负例继续走既有安全路径，不允许由顺序门补写定义；
- 真实四问 trace 中均能定位到策略版本、主要证据和最终 Trust 状态。

本地验证顺序为：策略单测 → ChainOfRAG 定向测试 → Trust/Trace 定向测试 → 真实四问回归。全部通过后才评估服务器热补丁；服务器阶段只部署已验证提交，不在服务器上现场修改业务代码。

## 7. 评审结论

### 保留

- 使用 ragent 的“证据区 / 问题区”分离和“结论先行”原则；
- 以请求级、版本化策略表达定义型答案契约；
- 主要证据与辅助中间答案分角色组织；
- 输出后置门只在必要时使用证据原句兜底，保证当前 Gold 的第一句确定性。

### 明确不做

- 不再只增加一句“概念问题先给定义”的 prompt；
- 不移除 ChainOfRAG 中间答案；
- 不修改 support filter、evidence admission、Trust threshold 或 Entailment 开关；
- 不按定义关键词修改 retrieval score 或重新建立一套检索排序；
- 不用未版本化的正则、字符串前缀或服务器临时补丁替代策略契约；
- 不在没有定义证据时根据标题、流程或外部常识生成定义。

结论：`definition-first.v1` 具备清晰触发条件、证据边界、输出后置保护、Trace 审计字段和 Gold 验收口径，可以进入本地实施；实施前不需要再调整 Trust 或检索参数。
