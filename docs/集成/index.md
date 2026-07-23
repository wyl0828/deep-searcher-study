# 集成模块支持

DeepSearcher 支持多种集成模块，包括 Embedding 模型、大语言模型、文档加载器和向量数据库。

## 📊 概览

| 模块类型 | 数量 | 说明 |
|----------|------|------|
| [Embedding 模型](#embedding-models) | 7+ | 文本向量化工具 |
| [大语言模型](#llm-support) | 11+ | 查询处理与文本生成 |
| [文档加载器](#document-loader) | 5+ | 解析并处理多种格式的文档 |
| [向量数据库](#vector-database-support) | 2+ | 存储和检索向量数据 |

## 🔢 Embedding 模型 {#embedding-models}

使用不同的 Embedding 模型把文本转换为向量，用于语义检索。

| Provider | 必需的环境变量 | 特点 |
|----------|----------------|------|
| **[开源模型](https://milvus.io/docs/embeddings.md)** | 无 | 可在本地运行 |
| **[OpenAI](https://platform.openai.com/docs/guides/embeddings/use-cases)** | `OPENAI_API_KEY` | 质量高，使用简单 |
| **[VoyageAI](https://docs.voyageai.com/embeddings/)** | `VOYAGE_API_KEY` | 针对检索优化 |
| **[Amazon Bedrock](https://docs.aws.amazon.com/bedrock/)** | `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY` | 集成 AWS，适合企业环境 |
| **[FastEmbed](https://qdrant.github.io/fastembed/)** | 无 | 轻量且速度快 |
| **[PPIO](https://ppinfra.com/model-api/product/llm-api)** | `PPIO_API_KEY` | 灵活的云端服务 |
| **[Novita AI](https://novita.ai/docs/api-reference/model-apis-llm-create-embeddings)** | `NOVITA_API_KEY` | 模型选择丰富 |
| **[IBM watsonx.ai](https://www.ibm.com/products/watsonx-ai/foundation-models#ibmembedding)** | `WATSONX_APIKEY`、`WATSONX_URL`、`WATSONX_PROJECT_ID` | IBM 企业 AI 平台 |
| **[Jiekou.AI](https://jiekou.ai/?utm_source=github_deep-searcher)** | `JIEKOU_API_KEY` | Jiekou.AI 嵌入模型 |

## 🧠 大语言模型 {#llm-support}

使用不同的大语言模型处理查询并生成回答。

| Provider | 必需的环境变量 | 特点 |
|----------|----------------|------|
| **[OpenAI](https://platform.openai.com/docs/models)** | `OPENAI_API_KEY` | GPT 模型系列 |
| **[DeepSeek](https://api-docs.deepseek.com/)** | `DEEPSEEK_API_KEY` | 推理能力强 |
| **[XAI Grok](https://x.ai/blog/grok-3)** | `XAI_API_KEY` | 实时知识能力 |
| **[Anthropic Claude](https://docs.anthropic.com/en/home)** | `ANTHROPIC_API_KEY` | 长上下文理解能力出色 |
| **[SiliconFlow](https://docs.siliconflow.cn/en/userguide/introduction)** | `SILICONFLOW_API_KEY` | 企业推理服务 |
| **[PPIO](https://ppinfra.com/model-api/product/llm-api)** | `PPIO_API_KEY` | 支持多种模型 |
| **[TogetherAI](https://docs.together.ai/docs/introduction)** | `TOGETHER_API_KEY` | 开源模型选择广泛 |
| **[Google Gemini](https://ai.google.dev/gemini-api/docs)** | `GEMINI_API_KEY` | Google 多模态模型 |
| **[SambaNova](https://docs.together.ai/docs/introduction)** | `SAMBANOVA_API_KEY` | 高性能 AI 平台 |
| **[Ollama](https://ollama.com/)** | 无 | 本地部署大语言模型 |
| **[Novita AI](https://novita.ai/docs/guides/introduction)** | `NOVITA_API_KEY` | 多种 AI 服务 |
| **[IBM watsonx.ai](https://www.ibm.com/products/watsonx-ai/foundation-models#ibmfm)** | `WATSONX_APIKEY`、`WATSONX_URL`、`WATSONX_PROJECT_ID` | IBM 企业 AI 平台 |
| **[Jiekou.AI](https://jiekou.ai/?utm_source=github_deep-searcher)** | `JIEKOU_API_KEY` | 提供开源和闭源模型 |

## 📄 文档加载器 {#document-loader}

支持从多种来源加载和处理文档。

### 本地文件加载器

| 加载器 | 支持格式 | 必需的环境变量 |
|--------|----------|----------------|
| **内置加载器** | PDF、TXT、MD | 无 |
| **[Unstructured](https://unstructured.io/)** | 多种文档格式 | `UNSTRUCTURED_API_KEY`、`UNSTRUCTURED_URL`（可选） |

### 网页抓取器

| 抓取器 | 说明 | 必需的环境变量或配置 |
|--------|------|----------------------|
| **[FireCrawl](https://docs.firecrawl.dev/introduction)** | 面向 AI 应用的抓取器 | `FIRECRAWL_API_KEY` |
| **[Jina Reader](https://jina.ai/reader/)** | 高精度网页内容提取 | `JINA_API_TOKEN` |
| **[Crawl4AI](https://docs.crawl4ai.com/)** | 浏览器自动化抓取器 | 首次使用前执行 `crawl4ai-setup` |

## 💾 向量数据库 {#vector-database-support}

使用向量数据库高效存储和检索 Embedding 数据。

| 数据库 | 说明 | 特点 |
|--------|------|------|
| **[Milvus](https://milvus.io/)** | 开源向量数据库 | 高性能、易扩展 |
| **[Zilliz Cloud](https://www.zilliz.com/)** | 托管式 Milvus 服务 | 无需自行维护 |
| **[Qdrant](https://qdrant.tech/)** | 向量相似度搜索引擎 | 简单高效 |
