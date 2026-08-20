# Evaluation of DeepSearcher

## 多文档业务质量基线（推荐）

`datasets/workspace_v2.json` 当前版本为 `2.2.0`，是基于仓库内三份固定 PDF 的 72 题中文金标集，
包含 65 道可回答问题和 7 道无答案问题，其中 8 道要求跨文档证据、12 道是带历史消息的多轮样本。它覆盖
单文档事实、跨文档组合、上下文追问、话题切换、精确术语、方案取舍和诚实边界；
每个来源都固定路径与 SHA-256，每道可回答问题标注文档和页码。

先用相同三份 PDF 准备独立评测 Collection，并比较 Dense/BM25/Hybrid：

```shell
python -m evaluation.retrieval_compare \
  --dataset evaluation/datasets/workspace_v2.json \
  --prepare \
  --collection eval_workspace_v2 \
  --top-k 8 --repetitions 3 \
  --output evaluation/results/workspace-v2-retrieval
```

`--prepare` 只允许精确使用质量评测自有的 `eval_workspace_v2`，每次执行都会先重建该
collection，再导入当前数据集，避免重复运行残留旧分块。`--cleanup` 也只允许清理这个
evaluator-owned collection，禁止模糊匹配或触碰个人知识库。然后执行：

质量门禁的 Live/Full 输出目录还会生成 `evaluator-manifest.json`，记录应用提交、PDF corpus
提交、dataset/config SHA、来源文件 SHA、collection 和执行节点。

```shell
python -m evaluation.benchmark \
  --dataset evaluation/datasets/workspace_v2.json \
  --collection eval_workspace_v2 \
  --agents naive,deep_search,chain_of_rag \
  --mode answer --top-k 8 --max-iter 3 \
  --sample-profile answer_stratified_v1 \
  --llm-timeout-seconds 60 \
  --external-call-timeout-seconds 130 \
  --request-timeout-seconds 600 \
  --output evaluation/results/workspace-v2-answer
```

回答评测会逐题追加 `checkpoint.jsonl`。进程中断后以完全相同的参数和输出目录重跑，会跳过
已成功样本、只重试失败或未完成样本；因观测到 Provider 超时而单纯放宽外部调用或整题超时也可
兼容续跑，变更会记录在 `checkpoint.history.jsonl`。其他运行参数、数据集或配置哈希发生变化时会
拒绝复用旧断点，避免混合实验口径。

2026-08-09 的 `answer_stratified_v1` 正式报告固定抽取 24 题，每个 Agent 各跑 24 行：20 道
可回答、4 道无答案、6 道历史依赖、4 道跨文档，难度为 7 易/8 中/9 难。三类 Agent 均为零错误。
NaiveRAG、DeepSearch、ChainOfRAG 的回答质量分分别为 `0.785600/0.761980/0.774910`，平均
Tokens 为 `2987.54/15139.17/9163.46`，P95 延迟为
`27696.727/223048.318/74820.630ms`。DeepSearch 的拒答准确率为 `0.50`，未达到 `0.75`
准入线；NaiveRAG 与 ChainOfRAG 合格，NaiveRAG 得分更高且成本、延迟最低，因此正式推荐
`naive` 作为路由失败时的默认回答 Agent。完整结果见
`results/workspace-v2-answer-stratified-20260809/report.json`。

### Token 成本治理复测（2026-08-13）

Token 成本治理以同一 `answer_stratified_v1` 24 题、同一 Collection 和三类 Agent 复测；回答阶段
使用当前默认 `max_iter=2`。结果如下：

| Agent | 旧基线平均 Token | 当前平均 Token | 降幅 | 平均/最大 LLM 调用 |
| --- | ---: | ---: | ---: | ---: |
| NaiveRAG | 2987.54 | 2098.46 | 29.8% | 1.21 / 2 |
| DeepSearch | 15139.17 | 4211.04 | 72.2% | 3.92 / 6 |
| ChainOfRAG | 9163.46 | 5239.92 | 42.8% | 4.75 / 7 |

中间结构化调用的 reasoning Token 为 0，三类 Agent 单题调用均未超过 8 次硬上限。ChainOfRAG
`5000` 是预期优化目标，不是质量门禁；当前实测略高 4.8%，但已经获得显著下降。

质量阈值没有因 Token 项目而调整。该轮 retrieval、context、NaiveRAG、DeepSearch 阈值通过，
ChainOfRAG Claim Support 为 `0.625`，低于固定 `0.65`，因此整套 Full Gate 报告为 failed，作为
已知质量观察保留。独立 DeepSeek Entailment live 校准使用 Thinking、受控 `max_tokens=3904`，
准确率 `0.9683`、稳定率 `1.0`、危险假阳性 `0`，通过发布门槛；Entailment 默认仍关闭。

完整复测继续使用质量门禁入口：

```powershell
.\scripts\run-quality-gate.ps1 -Mode Full -OutputDir tmp/quality-gate/full
```

`evaluation/quality_gate.json` 的固定质量 tolerance 不属于 Token 成本目标，不能为了达到成本数字而放宽。

报告使用 `metric_version=2.1.0`；`full_document_coverage_rate` 只以真正需要两份及以上文档的
样本为分母，并同时输出 `multi_document_sample_count`。除原有质量、延迟和成本数据外，还输出全部证据
召回率、声明级引用和上下文改写准确率，以及按标签和难度分组的
指标。多来源文件如果在入库时被重命名，必须使用
`--source-alias 原文件名=实际文件名` 明确映射。

Trust Layer 指标独立使用 `trust_metric_version=1.6.0`，避免改写已有检索基线的指标语义。
新回答报告会记录策略执行前 Claim 支持率、确定性一致性冲突率、Entailment 覆盖/矛盾/未知率、
Entailment Token 和 Answer Policy 改写率；
Provenance 与 Temporal 指标还输出谱系覆盖率、最终 Evidence 快照绑定/完整率、时间身份绑定率、
Web Evidence 快照数量、知识库 Evidence 发布日期锚点与文档系列覆盖率，以及相对时间与 Freshness
unknown/拒绝率；Freshness 另外记录 current/latest-effective/latest-published/recent mode 和触发率；
Benchmark 与线上查询使用同一个 `finalize_answer` 路径。2026-08-09 的存量报告早于 Trust
Metric，不包含这些字段，不能用于证明当前策略效果。

确定性 Checker 另有不依赖模型和向量库的人工标注集
`datasets/trust_consistency_v1.json`，当前 v1.6 共 58 例，覆盖数字/单位、受控实体—数量/范围绑定、
日期、版本、范围方向、AND/OR 条件关系、复合前置条件、“除非”例外、条件省略、否定冲突、
多 Citation、相对时间和已知语义边界。相对时间用固定请求时钟/时区评测，覆盖跨 UTC 日界线、
跨季度年份、实体错配、无请求时钟、无文档时间锚点，以及显式 `published_at` 的匹配与冲突；缺少
可信锚点必须成为 unknown 并触发保守策略，`effective_at` 不能冒充发布日期。
其中 13 例 Freshness 用固定请求日期覆盖当前有效、已失效、尚未生效、缺少有效日期、最新生效、
最新发布、存在同系列更新证据、无系列、跨系列干扰、系列歧义、排序覆盖不完整和“近期”窗口未定义；
“最新”只表示本次完整 Evidence 快照内同一系列的排序结果，不替代 Retrieval Recall 与连接器同步完整性。
实体绑定采用保守回退：只有声明与证据都识别出互斥实体类别时才判冲突，未知实体不强判。
运行：

```shell
python -m evaluation.trust_consistency \
  --output tmp/trust-consistency/report.json
```

快速质量门禁会自动运行该评测；任何 case 的状态、reason code 或 Policy 动作偏离金标都会失败。

Claim 到 Evidence 内部位置另有独立人工金标集 `datasets/citation_span_v1.json`，覆盖规范化精确
命中、句级近似、多 Citation 分布支持、数字冲突、无关证据和明确的语义边界。运行：

```shell
python -m evaluation.citation_span \
  --output tmp/citation-span/report.json
```

该报告同时校验 match type、quote 和半开字符偏移；快速质量门禁会阻断静默误定位。句级近似只
用于导航，不计作语义蕴含。

语义核验使用人工金标集 `datasets/entailment_v1.json`。快速门禁只运行无模型的数据结构验证：

```shell
python -m evaluation.entailment --mode validate \
  --output tmp/entailment-dataset/report.json
```

真实模型准确率必须显式运行 `--mode live`；报告包含混淆矩阵、逐题置信度和 Token。validate 报告
固定将 `semantic_accuracy` 写为 `null`，防止把数据集校验或 Stub 测试误报成 NLI 质量。

Risk Profile 使用独立人工金标 `datasets/risk_profile_v1.json`：

```shell
python -m evaluation.risk_profile \
  --output tmp/risk-profile/report.json
```

评测同时固定 risk level、query type 和最小独立来源数。Trust Metric 1.2 另外输出高风险样本率与
风险 Claim 拒绝率，便于观察严格策略是否因分类漂移突然扩大拒答范围。

Trust Provenance 使用人工定义的安全与可复现性不变量集
`datasets/trust_provenance_v1.json`。它验证集合顺序不影响摘要、密钥轮换不改变身份、模型变更会
改变摘要、动态路由必须先披露未知状态并在选库后绑定实际 Manifest、多轮选库必须稳定合并，
Evidence 内容/顺序和混合 Web 来源必须可验证，相对时间参考日期/时区必须进入摘要，以及嵌套字段
注入和摘要篡改不会进入持久化；文档发布日期与文档系列会分别进入 Evidence 去密指纹且其变化可检测；当前共
23 项不变量，并固定 Freshness Classifier 契约与版本：

```shell
python -m evaluation.provenance \
  --output tmp/trust-provenance/report.json
```

该门禁只验证谱系契约，不读取密钥、原始问题或完整 Prompt，也不调用外部模型。

## 单文档兼容基线

`datasets/milvus_v1.json` 是基于 `examples/data/WhatisMilvus.pdf` 固定的
30 题中文金标集：25 道可回答问题和 5 道无答案问题。每题包含来源页、
参考答案、可接受答案要点和标签，数据集及原始 PDF 都使用 SHA-256 固定版本。

仅评测检索：

```shell
python -m evaluation.benchmark \
  --collection kb_037e65f9612842fb809fd596de82e351 \
  --source-alias doc_d8555f3a34b741d788fe88793d489fce.pdf \
  --agents naive,deep_search,chain_of_rag \
  --mode retrieval --top-k 5 --max-iter 1 \
  --chain-min-evidence-for-stop 2
```

将 `--mode retrieval` 改为 `--mode answer` 可同时评测最终回答。每次运行保存
`report.json` 和 `details.csv`，记录数据集/配置哈希、Git 状态、模型、参数、
质量、延迟、Token、LLM 调用次数及失败类型。

若入库流程将原文件重命名，必须通过 `--source-alias` 显式声明实际来源名；
映射会写入报告，评测不会用“同 Collection 即相关”替代来源与页码校验。
快速验证可用 `--sample-ids milvus-001,milvus-020,milvus-026` 固定抽取
事实、多事实和无答案样本；指定后它优先于 `--limit`。

`grounded_criteria_coverage` 是“召回证据覆盖标准答案要点”的透明代理指标，
不等同于使用 LLM 裁判的忠实度评分。

ChainOfRAG 评测默认启用证据型 early stopping。只有累计达到
`--chain-min-evidence-for-stop` 条不同证据后，模型的停止判断才会生效；
该阈值与是否启用 early stopping 会写入报告。

## Dense、BM25 与 Hybrid/RRF 对比

Milvus 检索策略使用独立命令评测。命令会在 `eval_` 命名空间创建临时混合索引，使用同一份
Chunk 和查询 Embedding 分别执行 Dense、BM25 与 Hybrid/RRF；每种模式重复运行并轮换顺序，
保存整体指标、分标签指标、P50/P95、排序稳定率、索引清单和资源结构。`--cleanup` 会在报告生成
前删除临时 Collection，产品知识库不会被修改。

```shell
python -m evaluation.retrieval_compare \
  --prepare --cleanup \
  --collection eval_o06_milvus_v1 \
  --top-k 5 --repetitions 3 \
  --rrf-k 60 --batch-size 10 \
  --output evaluation/results/2026-08-01-o06-dense-bm25-hybrid-v1
```

报告通过透明门禁决定是否建议提升 Hybrid：Recall@K 和证据覆盖不能明显回退，Recall、MRR、
证据覆盖的等权综合分至少提高 1 个百分点，检索 P95 在容许范围内，至少包含 20 道可回答题且
Hybrid 零错误。阈值均可由命令行覆盖并会写入报告。

2026-08-09 的 `workspace_v2` 2.2.0 三重复报告中，加权 RRF 使用 Dense:BM25=`1.5:1`、
`k=5`、候选倍率 `1`，并保留 Dense 前两名。Dense 的 Recall@8/MRR/证据要点覆盖为
`0.8385/0.7116/0.8066`，Hybrid 为 `0.8744/0.7153/0.8184`；8 道跨文档题的完整文档覆盖率
从 `0.2500` 提升到 `0.3750`，综合质量提高 `0.017133`，P95 从 `403.296ms` 增至
`419.050ms` 且满足门禁。因此当前配置已提升 Hybrid。该结论只适用于这份固定业务集和上述融合
参数，语料、Embedding、分析器或参数变化后必须重新评测。

真实 Embedding 与 Milvus 的串行/受限并发探针：

```shell
python -m evaluation.concurrency_probe \
  --collection kb_037e65f9612842fb809fd596de82e351 \
  --counts 1,2,4,8 --max-concurrency 4 \
  --output evaluation/results/deep-search-concurrency/report.json
```

以下 2Wiki 流程是旧版兼容评测。

## Introduction
DeepSearcher is very good at answering complex queries. In this evaluation introduction, we provide some scripts to evaluate the performance of DeepSearcher vs. naive RAG.

The evaluation is based on the Recall metric:

> Recall@K: The percentage of relevant documents that are retrieved among the top K documents returned by the search engine.

Currently, we support the multi-hop question answering dataset of [2WikiMultiHopQA](https://paperswithcode.com/dataset/2wikimultihopqa). More dataset will be added in the future.

## Evaluation Script
The main evaluation script is `evaluate.py`. 

Your can provide a config file, say `eval_config.yaml`, to specify the LLM, embedding model, and other provider and parameters.
```shell
python evaluate.py \
--dataset 2wikimultihopqa \
--config_yaml ./eval_config.yaml \
--pre_num 5 \
--output_dir ./eval_output
```
`pre_num` is the number of samples to evaluate, the more samples, the more accurate the results will be, but it will consume more time and your LLM api token usage.

After you have loaded the dataset into vectorDB in the first run, if you want to skip loading dataset again, you can set the flag `--skip_load` in the command line.

For more arguments details, you can run
```shell
python evaluate.py --help
```

## Evaluation Results  
We conducted tests using the commonly used 2WikiMultiHopQA dataset. (Due to the high consumption of API tokens for testing, we only tested the first 50 samples. This may introduce some fluctuations compared to testing the entire dataset, but it can still roughly reflect the general landscape of performance.)

### Recall Comparison between Naive RAG and DeepSearcher with Different Models
With Max Iterations on the horizontal axis and Recall on the vertical axis, the following chart compares the recall rates of Deep Searcher and naive RAG.
![](plot_results/max_iter_vs_recall.png)
#### Performance Improvement with Iterations
As we can see, as the number of Max Iterations increases, the recall performance of Deep Searcher improves significantly. And all the model results from Deep Searcher are significantly higher than those from naive RAG.

#### Diminishing Returns
However, it is also evident that as the number of iterations gradually increases, the marginal gains decrease, indicating that there may be a certain limit reached after increasing the feedback iterations, and further feedback might not yield significantly better results.

#### Model Performance Comparison
Claude-3-7-sonnet (red line) demonstrates superior performance throughout, achieving nearly perfect recall at 7 iterations. Most models show significant improvement as iterations increase, with the steepest gains occurring between 2-4 iterations. Models like o1-mini (yellow) and deepseek-r1 (green) exhibit strong performance at higher iteration counts. Since our sample number for testing is limited, the results of each test may vary somewhat. 
Overall, reasoning models generally perform better than non-reasoning models. 

#### Limitations of Non-Reasoning Models
Additionally, in our tests, weaker and smaller non-reasoning models sometimes failed to complete the entire agent query pipeline, due to their inadequate instruction-following capabilities.

### Token Consumption
We plotted the graph below with the number of iterations on the horizontal axis and the average token consumption per sample on the vertical axis:  
![](plot_results/max_iter_vs_avg_token_usage.png)  
It is evident that as the number of iterations increases, the token consumption of Deep Searcher rises linearly. Based on this approximate token consumption, you can check the pricing on your model provider's website to estimate the cost of running evaluations with different iteration settings.
