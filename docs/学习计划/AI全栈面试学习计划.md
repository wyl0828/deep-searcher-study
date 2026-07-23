# DeepSearcher AI 全栈面试学习计划

> 适用对象：会 Python 基础、准备 AI 全栈应用工程师面试的人。
> 学习周期：4 周，每周 6 天；第 1～5 天约 2 小时，第 6 天约 3 小时，第 7 天休息或补漏。
> 最终目标：能独立运行和演示项目，并分别用 1 分钟、3 分钟、10 分钟讲清架构、实现、取舍、验证与不足。

## 0. 使用方法与事实基线

这不是一份“把名词背熟”的课表，而是一条从源码证据出发的面试训练路径。每天遵循同一闭环：**读源码 → 画数据流 → 做实验 → 留证据 → 口述回答 → 不看答案自测**。遇到不会的问题，先定位代码和测试，再补理论；不要把计划中的优化说成项目已经实现。

路径约定：本文写完整路径时均从仓库根目录起算；在同一小节中为减少重复，`agent/...`、`loader/...`、`embedding/...`、`vector_db/...` 默认相对于 `deepsearcher/`，`App.jsx`、`api.js`、`TracePanel.jsx` 默认相对于 `frontend/src/`。所有测试路径均从仓库根目录起算。

### 当前仓库的事实基线

- Python 要求为 3.10 及以上，后端使用 FastAPI；前端使用 React 19、Vite 6、Vitest。
- 当前 `deepsearcher/config.yaml` 通过 OpenAI-compatible 客户端调用千问：LLM 为 `qwen-plus`，Embedding 为 `text-embedding-v4`，维度为 1024。
- 向量库配置为独立 Milvus 服务：`http://127.0.0.1:19530`。本机基线采用 Docker Milvus，不是 Milvus Lite。
- 默认查询由 `RAGRouter` 在 `DeepSearch` 与 `ChainOfRAG` 之间选择；`NaiveRAG` 保留为直接基线，但不在默认路由列表中。
- `ChainOfRAG.early_stopping` 默认是 `False`；因此默认会运行到 `max_iter`，Trace 中的反思字段通常为空，而不是“反思失败”。
- Milvus dense 检索默认指标是 L2，`RetrievalResult.score` 实际承接 Milvus 返回的 `distance`；界面不应无条件把它解释为“越大越相似”。
- Trace 是程序显式记录的路由、子查询、集合、文档摘要、支持性筛选和 Token 数据，不是模型隐藏思维链。
- 当前 Trace 主要接入 `ChainOfRAG`；如果路由到 `DeepSearch`，可能只有 Agent 与汇总信息，没有逐轮明细。
- 当前系统没有正式的离线 RAG 评测集、鉴权、限流、多租户隔离、缓存或 SSE 流式 Trace。全量测试也存在需要单独核对的基线失败，面试时只能报告最近一次真实测试结果。

### 可复制的启动与验证命令（PowerShell）

```powershell
# 1. 启动 Milvus
docker compose -f infra/milvus/docker-compose.yml up -d

# 2. 启动后端（当前机器使用 8500）
uv run --frozen --env-file .env uvicorn main:app --host 127.0.0.1 --port 8500

# 3. 构建并启动前端代理
Set-Location frontend
npm run build
Set-Location ..
uv run --frozen uvicorn frontend.server:app --host 127.0.0.1 --port 8600

# 4. 验证接口和页面
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8500/openapi.json
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8600

# 5. 运行本学习计划涉及的定向测试
uv run --frozen pytest tests/test_trace.py tests/test_query_api.py tests/agent frontend/tests -q
Set-Location frontend
npm test
Set-Location ..

# 6. 构建中文文档
uv run --frozen mkdocs build
```

不要把 `.env`、API Key 或真实生产文档加入 Git。演示前先确认 `git status --short`、服务端口、Milvus Collection 和模型额度。

## 1. 项目知识地图

### 1.1 三条真实数据链路

```text
离线入库
PDF → PDFLoader → RecursiveCharacterTextSplitter → Chunk/wider_text
    → OpenAIEmbedding(text-embedding-v4, 1024) → Milvus Collection

在线查询
问题 → online_query.query/query_with_trace → RAGRouter
    → DeepSearch 或 ChainOfRAG → CollectionRouter → Embedding → Milvus
    → 支持文档/上下文 → qwen-plus → 最终回答与 Token/Trace

全栈链路
React App → frontend/src/api.js → frontend/server.py 本地代理
    → main.py FastAPI → 千问/Milvus → JSON → TracePanel
```

### 1.2 模块证据表

| 模块 | 解决什么问题 | 为什么这样设计 | 源码与测试证据 | 替代方案及取舍 | 当前没有实现 |
| --- | --- | --- | --- | --- | --- |
| `Configuration` / `ModuleFactory` | 从 YAML 选择 LLM、Embedding、Loader、Vector DB，并装配 Agent | 统一接口便于替换供应商，动态导入减少分支代码 | `deepsearcher/configuration.py`；配置在 `deepsearcher/config.yaml` | 依赖注入容器更利于测试和多租户，但改造成本更高 | 请求级配置隔离、热更新一致性、生命周期管理 |
| `PDFLoader` | 把 PDF/TXT/MD 变成 LangChain `Document` | Loader 层把文件格式差异隔离在入库流程之前 | `deepsearcher/loader/file_loader/pdf_loader.py`；`tests/loader/file_loader/test_pdf_loader.py` | Docling/Unstructured 更强但依赖更重 | OCR、表格结构保留、扫描件质量检测 |
| `split_docs_to_chunks` | 把长文切成可嵌入和可检索的小块 | 1500 字符块、100 字符重叠降低边界信息丢失；`wider_text` 给生成阶段更多上下文 | `deepsearcher/loader/splitter.py`；`tests/loader/test_splitter.py` | Token/语义/标题分块更稳，但实现和评测复杂 | 基于文档类型的自适应分块与参数实验报告 |
| `OpenAIEmbedding` | 把文档和问题映射到同一向量空间 | 利用 OpenAI-compatible 协议接入千问 Embedding；配置维度显式为 1024 | `deepsearcher/embedding/openai_embedding.py`；`deepsearcher/config.yaml` | 本地 BGE 降低外部依赖，云模型省运维 | 模型版本治理、批量失败重试、Embedding 缓存 |
| `Milvus` | 存储向量、过滤 Collection、执行 dense/hybrid 检索 | Collection 适合隔离语料；Milvus 具备索引和混合检索扩展能力 | `deepsearcher/vector_db/milvus.py`；`tests/vector_db/test_milvus.py` | FAISS 简单但不是服务；pgvector 利于业务 SQL；Milvus 更专注向量检索 | 生产容量规划、备份恢复、监控告警；当前 `hybrid` 默认关闭 |
| `CollectionRouter` | 在多 Collection 中缩小检索范围 | Collection 有自然语言描述时，LLM 能做语义路由；单集合时零 Token 直接返回 | `deepsearcher/agent/collection_router.py`；`tests/agent/test_collection_router.py` | 规则/Embedding 路由更稳定便宜，LLM 路由更灵活 | 返回值白名单校验、缓存、路由评测 |
| `RAGRouter` | 在不同 RAG 策略间选择一个 Agent | 简单问题可走更直接策略，复杂问题可走迭代策略 | `deepsearcher/agent/rag_router.py`；`tests/agent/test_rag_router.py` | 规则分类器/小模型路由更可控；并行多 Agent 更贵 | 索引上下界校验、置信度、回退策略、路由指标 |
| `NaiveRAG` | 建立一次检索再生成的基线 | 链路短、成本低，便于和复杂 RAG 做 A/B 对比 | `deepsearcher/agent/naive_rag.py`；`tests/agent/test_naive_rag.py` | Query rewrite、rerank、迭代检索提高复杂问题召回，但延迟更高 | 正式基准数据与对比报表 |
| `DeepSearch` | 将复杂问题拆成子查询，迭代补齐信息缺口 | 并发处理子查询并对结果做相关性判断，适合多跳问题 | `deepsearcher/agent/deep_search.py`；`tests/agent/test_deep_search.py` | ChainOfRAG 的逐步问答更可解释；普通 RAG 更便宜 | Internet 搜索仍是 TODO；逐轮 Trace 未完整接入 |
| `ChainOfRAG` | 逐轮生成 follow-up query、检索、生成中间答案并筛选支持文档 | 让下一轮使用上一轮上下文，减少一次检索覆盖不了的知识缺口 | `deepsearcher/agent/chain_of_rag.py`；`tests/agent/test_chain_of_rag.py` | CoRAG 论文含训练与拒绝采样，本项目只是受其推理思想启发 | 论文训练流程、默认 early stopping、严格引用对齐 |
| `TraceCollector` | 把可观测事件组织成稳定 JSON，同时减少敏感信息暴露 | 显式埋点比解析日志可靠；限制可见文档并清理路径/URL 参数 | `deepsearcher/trace.py`；`tests/test_trace.py`、`tests/test_query_api.py` | OpenTelemetry 适合跨服务追踪；事件流适合实时 UI | SSE/WebSocket、持久化、Trace ID、DeepSearch 全链路事件 |
| FastAPI + 本地代理 | 对外提供查询/入库接口，并把浏览器文件上传转换为后端可读临时路径 | 浏览器不能直接传本机路径；同源代理统一错误与超时并隐藏上游地址 | `main.py`、`frontend/server.py`；`frontend/tests/test_server.py` | 直接 CORS 调后端层级少，但暴露地址且难统一安全策略 | 鉴权、限流、后台任务、取消、生产级错误码 |
| React + `TracePanel` | 展示服务状态、入库、查询、答案和逐轮 Agent 事件 | `useState` 管理局部交互状态，组件拆分让 Trace 独立测试 | `frontend/src/App.jsx`、`api.js`、`TracePanel.jsx` 及同名测试 | 状态机更适合复杂异步流程；React Query 更适合服务状态缓存 | 路由、多用户状态、实时流、完整键盘标签页交互验证 |

### 1.3 你必须能画出的依赖关系

```text
Configuration ──创建──> LLM / Embedding / Loader / VectorDB
      │
      ├──创建──> NaiveRAG
      └──创建──> RAGRouter ──选择──> DeepSearch
                              └──> ChainOfRAG

CollectionRouter 同时依赖 LLM 与 VectorDB
所有检索 Agent 同时依赖 Embedding 与 VectorDB
FastAPI 使用全局 configuration 中已装配的对象
```

面试官真正关心的不是你会不会画箭头，而是能否解释：依赖为什么放在这里、失败如何传播、替换某一模块会影响哪些边界。

## 2. 四周实战安排

### 第 1 周：把“能运行”变成“能解释”

#### 第 1 天：建立可复现基线

1. **今日目标：** 启动 Milvus、FastAPI、前端，完成一次 PDF 入库和一次查询。
2. **必读源码：** `README.md` 本地控制台段落、`main.py`、`frontend/server.py`。
3. **相关理论：** 进程、端口、HTTP 方法、JSON、环境变量与 OpenAI-compatible API。
4. **必做实验：** 依次停掉 19530、8500，观察 `/api/health` 和查询错误；记录状态码与中文错误。
5. **可交付成果：** 一张包含 8600/8500/19530 的运行拓扑和一份启动清单。
6. **当日面试题：** 浏览器为什么不直接调用模型和 Milvus？
7. **自测标准：** 不看文档，在 15 分钟内启动全部服务并指出每个端口的责任。

#### 第 2 天：Python 工程结构与配置工厂

1. **今日目标：** 解释模块装配、抽象基类和动态导入。
2. **必读源码：** `deepsearcher/configuration.py`、`agent/base.py`、`embedding/base.py`、`vector_db/base.py`。
3. **相关理论：** 依赖倒置、Factory、module singleton、Python import 生命周期。
4. **必做实验：** 在 REPL 打印 `Configuration().get_provider_config(...)` 与各实例类型，不改配置文件。
5. **可交付成果：** 一张“配置键 → 类名 → 实例 → 消费者”的表。
6. **当日面试题：** 全局配置为什么方便，又为什么会伤害并发和多租户？
7. **自测标准：** 能从 `init_config` 口述出所有对象的创建顺序与共享关系。

#### 第 3 天：PDF 解析与元数据

1. **今日目标：** 理解文件如何变成 `Document`，以及 reference 如何一路流动。
2. **必读源码：** `loader/file_loader/pdf_loader.py`、`file_loader/base.py`、对应测试。
3. **相关理论：** 文本型 PDF 与扫描 PDF、OCR、结构化解析、路径穿越。
4. **必做实验：** 分别加载 PDF、TXT、MD；准备一份扫描件验证当前 Loader 的边界。
5. **可交付成果：** 三种文件的输出样例和“当前不支持 OCR”的失败记录。
6. **当日面试题：** 为什么生产 RAG 不能只用 `pdfplumber.extract_text()`？
7. **自测标准：** 能说出解析质量如何影响后续 Chunk、Embedding 与召回。

#### 第 4 天：Chunk、重叠与窗口文本

1. **今日目标：** 理解 1500/100 与 `wider_text` 的作用和风险。
2. **必读源码：** `loader/splitter.py`、`offline_loading.py`、`tests/loader/test_splitter.py`。
3. **相关理论：** 字符分块与 Token 分块、语义边界、重叠、上下文窗口。
4. **必做实验：** 用 500/50、1500/100、3000/200 三组参数统计 Chunk 数，并人工检查跨段问题。
5. **可交付成果：** 参数—Chunk 数—命中质量—成本对比表。
6. **当日面试题：** Chunk 越大是否越好？重叠为什么不是越多越好？
7. **自测标准：** 能用“召回粒度、上下文完整、重复成本”三因素解释选择。

#### 第 5 天：Embedding 与向量距离

1. **今日目标：** 理解同一向量空间、维度、L2/COSINE/IP 的语义。
2. **必读源码：** `embedding/openai_embedding.py`、`embedding/base.py`、`config.yaml`。
3. **相关理论：** 向量表示、归一化、距离与相似度、批量 Embedding。
4. **必做实验：** 嵌入三个近义句和一个无关句，计算 L2 与 cosine 排名是否一致。
5. **可交付成果：** 一页实验结果，明确“当前 score 是 distance”。
6. **当日面试题：** 为什么查询向量维度必须与 Collection schema 一致？
7. **自测标准：** 能指出配置中的模型、维度以及维度不一致时的失败位置。

#### 第 6 天：Milvus Collection、索引与周复盘

1. **今日目标：** 解释 Collection schema、索引、dense/hybrid 分支和 Docker 选择。
2. **必读源码：** `vector_db/milvus.py`、`tests/vector_db/test_milvus.py`、`infra/milvus/docker-compose.yml`。
3. **相关理论：** ANN、索引召回/延迟取舍、Strong consistency、BM25 与 RRF。
4. **必做实验：** 用 PyMilvus 查看 schema、行数和 Collection 列表；对同一问题查看返回 distance。
5. **可交付成果：** 第 1 周知识图、一次 3 分钟录音、10 个术语的因果解释。
6. **当日面试题：** Milvus、FAISS、pgvector 如何选择？Milvus Lite 与 Docker 有何区别？
7. **自测标准：** 从 PDF 开始，无提示画完离线入库链路并回答 5 次“为什么”。

### 第 2 周：理解 RAG、路由与迭代检索

#### 第 7 天：NaiveRAG 基线

1. **今日目标：** 跑通一次“路由 Collection → 检索 → 总结”的最短 RAG。
2. **必读源码：** `agent/naive_rag.py`、`tests/agent/test_naive_rag.py`。
3. **相关理论：** RAG 的检索与生成边界、grounding、top-k、幻觉。
4. **必做实验：** 对事实型问题与多跳问题各运行一次，记录结果、Token 和延迟。
5. **可交付成果：** NaiveRAG 基线表，作为后续复杂 Agent 的对照组。
6. **当日面试题：** 为什么不对所有问题都用复杂 Agent？
7. **自测标准：** 90 秒内说明 NaiveRAG 的优点、失败模式和适用场景。

#### 第 8 天：CollectionRouter

1. **今日目标：** 解释单 Collection 快路径与多 Collection 的 LLM 路由。
2. **必读源码：** `agent/collection_router.py`、对应测试。
3. **相关理论：** 语义路由、规则路由、白名单校验、路由准确率。
4. **必做实验：** 建两个带描述的 Collection，设计 10 个问题并人工统计路由正确率。
5. **可交付成果：** 路由混淆矩阵和至少两个误路由案例。
6. **当日面试题：** 为什么没有描述的 Collection 和默认 Collection 会被追加？
7. **自测标准：** 能给出 LLM 路由的失败回退和可测指标。

#### 第 9 天：RAGRouter

1. **今日目标：** 理解策略路由的 Prompt、解析与非法索引风险。
2. **必读源码：** `agent/rag_router.py`、`tests/agent/test_rag_router.py`。
3. **相关理论：** 分类、结构化输出、容错解析、路由置信度。
4. **必做实验：** Mock LLM 返回 `2`、解释文字、`0`、`99`、无数字，记录行为。
5. **可交付成果：** 一张输入—解析—异常—期望回退表。
6. **当日面试题：** 当前 `find_last_digit` 为什么不够安全？
7. **自测标准：** 能定位负索引和越界索引风险，并提出最小修复方案。

#### 第 10 天：DeepSearch

1. **今日目标：** 解释子查询生成、并发检索、相关性筛选和 gap query。
2. **必读源码：** `agent/deep_search.py`、`tests/agent/test_deep_search.py`。
3. **相关理论：** query decomposition、multi-hop retrieval、`asyncio.gather`、去重。
4. **必做实验：** 对一个多实体问题打印所有子查询和每轮结果，比较 `max_iter=1/3`。
5. **可交付成果：** 每轮“新增信息/重复信息/Token/延迟”表。
6. **当日面试题：** 并发子查询减少了什么延迟，又引入什么资源风险？
7. **自测标准：** 能按调用顺序讲清 `_generate_sub_queries` 到最终 summary。

#### 第 11 天：ChainOfRAG 与支持文档

1. **今日目标：** 理解 follow-up query、中间答案和支持文档筛选。
2. **必读源码：** `agent/chain_of_rag.py`、`tests/agent/test_chain_of_rag.py`。
3. **相关理论：** iterative retrieval、evidence selection、LLM-as-judge 的偏差。
4. **必做实验：** 构造一条正确和一条冲突文档，观察 `_get_supported_docs` 的索引选择。
5. **可交付成果：** 逐轮时序图与一次错误筛选分析。
6. **当日面试题：** 用 LLM 选支持文档会不会循环论证？
7. **自测标准：** 能区分“检索命中”“中间回答”“支持最终回答”三个概念。

#### 第 12 天：CoRAG、反思与周复盘

1. **今日目标：** 严格区分论文 CoRAG 与仓库 `ChainOfRAG`。
2. **必读源码：** `chain_of_rag.py` 中 `early_stopping`；阅读 [CoRAG 论文](https://arxiv.org/abs/2501.14342)。
3. **相关理论：** 动态 query reformulation、逐步检索、训练数据构造、拒绝采样。
4. **必做实验：** 分别设置 `early_stopping=False/True`，观察轮数、反思字段、Token。
5. **可交付成果：** “论文有/项目有/项目没有”三列表和第 2 周 5 分钟录音。
6. **当日面试题：** early stopping 关闭后为什么没有反思结果？
7. **自测标准：** 不使用“复现论文”措辞，能准确说“受推理思想启发”。

### 第 3 周：后端、可观测性与前端数据流

#### 第 13 天：FastAPI 接口边界

1. **今日目标：** 读懂 provider、入库、网站、查询四组接口及 Pydantic 校验。
2. **必读源码：** `main.py`、`tests/test_query_api.py`。
3. **相关理论：** GET/POST 语义、幂等性、状态码、OpenAPI。
4. **必做实验：** 用 `Invoke-RestMethod` 调用 `/query/`，分别携带和不携带 `include_trace`。
5. **可交付成果：** 接口契约表和“保留旧响应”的兼容性说明。
6. **当日面试题：** 查询为什么用 GET 有争议，何时改 POST？
7. **自测标准：** 能说出每个输入约束、返回字段和异常映射。

#### 第 14 天：同步、异步与阻塞调用

1. **今日目标：** 理解 FastAPI 的 `def`、`async def` 与阻塞 I/O。
2. **必读源码：** `main.py`、`frontend/server.py`、[FastAPI 并发文档](https://fastapi.tiangolo.com/async/)。
3. **相关理论：** event loop、线程池、I/O-bound、CPU-bound、`asyncio.run` 限制。
4. **必做实验：** 用两个并发请求比较代理健康检查和长查询；记录事件循环是否被阻塞。
5. **可交付成果：** 一张调用点分类表：同步 SDK、async httpx、CPU 解析。
6. **当日面试题：** 在 `async def` 中直接调用阻塞 SDK 会怎样？
7. **自测标准：** 能给出 `run_in_threadpool`、任务队列和原生 async 三种改造条件。

#### 第 15 天：本地代理、上传与错误映射

1. **今日目标：** 理解 Base64 PDF、临时目录、同源代理与超时。
2. **必读源码：** `frontend/server.py`、`frontend/tests/test_server.py`。
3. **相关理论：** BFF、CORS、路径穿越、MIME/魔数校验、502/503。
4. **必做实验：** 上传 TXT、伪 PDF、超限 PDF、非法 Collection；验证 400、502、503 分工。
5. **可交付成果：** 威胁模型：输入、信任边界、校验、残余风险。
6. **当日面试题：** `Path(filename).name` 和 `TemporaryDirectory` 分别防什么？
7. **自测标准：** 能解释临时文件为何只在请求范围存在，以及后端何时必须读完。

#### 第 16 天：TraceCollector 与安全边界

1. **今日目标：** 掌握显式事件埋点、版本化 JSON 与脱敏。
2. **必读源码：** `trace.py`、`online_query.py`、RAGRouter/ChainOfRAG Trace 代码、`tests/test_trace.py`。
3. **相关理论：** observability、trace/span、结构化日志、隐私最小化、思维链边界。
4. **必做实验：** 构造本地路径、带 token URL、超长文本和 7 条文档，检查输出限制。
5. **可交付成果：** Trace schema、字段来源和敏感字段清单。
6. **当日面试题：** 为什么 Trace 不是模型思维链？
7. **自测标准：** 能逐字段说明“谁记录、何时记录、如何脱敏、如何测试”。

#### 第 17 天：React 状态与 API 适配

1. **今日目标：** 理解 App 的状态、事件、派生展示和 API 规范化。
2. **必读源码：** `frontend/src/App.jsx`、`api.js`、同名测试、[React 状态模型](https://react.dev/learn/state-a-components-memory)。
3. **相关理论：** state snapshot、单向数据流、受控输入、异步竞态。
4. **必做实验：** 画出点击“运行查询”后所有 state 的变化；模拟成功与错误响应。
5. **可交付成果：** React 状态转换图和两条竞态风险说明。
6. **当日面试题：** 为什么 API 层要把 snake_case 规范化为前端字段？
7. **自测标准：** 能定位问题、加载、成功、错误、Trace、日志各自的 state 所有者。

#### 第 18 天：标签页、测试与周复盘

1. **今日目标：** 理解 TracePanel 的组件边界、ARIA 与前后端测试金字塔。
2. **必读源码：** `TracePanel.jsx`、`TracePanel.test.jsx`、[W3C Tabs 模式](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/)。
3. **相关理论：** `tablist/tab/tabpanel`、键盘操作、单元/集成/E2E 测试。
4. **必做实验：** 只用键盘切换标签和展开轮次；运行 Python/React 定向测试并保存输出。
5. **可交付成果：** 可访问性差距清单、测试覆盖矩阵、第 3 周 5 分钟录音。
6. **当日面试题：** `role="tab"` 是否等于完成了无障碍？
7. **自测标准：** 能指出已有测试证明什么、没有证明什么。

### 第 4 周：评测、生产化与面试表达

#### 第 19 天：建立 RAG 评测集

1. **今日目标：** 把“答案看起来不错”变成可重复评测。
2. **必读源码：** `RetrievalResult`、Agent 返回结构、现有测试夹具。
3. **相关理论：** Recall@k、MRR、nDCG、faithfulness、answer correctness。
4. **必做实验：** 从现有语料制作 20 题小型黄金集，标注答案与相关文档。
5. **可交付成果：** `question/expected_source/reference_answer` 评测表设计，不提交敏感语料。
6. **当日面试题：** 召回好但回答差，和召回差但回答像真的，如何区分？
7. **自测标准：** 能给检索层、生成层、系统层各选至少两个指标。

#### 第 20 天：延迟、Token 与成本

1. **今日目标：** 分解端到端延迟和 Token，而不是只看总数。
2. **必读源码：** `TraceCollector` token 字段、代理 `latency_ms`、Agent 的 Token 累加。
3. **相关理论：** p50/p95/p99、预算、冷启动、吞吐、成本模型。
4. **必做实验：** 对 10 个问题比较 `max_iter=1/3` 的延迟、Token、支持文档数与正确率。
5. **可交付成果：** 一张质量—延迟—成本 Pareto 图。
6. **当日面试题：** 为什么平均延迟不足以描述用户体验？
7. **自测标准：** 能提出一个明确 SLO，并说明测量窗口和失败定义。

#### 第 21 天：混合检索与 RRF

1. **今日目标：** 理解 dense、BM25、RRF 及当前代码的 `hybrid` 分支。
2. **必读源码：** `vector_db/milvus.py`；[Milvus 混合检索与 RRF](https://milvus.io/docs/multi-vector-search.md)。
3. **相关理论：** 语义召回、关键词召回、rank fusion、rerank。
4. **必做实验：** 用专有名词/编号问题比较 dense 与 hybrid；记录 Recall@k 与延迟。
5. **可交付成果：** 是否默认开启 hybrid 的证据化决策记录。
6. **当日面试题：** RRF 为什么不直接比较不同检索器的原始分数？
7. **自测标准：** 能说清当前代码“具备分支”与当前配置“默认未开启”的区别。

#### 第 22 天：并发、安全与十倍流量

1. **今日目标：** 对全局单例、阻塞调用、临时文件和外部 API 做容量与威胁分析。
2. **必读源码：** `configuration.py`、`main.py`、`frontend/server.py`、Milvus 异常处理。
3. **相关理论：** 无状态服务、连接池、队列、背压、鉴权、限流、租户隔离。
4. **必做实验：** 写 10 并发请求压测脚本，只在测试语料上运行；记录失败率和 p95。
5. **可交付成果：** “流量扩大十倍”分阶段改造图与风险排序。
6. **当日面试题：** 为什么简单加 Uvicorn worker 仍解决不了全局配置和外部限额？
7. **自测标准：** 能从入口、计算、依赖、存储四层提出限流与隔离。

#### 第 23 天：系统设计与 SSE 路线

1. **今日目标：** 设计生产版查询链路和非流式 Trace 的升级方案。
2. **必读源码：** `query_with_trace`、`TraceCollector`、代理 `/api/query`、React 查询逻辑。
3. **相关理论：** SSE、事件 ID、断线重连、取消、幂等、异步任务。
4. **必做实验：** 画事件协议：`routing/iteration/retrieval/reflection/final/error`，定义顺序与结束条件。
5. **可交付成果：** 10 分钟系统设计白板图和 API 草案，但不把草案说成已实现。
6. **当日面试题：** SSE、WebSocket、轮询为何此处优先 SSE？
7. **自测标准：** 能处理客户端断线、重复事件、服务重启、慢消费者四种情况。

#### 第 24 天：项目表达与模拟面试

1. **今日目标：** 完成 1/3/10 分钟讲述、端到端演示和四角色压力面试。
2. **必读源码：** 回看知识地图中的所有证据路径与最近一次测试输出。
3. **相关理论：** STAR、结论先行、证据—取舍—边界表达。
4. **必做实验：** 录制一次演示；依次扮演 RAG、后端、前端、质疑型面试官追问。
5. **可交付成果：** 最终讲稿、简历两条、事实清单、改进路线和薄弱题清单。
6. **当日面试题：** 这个项目最大的三个技术债是什么，你先修哪个？
7. **自测标准：** 任抽 10 题，每题都覆盖“是什么、为什么、怎么做、取舍、如何验证”。

## 3. 分层面试拷问题库（36 题）

回答顺序固定为：**先给结论，再讲因果链，接着给源码/测试证据，最后主动说明取舍与边界**。深入回答不是逐字背诵稿，而是 2～3 分钟的结构。

### 3.1 项目架构与选型（6 题）

#### 1. 请用一句话和三分钟介绍 DeepSearcher 的架构

- **一句话回答：** 它是一个把离线文档向量化入 Milvus、在线由 LLM 路由多种 RAG 策略检索并用千问生成答案、再由 React 展示结构化 Trace 的 AI 全栈学习项目。
- **2～3 分钟深入回答：** 离线侧由 `PDFLoader` 解析、`split_docs_to_chunks` 以 1500/100 切分并保存 `wider_text`，`OpenAIEmbedding` 生成 1024 维向量后写入 Milvus。在线侧 `RAGRouter` 在 `DeepSearch` 和 `ChainOfRAG` 间选择，Agent 再通过 `CollectionRouter` 选语料、检索、汇总。全栈侧 React 只调用同源的 `frontend/server.py`，代理转发到 `main.py`，把答案、Token、延迟和安全 Trace 返回 `TracePanel`。
- **连续追问：** 为什么要分离入库和查询？哪一层最容易成为瓶颈？Agent 路由失败如何回退？
- **项目证据：** `offline_loading.py`、`online_query.py`、`configuration.py`、`frontend/server.py`；定向证据见 `tests/test_query_api.py` 和前端测试。
- **取舍与替代：** 组件化利于替换供应商和学习链路，但全局实例让多租户隔离变难；可改请求级依赖注入和后台入库任务。
- **诚实边界：** 这是已跑通的本地学习系统，不是已经具备 SLA、鉴权、水平扩容的生产平台。

#### 2. 为什么使用 Configuration + ModuleFactory？

- **一句话回答：** 为了把“选哪个供应商”和“业务如何调用”分离，让 LLM、Embedding、Loader、Vector DB 能通过统一接口替换。
- **2～3 分钟深入回答：** `Configuration` 从 YAML 读取 provider 与 config，`ModuleFactory._create_module_instance` 根据类名动态导入并实例化，`init_config` 再把依赖注入 Agent。这样上层只依赖 `BaseLLM`、`BaseEmbedding` 等抽象；增加供应商主要落在适配器层。代价是配置错误会推迟到运行时暴露，且模块级 global 使生命周期与并发边界模糊。
- **连续追问：** 动态导入如何做类型安全？为什么测试时容易受环境变量污染？热切换 provider 时正在执行的请求怎么办？
- **项目证据：** `deepsearcher/configuration.py` 的 `ModuleFactory`、`init_config`；各 provider 测试在 `tests/llm`、`tests/embedding`。
- **取舍与替代：** 显式工厂更简单；FastAPI `Depends` 或 DI 容器能提供请求级作用域和可测试性，但会增加装配代码。
- **诚实边界：** 当前没有原子热切换、配置版本、租户级实例或回滚机制。

#### 3. 为什么默认不直接使用普通 Naive RAG？

- **一句话回答：** NaiveRAG 对单事实问题便宜有效，但复杂多跳问题可能一次查询召回不全，所以默认路由提供了可拆解、可迭代的 Agent。
- **2～3 分钟深入回答：** `NaiveRAG` 是一次 Collection 路由、一次向量检索、一次总结；`DeepSearch` 会拆分子查询并补 gap，`ChainOfRAG` 会根据中间问答生成下一步问题并筛支持文档。复杂策略可能提高多跳召回，却增加 LLM 调用、延迟和错误传播。因此正确做法不是宣布 Agent 一定更好，而是用同一评测集对比 Recall@k、正确率、Token 与 p95。
- **连续追问：** 什么问题应走 NaiveRAG？路由成本会不会超过收益？如何证明复杂 Agent 更好？
- **项目证据：** `agent/naive_rag.py`、`deep_search.py`、`chain_of_rag.py`；三者各有单元测试。
- **取舍与替代：** 可按问题长度/实体数做规则路由，或先 Naive、低置信度再升级；更可控但可能漏掉隐式复杂问题。
- **诚实边界：** 当前默认路由甚至不包含 NaiveRAG，也没有路由收益的正式实验，所以只能说“设计意图”，不能说“已经证明更优”。

#### 4. 为什么用 LLM 做 Agent 路由？

- **一句话回答：** 因为 Agent 描述和问题都是自然语言，LLM 能快速实现开放语义匹配，但必须加结构化约束、校验、回退与评测。
- **2～3 分钟深入回答：** `RAGRouter` 把 Agent 索引和描述放入 Prompt，要求只返回一个数字。这比手写关键词规则覆盖面广，新增 Agent 也方便；但输出存在非确定性、注入和非法索引风险，而且多一次模型调用。生产方案应使用 schema/枚举输出，校验 `0 <= index < len(agents)`，解析失败时走默认 Agent，并记录路由标签做准确率和成本评测。
- **连续追问：** 温度设多少？如果两个 Agent 都适合呢？如何避免用户在问题里操纵路由？
- **项目证据：** `agent/rag_router.py` 的 `_route`、`find_last_digit`；`tests/agent/test_rag_router.py` 覆盖纯数字和尾部数字回退。
- **取舍与替代：** 规则/Embedding/小分类器更稳定便宜；LLM 更灵活。可采用分层路由或小模型优先、LLM 兜底。
- **诚实边界：** 当前没有置信度、白名单验证、Prompt injection 防护和路由评测集。

#### 5. 为什么前端要经过本地代理？

- **一句话回答：** 代理作为 BFF 统一同源访问、文件临时落盘、上游地址、超时和中文错误，同时避免浏览器接触模型密钥。
- **2～3 分钟深入回答：** React 通过 `/api/ingest` 上传 Base64，代理验证扩展名、PDF 魔数、20 MiB 限制和 Collection 名称，再写入 `TemporaryDirectory`，因为 DeepSearcher 原接口接收后端本地路径。查询代理将 POST body 转成后端 GET 参数，并附带 `include_trace=True`。这也让浏览器不需要开放宽泛 CORS。代价是多一跳、内存中 Base64 约有膨胀、代理可能成为瓶颈。
- **连续追问：** 为什么不用 multipart？临时文件何时删除？8500 不可用为什么是 503？
- **项目证据：** `frontend/server.py`、`frontend/src/api.js`、`frontend/tests/test_server.py`。
- **取舍与替代：** 可让后端原生接收 multipart 或对象存储 URL，减少 Base64 和双层 API；但需要修改业务接口。
- **诚实边界：** 当前代理没有鉴权、上传病毒扫描、请求 ID、限流或分布式部署下的共享文件机制。

#### 6. 当前项目最大的三个技术债是什么？先修哪个？

- **一句话回答：** 第一是没有可重复 RAG 评测，第二是全局单例与同步长任务限制并发/多租户，第三是路由和 Milvus 异常缺少严格失败语义；我先补评测，因为没有指标无法判断后续改造是否真的变好。
- **2～3 分钟深入回答：** 先建立带黄金来源的 20～100 题数据集，分开测 Recall@k、faithfulness、正确率、延迟与 Token；再把配置和客户端改成应用/请求作用域，入库移到任务队列；最后统一路由校验和向量库异常，让失败可重试、可告警而不是返回空结果。之后才是 hybrid、SSE、缓存等优化，否则容易优化错方向。
- **连续追问：** 为什么安全不是第一？两周只能做一件事怎么办？如何定义评测通过？
- **项目证据：** 评测缺口可从仓库只有单元测试、没有黄金集看出；全局对象在 `configuration.py`；异常吞并在 `vector_db/milvus.py`。
- **取舍与替代：** 若马上公网发布，鉴权和密钥安全优先级必须提升到最高；优先级取决于暴露面，而非固定答案。
- **诚实边界：** 这是改造路线，不是当前已完成能力；测试失败数量也要以面试前最新命令为准。

### 3.2 RAG 与 Agent（10 题）

#### 7. NaiveRAG、DeepSearch、ChainOfRAG 的核心差异是什么？

- **一句话回答：** NaiveRAG 一次检索，DeepSearch 先并行拆问题再迭代补缺口，ChainOfRAG 则串行利用中间问答生成下一步检索。
- **2～3 分钟深入回答：** 三者共享 Embedding、CollectionRouter 和 Vector DB，但控制流不同。NaiveRAG 延迟/Token 最低；DeepSearch 适合可并行分解的复杂问题，通过 gap query 继续补知识；ChainOfRAG 让每轮 follow-up 依赖历史，解释性更强，却更慢且误差会累积。选择必须由问题类型和质量—成本曲线驱动。
- **连续追问：** 哪个更适合多跳？哪个更容易并发？如何避免迭代重复？
- **项目证据：** `agent/naive_rag.py`、`deep_search.py`、`chain_of_rag.py` 及其测试。
- **取舍与替代：** 可加入 reranker、query expansion 或 graph retrieval；复杂度提高前先证明基线瓶颈。
- **诚实边界：** 项目没有三者的正式 A/B 数据，不能给出“某 Agent 提升 X%”的数字。

#### 8. RAGRouter 输出非法索引会怎样？

- **一句话回答：** 非数字会尝试取最后一个数字，但 0 会变成 Python 的 `-1` 误选最后 Agent，过大索引会抛 `IndexError`，无数字会抛 `ValueError`。
- **2～3 分钟深入回答：** `_route` 先 `int(content)-1`，解析失败才调用 `find_last_digit`，随后直接访问 `self.rag_agents[selected_agent_index]`。代码没有边界校验，因此“能解析”不等于“合法”。最小修复是结构化整数输出、范围检查、日志记录和默认回退；测试应覆盖 0、负数、超界、多位数、Prompt 注入和空列表。
- **连续追问：** `find_last_digit` 对 12 返回什么？应该抛错还是回退？如何监控误路由？
- **项目证据：** `agent/rag_router.py`；现有测试覆盖正常数字、解释文本和无数字，但未完整覆盖上下界。
- **取舍与替代：** 严格失败便于发现问题；默认回退提高可用性。生产可回退同时上报指标。
- **诚实边界：** 当前风险尚未在业务代码修复，文档只能准确指出，不能说系统已经防住。

#### 9. CollectionRouter 为什么使用 LLM，返回结果可靠吗？

- **一句话回答：** 它用 Collection 的名称与描述做语义路由，灵活但不天然可靠，必须验证返回集合确实存在。
- **2～3 分钟深入回答：** 无 Collection 返回空，单 Collection 直接返回且不花 Token；多 Collection 才调用 LLM 解析 Python list。随后会追加无描述集合和默认集合，降低漏召回，但可能扩大检索范围。当前 `literal_eval` 降低任意代码执行风险，却没有对名称做显式白名单过滤，错误名称会下沉到向量库。
- **连续追问：** 为什么追加无描述集合？描述由谁维护？路由错误如何评测？
- **项目证据：** `agent/collection_router.py` 与 `tests/agent/test_collection_router.py`。
- **取舍与替代：** Embedding 路由便宜稳定，规则路由可审计，LLM 适合复杂描述；可先 top-n 语义召回再 LLM 精排。
- **诚实边界：** 当前没有 Collection ACL，不能把路由等同于租户数据授权。

#### 10. DeepSearch 如何生成和使用子查询？

- **一句话回答：** 它先让 LLM 把原问题拆成简单子查询，并发检索后筛相关文档，再根据已有结果生成 gap query 进入下一轮。
- **2～3 分钟深入回答：** `_generate_sub_queries` 产生列表；`async_retrieve` 为每个 gap query 创建任务并用 `asyncio.gather` 等待；每个任务按 Collection 搜索并让 LLM判断文档相关性；未到最后一轮时 `_generate_gap_queries` 根据原问题、全部子查询和已检索块寻找知识缺口。最后去重并汇总。并发降低同轮等待，但模型/向量库调用数增大。
- **连续追问：** 为什么最后一轮不再反思？一个任务失败会怎样？如何限制子查询爆炸？
- **项目证据：** `agent/deep_search.py` 的 `_generate_sub_queries`、`async_retrieve`、`_generate_gap_queries`。
- **取舍与替代：** 可设并发 semaphore、单任务容错、查询去重和预算上限；会增加控制逻辑。
- **诚实边界：** Internet 检索变量仍标为 TODO，且 DeepSearch 的逐轮 Trace 尚未完整接入。

#### 11. ChainOfRAG 与 CoRAG 论文相同和不同在哪里？

- **一句话回答：** 相同点是都强调根据历史中间结果动态改写查询并逐步检索；不同点是本项目仅实现推理时的启发式链路，没有论文的训练、数据构造和拒绝采样。
- **2～3 分钟深入回答：** 项目每轮生成 follow-up question、检索、生成 intermediate answer、筛支持文档，并可判断信息是否足够；这对应逐步检索思想。但类注释只是 `Inspired by`，不能称为论文复现。论文讨论如何训练模型学会 CoT-guided retrieval 与构造训练样本，本仓库直接依赖通用 LLM Prompt。
- **连续追问：** 没有训练会损失什么？项目为何仍叫 ChainOfRAG？如何验证差异？
- **项目证据：** `agent/chain_of_rag.py` 类注释与方法；论文：[CoRAG](https://arxiv.org/abs/2501.14342)。
- **取舍与替代：** Prompt 方案开发快、可换模型；训练方案可能更稳定，但需要数据、算力和复杂评测。
- **诚实边界：** 只能说“受 CoRAG 启发”，不能说“实现/复现了 CoRAG 训练方法”。

#### 12. 子查询为什么可能提高召回，又可能降低质量？

- **一句话回答：** 子查询把复合意图拆小可覆盖更多证据，但错误分解会造成语义漂移、重复检索和噪声累积。
- **2～3 分钟深入回答：** 对“负责人和发布日期”这类问题，两个子查询比一个长向量更易命中各自证据；但 LLM 可能丢限定条件，后续 intermediate answer 又影响下一轮，形成误差放大。应限制数量、保留原问题约束、对重复查询去重，并比较原查询与子查询的联合召回。
- **连续追问：** 如何判断语义漂移？是否应同时检索原问题？子查询数量如何设？
- **项目证据：** DeepSearch 的 sub/gap query 与 ChainOfRAG 的 follow-up Prompt；Trace 可观察 ChainOfRAG 子查询。
- **取舍与替代：** 可用 multi-query union、HyDE、规则拆解；每种方法对成本和可控性不同。
- **诚实边界：** 当前没有自动漂移检测，也没有证明默认 `max_iter=3` 是最优。

#### 13. 为什么还要筛“支持文档”？

- **一句话回答：** 检索相关不等于能支持具体答案，支持性筛选试图只保留真正为中间问答提供证据的文档。
- **2～3 分钟深入回答：** `_get_supported_docs` 把文档、follow-up query 和 intermediate answer 一起交给 LLM，返回文档下标，最终回答基于累积支持文档。好处是减少上下文噪声，风险是同一个 LLM 既生成答案又判断支持性，可能自证；非法/负数索引也需验证。更稳的方案是独立 reranker/NLI 模型、引用句对齐和人工评测。
- **连续追问：** 负下标会怎样？筛错后能否恢复？如何测 precision/recall？
- **项目证据：** `chain_of_rag.py::_get_supported_docs` 及 `test_get_supported_docs`。
- **取舍与替代：** LLM judge 迭代快；专用模型便宜稳定；人工标注可靠但贵。
- **诚实边界：** 当前筛选不是事实验证器，也没有校准分数或交叉模型验证。

#### 14. early stopping 关闭后为什么没有反思结果？

- **一句话回答：** 因为 `_check_has_enough_info` 只在 `self.early_stopping` 为真时调用，默认值为假，所以 Trace 的 `has_enough_information` 保持 `null` 是预期行为。
- **2～3 分钟深入回答：** `retrieve` 每轮始终生成 follow-up、检索、回答、筛支持文档；只有开启 early stopping 才额外调用 LLM 判断 `yes/no`，记录 reflection token，并可能提前 break。关闭能保证固定轮数和较稳定的流程形态，但会浪费已足够信息后的 Token；开启能节省成本，却可能因误判过早停止。
- **连续追问：** UI 应如何区分“未执行”和“否”？默认为什么是 false？如何校准停止阈值？
- **项目证据：** `chain_of_rag.py` 构造参数和 `if self.early_stopping`；`trace.py` 默认字段为 `None`。
- **取舍与替代：** 可用规则（支持文档数/覆盖度）、LLM judge 或混合条件；需评测停止错误率。
- **诚实边界：** 默认 Trace 不能声称展示了每轮反思，只能展示“未执行/无结果”。

#### 15. 为什么 Trace 不是模型思维链？

- **一句话回答：** Trace 只记录程序可验证的输入输出事件和统计，不保存或声称暴露模型内部逐 token 推理。
- **2～3 分钟深入回答：** `TraceCollector` 接收 Agent 名、子查询、Collection、检索文档摘要、中间答案、支持标记和 Token；这些都是应用显式传入的字段。它还截断文本、限制最多展示 5 条文档、移除本地目录和 URL 查询参数、丢弃 embedding/metadata。隐藏推理既不是稳定 API，也可能包含敏感或误导信息；可观测性应关注决策结果和证据。
- **连续追问：** 中间答案算不算思维链？如何调试 Prompt？哪些字段不能展示？
- **项目证据：** `deepsearcher/trace.py` 和 `tests/test_trace.py` 的脱敏、截断测试。
- **取舍与替代：** 开发环境可保存受控 Prompt/响应并严格授权；生产只保留必要事件、哈希或采样。
- **诚实边界：** 当前脱敏规则有限，不能保证清除文档正文中的所有 PII 或密钥。

#### 16. 当前系统如何评测召回质量和回答质量？

- **一句话回答：** 当前仓库主要有功能单元测试，没有正式 RAG 质量评测；应新增黄金集并分层测 retrieval、generation 与系统指标。
- **2～3 分钟深入回答：** 检索层用 Recall@k、MRR/nDCG、支持文档 precision；生成层用 faithfulness、引用正确率、answer correctness，并对关键问题人工复核；系统层看 p50/p95、Token、错误率和成本。Agent 路由还要单独测 route accuracy。评测数据必须固定模型版本、Collection 快照和参数，结果才可比较。
- **连续追问：** 没有唯一答案怎么办？LLM judge 是否可信？20 题够不够？
- **项目证据：** 现有 `tests/` 验证控制流和契约，但没有黄金 QA 数据或评测 runner。
- **取舍与替代：** 自动指标适合回归，人工评审适合高价值样本；二者结合并保留盲测集。
- **诚实边界：** 在评测补齐前，只能展示具体案例和测试，不得宣称整体准确率或召回提升。

### 3.3 Embedding 与 Milvus（8 题）

#### 17. Chunk 大小和重叠为什么设为 1500/100？

- **一句话回答：** 这是当前项目的经验默认值，用较大字符块保留语义、少量重叠缓解边界切断，但并非经过本项目评测证明的最优参数。
- **2～3 分钟深入回答：** `RecursiveCharacterTextSplitter` 按字符近似控制块大小，1500 提供较完整段落，100 让跨边界事实在相邻块重复；随后额外保存前后约 300 字符 `wider_text` 供生成。块太小会碎片化和增加向量数，太大则召回不精确、上下文噪声和 Token 上升。应按语料与问题集做网格实验。
- **连续追问：** 为什么按字符不是 Token？中文和英文有何差异？重叠与 wider_text 是否重复？
- **项目证据：** `offline_loading.py` 默认值；`loader/splitter.py` 的 splitter 和 `offset=300`。
- **取舍与替代：** Token、句子、标题、语义分块更贴合模型，但解析与实现更复杂。
- **诚实边界：** 不能说 1500/100 是行业最佳；当前 `_sentence_window_split` 还依赖 `original_text.index`，重复文本值得专项测试。

#### 18. `wider_text` 有什么价值和代价？

- **一句话回答：** 检索用较小 Chunk 保持精度，生成时用 wider window 恢复上下文；代价是 Token 增加、重复内容和潜在越权暴露。
- **2～3 分钟深入回答：** Chunk 的 `text` 用于嵌入和匹配，metadata 中的 `wider_text` 含前后上下文；Agent 在 `text_window_splitter=True` 时优先把 wider text 送给 LLM。这是“small-to-big retrieval”思路。它不改变命中文档，却会扩大最终 Prompt，应去重、限制窗口，并确保 metadata 不跨文档或租户边界。
- **连续追问：** wider_text 为什么不直接嵌入？相邻命中如何去重？会不会泄漏无关段落？
- **项目证据：** `loader/splitter.py`；NaiveRAG、DeepSearch、ChainOfRAG 读取 `wider_text` 的分支。
- **取舍与替代：** 父子文档检索、句子窗口、动态窗口更精细，但需要额外 ID 和存储结构。
- **诚实边界：** 当前窗口固定，不会根据问题复杂度或 Token 预算动态调整。

#### 19. Embedding 维度为什么必须与 Collection 一致？

- **一句话回答：** 向量距离只能在同一维度空间计算，Collection schema 固定了 `FLOAT_VECTOR dim`，查询与入库向量不一致会被 Milvus 拒绝或无法参与检索。
- **2～3 分钟深入回答：** 入库时 `offline_loading` 用 `embedding_model.dimension` 创建 Collection，再写入每个 Chunk 的 embedding；查询时使用同一个模型产生 query vector。`CollectionRouter` 列集合时还按 dim 过滤。更换模型或维度不能只改 API 参数，必须新建/重建 Collection 并重新嵌入全部文档，同时版本化模型和索引。
- **连续追问：** 同维度不同模型能混用吗？降维后能复用旧向量吗？迁移如何不停机？
- **项目证据：** `offline_loading.py::init_collection(dim=...)`、`vector_db/milvus.py` schema 与 `list_collections(dim=...)`。
- **取舍与替代：** 双写新旧 Collection、后台回填、别名切换可平滑迁移，但存储和成本翻倍。
- **诚实边界：** 维度一致只是必要条件；同维不同模型仍不是同一语义空间，不能混用。

#### 20. L2 距离能否直接叫“相似度”？

- **一句话回答：** 不能；当前默认 L2 返回的是距离，通常越小越近，而 cosine/IP 的含义和排序方向可能不同，必须连同 metric 一起解释。
- **2～3 分钟深入回答：** `Milvus.init_collection` 默认 `metric_type="L2"`，`search_data` 把结果中的 `distance` 放进 `RetrievalResult.score`。`score` 只是通用字段名，不代表越大越好。UI 若统一叫“相似度”会误导；应显示“L2 距离”，或在后端携带 metric/direction，再做有依据的归一化。不同指标原始值也不能横向比较。
- **连续追问：** 归一化向量下 L2 与 cosine 有何关系？阈值怎么设？RRF 为什么绕开原始分数？
- **项目证据：** `vector_db/milvus.py` 的默认参数和 `score=b["distance"]`；TracePanel 展示 score。
- **取舍与替代：** 可改字段名为 `distance`，或返回 `{metric, value, better}`；兼容性需要版本化。
- **诚实边界：** 当前代码没有把 metric 放入 Trace，前端无法仅凭数值可靠判断好坏。

#### 21. 为什么选择 Milvus，和 pgvector、FAISS 有何区别？

- **一句话回答：** Milvus 适合独立向量检索服务和多 Collection/索引扩展；pgvector 适合向量与业务关系数据同库；FAISS 适合单机库内检索和原型。
- **2～3 分钟深入回答：** 本项目需要 Collection、schema、服务化访问和未来 dense+BM25 混合检索，因此 Milvus 接口契合。pgvector 可复用 PostgreSQL 的事务、权限与 JOIN，运维面更小，但大规模 ANN 调优与专用能力取舍不同。FAISS 性能强且简单，但持久化、并发、权限、高可用要自己封装。选择由规模、过滤、事务、团队运维和 SLA 决定。
- **连续追问：** 小项目为何不选 pgvector？多少数据算需要 Milvus？元数据过滤怎么影响选择？
- **项目证据：** `vector_db/milvus.py` 已实现 Collection、dense/hybrid；仓库也有 Qdrant/Oracle 适配器，说明接口可替换。
- **取舍与替代：** 学习 Milvus能展示向量数据库能力；生产若已有 PostgreSQL 且规模小，pgvector 可能更经济。
- **诚实边界：** 本项目没有做 Milvus/pgvector/FAISS 基准测试，不能声称性能绝对领先。

#### 22. Milvus Lite 与 Docker Milvus 如何选择？

- **一句话回答：** Lite 适合受支持环境中的单进程本地开发、零服务依赖；Docker Milvus 更接近独立服务形态并适合当前 Windows 基线，但占用资源和运维步骤更多。
- **2～3 分钟深入回答：** Lite 通常以本地文件 URI 嵌入进程，部署轻、演示方便，却不代表分布式 Milvus 的网络、并发和运维行为。当前仓库依赖锁只明确包含 `pymilvus`，配置指向 19530，Windows 本地基线已用 Compose 验证，因此继续用 Docker 风险更低。是否切 Lite 要先确认平台支持、安装包、功能差异和测试结果。
- **连续追问：** Lite 数据如何迁移？hybrid/BM25 是否等价？为什么“本地”不等于 Lite？
- **项目证据：** `deepsearcher/config.yaml` 的 HTTP URI、`infra/milvus/docker-compose.yml`、`pyproject.toml`/`uv.lock`。
- **取舍与替代：** CI/个人实验可评估 Lite；多人协作或模拟生产用容器/托管 Milvus更合适。
- **诚实边界：** 当前项目实际运行的是 Docker Milvus，不能在简历上写成“已使用 Milvus Lite”。

#### 23. Collection、索引和 Strong consistency 在项目中如何使用？

- **一句话回答：** Collection 固定向量 schema 与语料边界，创建时为 embedding 建索引，并使用 Strong consistency 让刚写入的数据立即可见。
- **2～3 分钟深入回答：** `init_collection` 创建主键、1024 维向量、文本、reference、JSON metadata；hybrid 时再加 sparse vector 与 BM25 function。索引参数由客户端准备，dense 至少绑定 metric；Collection 创建使用 `consistency_level="Strong"`。Strong 简化入库后马上查询的演示，但可能牺牲吞吐/延迟，生产需按读写一致性要求选择。
- **连续追问：** 为什么 Collection 不是租户权限边界？索引类型默认是什么？重复入库如何处理？
- **项目证据：** `vector_db/milvus.py::init_collection`。
- **取舍与替代：** 数据库/Collection/partition/metadata filter 都可隔离语料；粒度越细管理成本越高。
- **诚实边界：** 当前代码没有显式去重 ID、版本字段、软删除、备份恢复和容量指标。

#### 24. 混合检索和 RRF 是什么，项目是否已启用？

- **一句话回答：** 混合检索合并 dense 语义结果与 BM25 稀疏结果，RRF 按排名而非不可比原始分数融合；代码支持该分支，但当前配置默认未启用。
- **2～3 分钟深入回答：** `Milvus(hybrid=True)` 时 schema 增加 analyzer、`sparse_vector` 和 BM25 function；查询构造 sparse/dense 两个 `AnnSearchRequest`，用 `RRFRanker` 融合。dense 擅长同义表达，BM25 擅长编号、专名和精确词，组合可能提高稳健性，也会增加索引、存储和查询成本。是否启用应看黄金集收益。
- **连续追问：** RRF 的 k 如何影响排名？何时需要 reranker？中文 tokenizer 是否合适？
- **项目证据：** `vector_db/milvus.py` 的 `hybrid` 分支；权威资料：[Milvus 混合检索与 RRF](https://milvus.io/docs/multi-vector-search.md)。
- **取舍与替代：** weighted ranker 可调权重但需校准；RRF 简单稳健但忽略原始置信度。
- **诚实边界：** `config.yaml` 未设置 `hybrid: true`，没有当前语料上的收益数字。

### 3.4 FastAPI、可靠性和安全（7 题）

#### 25. FastAPI 的 `def`、`async def` 和阻塞调用如何处理？

- **一句话回答：** 同步 `def` 路由可由 FastAPI 放入线程池；`async def` 适合真正可 await 的 I/O，但其中若直接运行阻塞 SDK 会卡住事件循环。
- **2～3 分钟深入回答：** `main.py` 的查询/入库是 `def`，内部同步 LLM、PDF 和 Milvus 调用不会直接占住主 event loop，但受线程池容量限制；代理是 `async def` 并使用 `httpx.AsyncClient`、`asyncio.open_connection`，这是正确的异步 I/O。若在代理 async 路由中直接调用同步 PyMilvus，应改用 `run_in_threadpool`、原生 async 客户端或任务队列。CPU-heavy PDF/OCR 还需进程/worker。
- **连续追问：** `asyncio.run` 在已有 event loop 中会怎样？线程池能无限扩吗？多个 Uvicorn worker 有何代价？
- **项目证据：** `main.py`、`frontend/server.py`、`deep_search.py::retrieve`；[FastAPI async](https://fastapi.tiangolo.com/async/)。
- **取舍与替代：** 线程池适合小规模兼容同步 SDK，任务队列适合长任务，原生 async 适合高并发 I/O。
- **诚实边界：** 项目没有做系统并发压测，也没有请求取消或后台任务状态。

#### 26. 全局配置单例在并发和多租户场景有什么问题？

- **一句话回答：** `init_config` 会替换模块级共享实例，所有请求共用同一 LLM、Embedding、Vector DB 和 Agent，配置切换可能互相影响且无法提供租户隔离。
- **2～3 分钟深入回答：** `main.py` 导入时初始化一次；`/set-provider-config/` 修改同一 `config` 后重新装配 globals。并发请求可能在切换前后拿到不同组合，客户端线程安全也未声明；多 worker 又各有独立副本，配置状态不一致。生产应把不可变客户端放应用生命周期，把租户/请求配置显式传递，并通过版本化配置、锁或重建流程保证一致性。
- **连续追问：** 加锁够吗？多进程如何同步？连接池由谁关闭？
- **项目证据：** `configuration.py` 的 global 声明与 `init_config`；`main.py::set_provider_config`。
- **取舍与替代：** 全局单例适合本地单用户、减少重复连接；DI 和 registry 提升隔离但更复杂。
- **诚实边界：** 当前不能声称支持多租户或无损在线切模。

#### 27. 超时、错误映射和重试应该怎样设计？

- **一句话回答：** 要区分输入错误、上游不可用、上游坏响应和内部错误，设置分层超时与有条件重试，并用 request ID 保留因果链。
- **2～3 分钟深入回答：** 代理验证失败返回 400，连接后端失败返回 503，后端非成功或不可解析响应映射 502；查询/入库上游超时是 180 秒。后端目前常把异常统一成 500。改进时需配置连接/读取/总预算，不对非幂等入库盲重试；对 429/暂时性 5xx 用指数退避与抖动，并结合熔断、取消和可观测日志。
- **连续追问：** 504 在哪里返回？用户取消后模型调用会停吗？入库如何保证幂等？
- **项目证据：** `frontend/server.py` 的 httpx 和 `HTTPException`；`main.py` 的异常包装。
- **取舍与替代：** 统一错误信息保护内部细节，但需在受控日志保留根因；重试提高可用性也可能放大流量。
- **诚实边界：** 当前没有自动重试、熔断、请求 ID 或幂等键。

#### 28. 如何防止 API Key、临时路径和 metadata 泄漏？

- **一句话回答：** 密钥只放环境变量/密钥服务，浏览器只访问代理；Trace 和日志采用字段白名单，路径只保留文件名，URL 去查询参数，metadata 默认不返回。
- **2～3 分钟深入回答：** 模型适配器从 `OPENAI_API_KEY/OPENAI_BASE_URL` 读取；代理 `Path(filename).name` 去目录并使用自动清理的临时目录；`TraceCollector` 不序列化 embedding/metadata，把本地 reference 截成 basename、URL 移除 query。还应关闭异常详情外泄、对日志做结构化脱敏、限制文档正文、设置文件权限和保留期，并在 CI 做 secret scan。
- **连续追问：** URL fragment 会不会含秘密？文档正文中的 PII 怎么办？服务日志中的 Prompt 呢？
- **项目证据：** `embedding/openai_embedding.py`、`llm/openai_llm.py`、`frontend/server.py`、`trace.py` 及脱敏测试。
- **取舍与替代：** 全量 Trace 调试方便但风险高；生产采用最小字段、采样、加密和访问控制。
- **诚实边界：** 当前只覆盖部分路径/URL 规则，没有 DLP、密钥轮换、日志访问审计或文档 PII 检测。

#### 29. Milvus wrapper 吞掉异常返回空结果有什么问题？

- **一句话回答：** 它把“真的没有相关文档”和“数据库故障”混成空列表，可能让上层生成误导性的无结果答案并掩盖告警。
- **2～3 分钟深入回答：** `init_collection`、`insert_data`、`list_collections` 记录 critical 后不抛，`search_data` 记录后返回 `[]`。本地演示不易崩溃，但数据未写入也可能仍返回“成功”，可靠性差。应定义领域异常，写入失败必须失败；查询可按策略重试或降级，但响应要带可识别错误，日志附 trace ID，并对错误率告警。
- **连续追问：** 哪些异常可重试？空结果是否应该是 200？降级到关键词检索如何标记？
- **项目证据：** `deepsearcher/vector_db/milvus.py` 的多个 `except Exception`。
- **取舍与替代：** fail-fast 保证正确性，best-effort 提高可用性；RAG 对静默错误尤其危险，应优先可解释失败。
- **诚实边界：** 当前行为尚未改造，不能把日志记录说成完整容错。

#### 30. 为什么当前 Trace 非流式，如何升级为 SSE？

- **一句话回答：** 当前 `query_with_trace` 等 Agent 全部完成后一次 `build()` 返回 JSON；升级需把 Collector 变成事件发布器，后端和代理逐事件转发，前端增量归并。
- **2～3 分钟深入回答：** 现有方法调用简单、兼容旧 `/query/`，但用户要等最终结果才看到过程。SSE 适合服务端单向事件：定义 `routing`、`iteration_started`、`documents`、`reflection`、`final`、`error`，带 `trace_id/event_id`；FastAPI 用 streaming response，代理保持流不缓冲，React 用 EventSource/fetch stream 更新状态。还要处理顺序、心跳、断线重连、取消与最终一致性。
- **连续追问：** GET SSE 如何携带长问题？鉴权 token 放哪里？断线后是否重放？
- **项目证据：** `online_query.py::query_with_trace`、`trace.py::build`、代理 `/api/query` 当前都返回完整 JSON。
- **取舍与替代：** SSE 比 WebSocket 简单且适合单向事件；WebSocket 适合双向控制；轮询最兼容但延迟与负载较高。
- **诚实边界：** SSE 只是路线图，当前没有流式接口、事件持久化或断线恢复。

#### 31. 如果流量扩大十倍，如何改造？

- **一句话回答：** 先测瓶颈，再把请求入口、长任务、外部模型、向量库和可观测性分别做限流、异步化、缓存、扩容与隔离，而不是只加进程。
- **2～3 分钟深入回答：** 入口加鉴权、配额、body 限制和背压；查询服务尽量无状态，配置版本化，使用连接池和 worker；入库进入任务队列并幂等；对子查询限制并发和 Token 预算；Embedding/路由结果可缓存；Milvus 做容量/索引/副本规划；全链路记录 p95、错误率、模型 429、队列深度。先压测找出模型限额、线程池或 Milvus哪个先饱和。
- **连续追问：** 缓存 key 包含什么？如何避免租户串数据？模型供应商限流时如何降级？
- **项目证据：** 当前瓶颈入口分别在 `frontend/server.py`、`main.py`、`configuration.py` 和各 Agent 多次 LLM 调用。
- **取舍与替代：** 扩 worker 简单但会复制客户端并受外部限额；队列提高韧性但引入最终一致和运维。
- **诚实边界：** 当前没有负载测试结论和生产流量，因此方案必须表述为基于测量的设计，而非已经验证。

### 3.5 React 与全栈数据流（5 题）

#### 32. React 页面中的状态如何流动？

- **一句话回答：** `App` 持有健康、入库、查询、答案、Trace 和日志状态，用户事件调用 API，异步结果再单向下传给流程组件与 `TracePanel`。
- **2～3 分钟深入回答：** 输入是受控 state；查询开始时设置 loading 并清理旧结果，成功后写 answer/latency/tokens/trace，失败写 error/log。`TracePanel` 接收 trace 与 logs，通过自己的 tab/accordion 状态控制展示。这样的状态提升让数据源唯一、测试容易；但 state 多时会出现不一致组合和竞态，可用 reducer/状态机把 idle/loading/success/error 约束成有限状态。
- **连续追问：** 连点两次查询谁覆盖谁？组件卸载后请求怎么办？哪些值应 `useMemo`？
- **项目证据：** `frontend/src/App.jsx`、`TracePanel.jsx`；`App.test.jsx` 验证真实响应进入 UI。
- **取舍与替代：** `useState` 适合当前规模；`useReducer`/XState 管复杂流程；React Query 管服务端缓存。
- **诚实边界：** 当前没有 AbortController、请求序号去陈旧响应或跨页面状态持久化。

#### 33. API 适配层为什么要规范化字段和错误？

- **一句话回答：** 它把后端 snake_case 与 HTTP 细节转换成 UI 稳定的数据模型，避免组件到处解析响应和错误。
- **2～3 分钟深入回答：** `queryDeepSearcher` 接收 `{result, consume_token, latency_ms, trace}`，返回 `{answer, totalTokens, latencyMs, trace}`；非 2xx 尝试读取 `detail` 作为中文错误。组件只处理领域字段，不依赖每个接口的命名。版本升级时可在适配层兼容，但不能静默吞缺失字段，关键 schema 应验证。
- **连续追问：** 为什么不让后端直接返回 camelCase？运行时 schema 如何校验？错误 detail 是否可信？
- **项目证据：** `frontend/src/api.js` 与 `api.test.js`。
- **取舍与替代：** OpenAPI 生成客户端减少手写偏差；适配层更灵活但需要维护测试。
- **诚实边界：** 当前是 JavaScript，没有 TypeScript/运行时 schema，对错误 payload 的结构假设仍较弱。

#### 34. 标签页如何做到无障碍？当前完成了吗？

- **一句话回答：** 要同时具备正确角色/关联、选中状态、焦点管理和方向键/Home/End 键盘行为；当前已有部分 ARIA 与点击测试，但不能宣称完整达标。
- **2～3 分钟深入回答：** WAI-ARIA Tabs 要求 `tablist` 包含 `tab`，每个 tab 用 `aria-controls` 关联 `tabpanel`，选中项 `aria-selected=true` 且进入 tab 顺序；焦点应支持左右方向键循环。当前 TracePanel 使用 tab/tabpanel 角色并可点击切换，测试覆盖点击和清空日志，但需逐项核对 ID 关联、roving tabindex 和键盘操作。
- **连续追问：** 自动激活还是手动激活？隐藏 panel 用什么属性？屏幕阅读器如何验证？
- **项目证据：** `TracePanel.jsx`、`TracePanel.test.jsx`；规范：[W3C Tabs](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/)。
- **取舍与替代：** 使用成熟 headless 组件可减少错误，但增加依赖；自研需完整测试。
- **诚实边界：** 有 ARIA 属性不等于通过 WCAG，也没有当前屏幕阅读器测试报告。

#### 35. 当前前后端测试分别证明了什么？

- **一句话回答：** Python 测试证明 Trace schema、API 兼容、上传校验和代理映射；Vitest 证明关键数据能渲染和交互，但它们不证明真实模型、Milvus和浏览器端到端稳定。
- **2～3 分钟深入回答：** `tests/test_trace.py` 验证版本、Token、截断与脱敏；`test_query_api.py` 验证 `include_trace` 不破坏旧响应；`frontend/tests/test_server.py` 验证 PDF/Collection/代理；React 测试 mock API 验证答案、Trace、标签交互。还需要真实服务集成测试、浏览器 E2E、并发、性能、安全和 RAG 黄金集。
- **连续追问：** Mock 会掩盖什么？哪些测试最值得加？全量测试失败如何汇报？
- **项目证据：** 上述测试文件和实际命令输出。
- **取舍与替代：** 单测快且定位准，E2E 真实但慢；采用少量关键 E2E 加大量单测。
- **诚实边界：** 只能报告本次运行的真实通过数；已知基线失败不能隐瞒或归因给自己的改动而无证据。

#### 36. 从点击“运行查询”到显示 Trace，完整数据流是什么？

- **一句话回答：** React 发 POST `/api/query`，本地代理转 GET `/query/?include_trace=true`，FastAPI 调 `query_with_trace`，RAG 显式记录事件，完整 JSON 再经适配层进入 `TracePanel`。
- **2～3 分钟深入回答：** `App` 调 `queryDeepSearcher(question,maxIter)`；代理校验问题和轮数、计时并用 async httpx 调 8500；`main.perform_query` 分支到 `query_with_trace`；Collector 随 kwargs 传入 RAGRouter/ChainOfRAG，最终 `build` 返回 trace；代理保留它并加 `latency_ms`；API 适配字段；App 更新 state，TracePanel 按轮渲染。任一层失败都要清理 loading 并显示可理解错误。
- **连续追问：** 为什么后端查询是 GET 而浏览器代理是 POST？Trace 为空时 UI 如何退化？延迟从哪里开始计？
- **项目证据：** `App.jsx` → `api.js` → `frontend/server.py` → `main.py` → `online_query.py` → `trace.py`，相关测试覆盖每个契约边界。
- **取舍与替代：** 当前完整响应实现简单；SSE 可改善首屏反馈但协议、状态与重连复杂度更高。
- **诚实边界：** `latency_ms` 是代理等待后端的近似端到端时间，不含用户网络之外的统一分段指标；Trace 也不是所有 Agent 都有完整轮次。

## 4. 项目表达与实战材料

### 4.1 一分钟项目介绍模板

> 我基于 DeepSearcher 做了一个中文 AI 全栈学习控制台。离线侧把 PDF 解析、按 1500/100 字符切分、用千问 `text-embedding-v4` 生成 1024 维向量并写入 Docker Milvus；在线侧由 `RAGRouter` 在 `DeepSearch` 和 `ChainOfRAG` 间选择，再路由 Collection、迭代检索并由 `qwen-plus` 生成回答。为了让流程可理解，我补了结构化 `TraceCollector` 和 React `TracePanel`，展示 Agent、子查询、命中文档、支持性筛选、轮次和 Token，同时清理本地路径、URL 参数和 metadata。项目已经在本地跑通入库、查询和定向测试；目前仍是非流式、全局单例，且缺少正式 RAG 评测、鉴权限流和多租户。我下一步会先补黄金评测集，再做路由校验、SSE 和生产安全。

这段话要说到四件事：**做了什么、链路是什么、你贡献了什么、哪些还没做**。不要把“学习控制台”包装成生产平台。

### 4.2 三分钟架构讲解模板

1. **问题（20 秒）：** 普通聊天看不到资料如何进入向量库，也看不到复杂问题为什么要多轮检索；学习和排错都困难。
2. **离线入库（35 秒）：** `PDFLoader` 提取文本，Recursive splitter 生成 Chunk 与 `wider_text`，Embedding 输出 1024 维向量，Milvus 按 Collection 保存 text/reference/metadata/embedding。说明 1500/100 是待评测默认值。
3. **在线 Agent（55 秒）：** `RAGRouter` 选 `DeepSearch` 或 `ChainOfRAG`；CollectionRouter 选语料；复杂 Agent 通过子查询/中间答案补信息，再筛支持文档，最后用千问总结。主动指出非法索引与 LLM judge 风险。
4. **全栈数据流（35 秒）：** React → 本地 BFF → FastAPI → 千问/Milvus；BFF 负责 Base64 PDF 校验、临时路径、超时和错误映射。
5. **可观测性（20 秒）：** Trace 记录显式事件，不是思维链；最多展示 5 条文档、每条 600 字，并清理 reference，API 用 `include_trace` 保持兼容。
6. **验证与取舍（15 秒）：** 报告最近一次真实定向测试和演示结果；承认没有正式质量评测、SSE、鉴权、限流、多租户，给出优先级。

### 4.3 十分钟白板讲解提纲

```text
0:00-1:00  用户问题、目标、非目标
1:00-2:30  离线入库：Loader → Chunk → Embedding → Milvus
2:30-4:30  在线查询：RAGRouter → Agent → Collection → Retrieval → Answer
4:30-5:30  NaiveRAG / DeepSearch / ChainOfRAG 的控制流与成本差异
5:30-6:30  React → BFF → FastAPI 的接口与错误边界
6:30-7:30  Trace schema、脱敏、为什么不是思维链
7:30-8:30  现场证据：源码、测试、一次真实回答、Token/延迟
8:30-9:20  三个技术债：评测、全局单例、静默异常/路由校验
9:20-10:00 路线：黄金集 → hybrid/rerank → 引用 → SSE → 安全与多租户
```

白板至少画出两个反馈环：ChainOfRAG 的“中间回答 → 下一子查询”和生产系统的“指标 → 参数/路由策略”。每画一个组件，都要能回答“谁调用它、失败后谁负责、怎么验证”。

### 4.4 两条不夸大的简历项目描述

- 基于 Python/FastAPI、React 19 与 Milvus 搭建 DeepSearcher 中文学习控制台，跑通 PDF 解析、Chunk、千问 Embedding、向量检索、多策略 RAG 路由和答案展示的端到端链路；通过本地 BFF 完成 PDF 校验、临时文件转发、超时与中文错误映射。
- 为 `ChainOfRAG` 增加版本化结构 Trace，在不暴露隐藏思维链的前提下展示 Agent、子查询、Collection、命中文档、支持性筛选和 Token；为路径/URL/metadata 脱敏、API 兼容与 React 交互编写定向测试，并明确评测、SSE、鉴权和多租户仍属后续工作。

禁止写“打造高并发生产级平台”“准确率提升 30%”“完整复现 CoRAG”等没有证据的句子。

### 4.5 端到端演示检查表

#### 演示前

- [ ] `git status --short` 已查看，知道哪些是自己的改动，不展示任何密钥。
- [ ] `.env` 未投屏；终端历史不含 API Key、token 或敏感 URL。
- [ ] `docker compose -f infra/milvus/docker-compose.yml ps` 显示服务正常。
- [ ] 19530、8500、8600 可访问，`/api/health` 状态符合实际。
- [ ] Collection 内已有非敏感演示 PDF，或准备好 20 MiB 以下的有效 PDF。
- [ ] 千问账号有额度；准备一条事实题和一条多跳题。
- [ ] 定向 Python 测试、前端测试、`mkdocs build` 的最新结果已保存。
- [ ] 准备“模型服务不可用”时的截图/录屏备用，不现场硬等。

#### 演示中

- [ ] 先用 20 秒说目标，再展示离线入库四步。
- [ ] 显示当前 `qwen-plus`、`text-embedding-v4`、Collection 配置，不展示 Key。
- [ ] 发起查询，解释路由、子查询、检索、支持文档与最终回答。
- [ ] 主动说明 L2 score 的方向，避免称为统一相似度。
- [ ] 打开 TracePanel，说明它是结构化事件，不是隐藏思维链。
- [ ] 展示一个测试作为证据，不只展示“页面看起来能用”。

#### 演示后

- [ ] 用 30 秒总结质量—延迟—成本取舍。
- [ ] 主动说出三个未实现项和优先级。
- [ ] 若结果错误，沿“路由 → Collection → 检索 → 支持性 → 生成”定位，不把一切归咎于模型。

### 4.6 面试时不能说错的事实清单

- [ ] 默认 LLM 是 `qwen-plus`，通过 `OpenAI` 兼容适配器调用；provider 名不是 `Aliyun`。
- [ ] 默认 Embedding 是 `text-embedding-v4`，配置维度为 1024。
- [ ] 当前 Milvus 是 Docker 服务 URI `http://127.0.0.1:19530`，不是 Milvus Lite。
- [ ] 默认 dense metric 是 L2；`score` 承接的是 Milvus `distance`。
- [ ] 默认路由候选是 `DeepSearch`、`ChainOfRAG`；`NaiveRAG` 不在默认候选列表。
- [ ] `query()` 返回三元组；`query_with_trace()` 返回四元组；`include_trace` 默认 `False` 保持旧接口。
- [ ] `ChainOfRAG.early_stopping` 默认 `False`；`null` 表示该判断未执行，不是“信息不足”。
- [ ] `DeepSearch` 有异步并发子查询，但同步 `retrieve()` 用 `asyncio.run` 包装。
- [ ] 项目受 CoRAG 启发，没有实现论文训练和拒绝采样流程。
- [ ] Trace 是应用事件，不是模型内部思维链；当前主要覆盖 ChainOfRAG 逐轮过程。
- [ ] Trace 最多展示前 5 条检索文档、每条最多 600 字；不会返回 embedding/metadata。
- [ ] 代理上传用 Base64 + 临时目录，不是浏览器直接把本机路径交给 8500。
- [ ] 当前无鉴权、限流、缓存、多租户、正式 SLO、SSE 和 RAG 黄金评测。
- [ ] Milvus 层部分异常只记录日志或返回空列表，这是技术债，不是完善容错。
- [ ] 测试通过数必须引用面试前的真实输出；不能说“全量通过”除非刚刚验证过。

### 4.7 后续优化路线

| 阶段 | 目标 | 具体动作 | 验收指标 | 主要取舍 |
| --- | --- | --- | --- | --- |
| P0 评测 | 建立改造基线 | 20～100 题黄金集；固定 Collection/模型；测 Recall@k、faithfulness、正确率、p95、Token | 报告可重复，参数变更可对比 | 标注成本、LLM judge 偏差 |
| P0 正确性 | 失败可见 | 路由索引/Collection 白名单；Milvus 领域异常；幂等入库；统一错误码 | 故障不再伪装成空结果；错误有 trace ID | 可用性与 fail-fast 平衡 |
| P1 检索 | 提升长尾召回 | 对照 dense、BM25+RRF、reranker、chunk 网格实验 | 在预算内提升 Recall@k/正确率 | 索引、延迟、Token 上升 |
| P1 引用 | 回答可核验 | 保存 chunk/source/page，句子级引用对齐，前端可展开证据 | 引用 precision、无来源陈述率 | 解析和 UI 复杂度 |
| P1 SSE | 降低等待感 | 定义版本化事件；Collector 发布；FastAPI/BFF 流式转发；React 增量状态 | 首事件时间、断线恢复、顺序正确 | 协议与状态复杂度 |
| P0 安全 | 控制暴露面 | 身份认证、RBAC、租户 Collection ACL、密钥服务、审计、DLP | 越权测试为 0；密钥不进入客户端/日志 | 开发与运维成本 |
| P1 稳定性 | 抗突发流量 | 分层超时、限流、重试/熔断、并发预算、后台入库队列 | 错误率/p95 达到 SLO | 排队与最终一致 |
| P2 效率 | 降成本 | Embedding/路由缓存、问题去重、模型分层、Token 预算 | 单问成本下降且质量不退化 | 缓存失效与数据隔离 |
| P2 多租户 | 隔离配置与数据 | 请求级 tenant context、配置版本、Collection/DB 隔离、配额 | 并发租户无串配/串数 | 架构复杂度上升 |

## 5. 自我反问与优化

### 5.1 四轮反向审查记录

#### 第一轮：RAG 面试官

追问“为什么拆查询一定更好”“1500/100 的依据是什么”“支持文档由同一 LLM 判断是否自证”“没有黄金集凭什么说效果”。审查结论：所有“提升质量”改写为“设计意图或待验证假设”，增加 Recall@k、faithfulness、对照组和固定数据快照；明确 Chunk 默认值不是最佳实践结论。

#### 第二轮：后端面试官

追问“同步模型调用是否堵线程”“180 秒超时是否拖垮 worker”“全局配置切换是否线程安全”“Milvus 故障为什么变空结果”。审查结论：把 `def`/`async def`、线程池、任务队列和应用生命周期分开；将静默异常列为高优先级正确性债务，不再把日志当成容错。

#### 第三轮：前端面试官

追问“为什么不用 multipart”“连续点击请求是否竞态”“有 ARIA role 是否等于无障碍”“mock 测试证明了什么”。审查结论：明确 BFF 是兼容现有路径 API 的实现取舍；补充 AbortController/状态机路线；将标签页描述从“无障碍完成”改为“具备部分语义、仍需键盘与读屏验证”。

#### 第四轮：质疑型面试官

追问“是不是包装上游项目”“Trace 是不是泄漏思维链”“CoRAG 是否只是改名”“测试是否全绿”“生产能力有何证据”。审查结论：把个人贡献限定为本地基线、中文控制台与结构化 Trace；写清 CoRAG 训练未实现、Trace 非流式/非思维链、全量测试存在基线问题；所有性能和准确率数字必须来自当场可展示的实验。

### 5.2 自我拷问后的优化记录（12 组）

下表中的“优化版本”都要同时回答：**是什么、为什么、怎么做、取舍、如何验证**。

| # | 薄弱回答 | 面试官会攻击什么 | 优化版本 |
| --- | --- | --- | --- |
| 1 | “我们用了 Agent，所以比 RAG 更智能。” | 没有定义、因果与指标。 | **是什么：** 默认在 DeepSearch/ChainOfRAG 间路由。**为什么：** 多跳问题一次召回可能不全。**怎么做：** 子查询、迭代检索、支持性筛选。**取舍：** 增加延迟、Token 和误差传播。**验证：** 与 NaiveRAG 在固定黄金集比较 Recall@k、正确率、p95、Token；目前尚无该正式结论。 |
| 2 | “Chunk 设 1500 是经验最佳值。” | 把默认值包装成最优。 | **是什么：** 当前按字符 1500、重叠 100，并保存约 300 字符窗口。**为什么：** 平衡语义完整和召回粒度。**怎么做：** 用 500/1500/3000 网格实验。**取舍：** 小块碎、大块噪声多。**验证：** 看 Recall@k、答案正确率、向量数与 Token；当前只能称经验默认。 |
| 3 | “score 越高越相似。” | 当前 L2 恰可能相反。 | **是什么：** `score` 承接 Milvus `distance`，默认 metric 是 L2。**为什么：** 通用字段名隐藏了度量语义。**怎么做：** 返回 metric/value/direction 或 UI 标“L2 距离”。**取舍：** 改契约涉及兼容。**验证：** 构造相同/近义/无关向量检查排序，不能跨 metric 比原始数值。 |
| 4 | “我们复现了 CoRAG。” | 项目没有训练与拒绝采样。 | **是什么：** 仓库实现推理时逐步改写查询和检索。**为什么：** 受 CoRAG 的逐步检索思想启发。**怎么做：** Prompt 生成 follow-up、中间答案与支持文档。**取舍：** 无需训练但稳定性依赖通用 LLM。**验证：** 对照论文方法章节和源码；明确训练/拒绝采样未实现。 |
| 5 | “Trace 展示了模型思考过程。” | 安全与概念都错误。 | **是什么：** Trace 是程序显式记录的路由、查询、文档和统计事件。**为什么：** 可调试且不依赖隐藏推理。**怎么做：** Collector 埋点、截断、路径/URL 脱敏、API 版本化。**取舍：** 看不到内部推理但边界更安全稳定。**验证：** `tests/test_trace.py` 证明字段白名单与脱敏。 |
| 6 | “FastAPI 用 async，所以并发没问题。” | 忽略同步 SDK、线程池和长任务。 | **是什么：** 主后端路由是同步 `def`，BFF 是 async httpx。**为什么：** SDK 大多同步而代理 I/O 可 await。**怎么做：** 阻塞调用进线程池/任务队列，设置并发预算。**取舍：** 线程兼容快但容量有限。**验证：** 并发压测看 p95、线程/队列、429 和错误率；当前未证明高并发。 |
| 7 | “有异常处理，所以系统很稳定。” | Milvus 异常被吞导致假空结果。 | **是什么：** wrapper 捕获异常并记录日志，搜索返回空列表。**为什么：** 本地演示避免崩溃。**怎么做：** 领域异常、可重试分类、错误码、trace ID。**取舍：** fail-fast 会降低表面可用性但提高正确性。**验证：** 注入连接/写入故障，断言不会返回成功或普通空结果。 |
| 8 | “代理只是为了跨域。” | 忽略上传路径和安全价值。 | **是什么：** 它是本地 BFF。**为什么：** 浏览器不能给后端有效本地路径，也不应接触 Key/上游地址。**怎么做：** PDF 校验、临时落盘、转发、错误映射。**取舍：** 多一跳和 Base64 膨胀。**验证：** 非 PDF、超限、非法 Collection、8500 离线测试。 |
| 9 | “项目支持多租户。” | 没有身份、ACL、请求级配置。 | **是什么：** 当前是单用户本地控制台。**为什么：** 全局实例和默认 Collection 简化学习。**怎么做：** 若升级则引入 tenant context、认证、Collection ACL、配额。**取舍：** 隔离提高安全也增加运维。**验证：** 并发双租户越权测试；当前不能声称已支持。 |
| 10 | “测试都通过了。” | 全量测试有已知基线失败且 mock 不代表 E2E。 | **是什么：** 定向 Trace/API/Agent/前端测试可验证指定契约。**为什么：** 快速定位个人改动。**怎么做：** 面试前重新运行并保存输出，基线失败单独分类。**取舍：** 单测快但不能替代真实模型/Milvus E2E。**验证：** 只报告命令当次的真实通过/失败数。 |
| 11 | “打开 early stopping 就会更快。” | 忽略误判与额外判断调用。 | **是什么：** 每轮多一次信息充足判断，可能提前 break。**为什么：** 避免无效后续检索。**怎么做：** 开/关对照并记录停止轮数。**取舍：** 多一次 Token 且 false positive 会漏证据。**验证：** 看正确率、平均轮数、Token、提前停止错误率；默认关闭时 reflection 为空是预期。 |
| 12 | “十倍流量就加十个 worker。” | 外部模型限额、单例、Milvus、入库任务都未解决。 | **是什么：** 十倍流量是端到端容量问题。**为什么：** 最慢依赖和配额决定吞吐。**怎么做：** 先压测，再限流/队列/缓存/连接池/水平扩容/租户隔离。**取舍：** 队列和缓存引入一致性复杂度。**验证：** 用目标并发测 p95、错误率、429、队列深度和成本。 |

### 5.3 最终回答自检卡

每次模拟面试抽 10 题，给自己的回答逐项打勾：

- [ ] **是什么：** 先定义对象和边界，没有只报术语。
- [ ] **为什么：** 至少一条明确因果链，而不是“大家都这样做”。
- [ ] **怎么做：** 能说出真实调用顺序、关键参数或失败路径。
- [ ] **取舍：** 同时说收益、成本和至少一个替代方案。
- [ ] **如何验证：** 给出指标、测试、实验数据或可执行命令。
- [ ] **项目证据：** 能定位至少一个源码文件或测试文件。
- [ ] **诚实边界：** 清楚区分已实现、部分实现、计划实现。

任意一项未勾选，就把答案退回重写。尤其警惕“用了先进技术”“提高了准确率”“支持高并发”“生产级”这类没有可验证对象的表达。

## 6. 权威资料与延伸阅读

- [CoRAG: Chain-of-Thought Enhanced Retrieval-Augmented Generation](https://arxiv.org/abs/2501.14342)：用于区分论文训练方法与本项目的推理时实现。
- [Milvus Multi-Vector Hybrid Search](https://milvus.io/docs/multi-vector-search.md)：理解 dense、sparse、hybrid search 与 ranker。
- [FastAPI 并发与 async/await](https://fastapi.tiangolo.com/async/)：判断何时使用 `def`、`async def` 和线程池。
- [React：State as a Component's Memory](https://react.dev/learn/state-a-components-memory)：理解 state snapshot 与单向状态流。
- [WAI-ARIA Tabs Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/)：核对标签页角色、焦点与键盘交互。

## 7. 四周完成验收

- [ ] 24 个学习日均有目标、源码、理论、实验、成果、面试题和无答案自测标准。
- [ ] 能在 15 分钟内启动 Milvus、FastAPI 和前端，并独立完成入库与查询。
- [ ] 能不看文档画出离线、在线和全栈三条链路。
- [ ] 能分别完成 1 分钟、3 分钟和 10 分钟讲解，且三版事实一致。
- [ ] 36 道题任抽 10 道，均能给源码/测试证据、取舍和诚实边界。
- [ ] 完成至少 20 题的评测集设计，并跑过一次 Naive/Agent 或参数对照实验。
- [ ] 能准确解释 L2 distance、Embedding 维度、Milvus Lite/Docker、CoRAG 差异、early stopping 与 Trace 边界。
- [ ] 能说出三项最高优先级技术债，并按指标说明修复顺序。
- [ ] 最近一次定向测试、前端测试和 MkDocs 构建结果有时间戳，不引用过期数字。
- [ ] 简历和口述不包含未实现的生产能力或无法验证的提升比例。

完成标准不是“看完了”，而是：**你能演示、能画图、能定位源码、能承认边界，也能用指标说明下一步。**
