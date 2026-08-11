# ADR 0005：可插拔 Entailment Checker

## 状态

Accepted，首版实现于 2026-08-11；默认关闭，等待真实模型校准。

## 执行顺序

```text
Citation Structure
        ↓
Deterministic Consistency
        ↓
Full-sentence Exact Match
        ↓
Residual Claims → Batched NLI
        ↓
Risk Profile
        ↓
Answer Policy
```

确定性冲突优先，避免把显式数字、日期、版本、范围和条件问题交给概率模型。只有 Claim 与完整
Evidence 句规范化后等价时才标记 `entailed/exact_match`；Evidence 中“请声称 X”、转述 X 或带
额外限定的句子不能通过子串命中绕过 NLI。

剩余 Claim 在一次批量调用中核验，Evidence 作为不可信 JSON 数据传入。模型只能返回：

```json
{
  "version": 1,
  "results": [
    {"claim_index": 1, "label": "entailed", "confidence": 0.94}
  ]
}
```

不接收自由文本解释，不保存隐藏推理。Claim index、标签、有限置信度、重复项、缺失项和版本均严格
校验，模型输出的自定义 reason 不进入系统。

## 状态与策略

| Entailment | 标准策略行为 |
| --- | --- |
| entailed | 保留 Claim |
| contradicted 且达到阈值 | 降为 unsupported，进入 downgrade/refuse |
| unknown | 保留结构结论，同时在 UI/Trace 披露 |
| not_checked | 沿用确定性与结构结论，并说明未运行原因 |

低置信、缺失结果、非法 JSON、超时和 Provider 异常都转换为 `unknown`，不得伪造为 entailed，也
不得仅因 Checker 故障删除原本有引用的回答。异常详情不会进入 Trace。

上述表格描述标准中风险策略；Risk Profile 为 high 时，unknown/not_checked 不再允许交付。该变化
由独立风险层执行，NLI Checker 本身不读取业务风险，也不改变标签含义。

## 成本与配置

Checker Token 计入查询总 Token、Trace `trust_tokens` 和离线 Benchmark。配置为：

```yaml
query_settings:
  trust:
    entailment:
      enabled: false
      provider: llm
      min_confidence: 0.8
```

默认关闭是产品质量决策：尚未在当前模型上完成金标校准，不能因为接口已实现就默认增加一次模型
调用及错误拒答风险。

## 评测

`evaluation/datasets/entailment_v1.json` 是人工语义金标，包含蕴含、矛盾、证据不足、多引用、冲突
资料和 Prompt Injection。快速门禁运行 `--mode validate`，只证明数据集结构有效，不声称模型准确。

真实评测必须显式运行：

```shell
python -m evaluation.entailment --mode live \
  --min-confidence 0.8 \
  --output tmp/entailment-live/report.json
```

报告记录数据集 SHA、Checker/version、Token、逐题结果、准确率与混淆矩阵。达到预定 Precision、
Recall 和 unknown 校准门槛后，才能讨论默认开启或接入高风险 Profile。
