# Embedding 模型配置

DeepSearcher 支持多种 Embedding 模型，可将文本转换为向量表示，用于语义检索。

## 📝 基本配置

```python
config.set_provider_config("embedding", "(EmbeddingModelName)", "(Arguments dict)")
```

## 📋 可用的 Embedding Provider

| Provider | 说明 | 主要特点 |
|----------|------|----------|
| **OpenAIEmbedding** | OpenAI 文本嵌入模型 | 质量高，适合生产环境 |
| **MilvusEmbedding** | 通过 Pymilvus 使用内置嵌入模型 | 可选模型丰富 |
| **VoyageEmbedding** | VoyageAI 嵌入模型 | 针对搜索优化 |
| **BedrockEmbedding** | Amazon Bedrock 嵌入模型 | 集成 AWS |
| **GeminiEmbedding** | Google Gemini 嵌入模型 | 性能较高 |
| **GLMEmbedding** | ChatGLM 嵌入模型 | 支持中文 |
| **OllamaEmbedding** | 通过 Ollama 在本地运行 | 可自行托管 |
| **PPIOEmbedding** | PPIO 云端嵌入服务 | 易于扩展 |
| **SiliconflowEmbedding** | SiliconFlow 模型 | 企业级服务 |
| **VolcengineEmbedding** | 火山引擎嵌入模型 | 高吞吐量 |
| **NovitaEmbedding** | Novita AI 嵌入模型 | 成本较低 |
| **SentenceTransformerEmbedding** | Sentence Transformer 模型 | 可自行托管 |
| **IBM watsonx.ai** | 多种嵌入模型 | IBM 企业 AI 平台 |
| **JiekouAIEmbedding** | Jiekou.AI 嵌入模型 | 质量较高、成本较低 |

## 🔍 常用配置示例

### OpenAI Embedding

```python
config.set_provider_config("embedding", "OpenAIEmbedding", {"model": "text-embedding-3-small"})
```

需要设置环境变量 `OPENAI_API_KEY`。

### Milvus 内置 Embedding

```python
config.set_provider_config("embedding", "MilvusEmbedding", {"model": "BAAI/bge-base-en-v1.5"})
```

```python
config.set_provider_config("embedding", "MilvusEmbedding", {"model": "jina-embeddings-v3"})
```

使用 Jina 模型时，需要设置 `JINAAI_API_KEY`。

### VoyageAI Embedding

```python
config.set_provider_config("embedding", "VoyageEmbedding", {"model": "voyage-3"})
```

需要设置 `VOYAGE_API_KEY`，并执行 `pip install voyageai`。

## 📚 其他 Provider

??? example "Amazon Bedrock"

    ```python
    config.set_provider_config("embedding", "BedrockEmbedding", {"model": "amazon.titan-embed-text-v2:0"})
    ```

    需要设置 `AWS_ACCESS_KEY_ID` 和 `AWS_SECRET_ACCESS_KEY`，并执行 `pip install boto3`。

??? example "Novita AI"

    ```python
    config.set_provider_config("embedding", "NovitaEmbedding", {"model": "baai/bge-m3"})
    ```

    需要设置 `NOVITA_API_KEY`。

??? example "SiliconFlow"

    ```python
    config.set_provider_config("embedding", "SiliconflowEmbedding", {"model": "BAAI/bge-m3"})
    ```

    需要设置 `SILICONFLOW_API_KEY`。

??? example "火山引擎"

    ```python
    config.set_provider_config("embedding", "VolcengineEmbedding", {"model": "doubao-embedding-text-240515"})
    ```

    需要设置 `VOLCENGINE_API_KEY`。

??? example "GLM"

    ```python
    config.set_provider_config("embedding", "GLMEmbedding", {"model": "embedding-3"})
    ```

    需要设置 `GLM_API_KEY`，并执行 `pip install zhipuai`。

??? example "Google Gemini"

    ```python
    config.set_provider_config("embedding", "GeminiEmbedding", {"model": "text-embedding-004"})
    ```

    需要设置 `GEMINI_API_KEY`，并执行 `pip install google-genai`。

??? example "Ollama"

    ```python
    config.set_provider_config("embedding", "OllamaEmbedding", {"model": "bge-m3"})
    ```

    需要在本地安装 Ollama，并执行 `pip install ollama`。

??? example "PPIO"

    ```python
    config.set_provider_config("embedding", "PPIOEmbedding", {"model": "baai/bge-m3"})
    ```

    需要设置 `PPIO_API_KEY`。

??? example "SentenceTransformer"

    ```python
    config.set_provider_config("embedding", "SentenceTransformerEmbedding", {"model": "BAAI/bge-large-zh-v1.5"})
    ```

    需要执行 `pip install sentence-transformers`。

??? example "IBM WatsonX"

    ```python
    config.set_provider_config("embedding", "WatsonXEmbedding", {"model": "ibm/slate-125m-english-rtrvr-v2"})
    ```

    需要执行 `pip install ibm-watsonx-ai`。

??? example "Jiekou.AI"

    ```python
    config.set_provider_config("embedding", "JiekouAIEmbedding", {"model": "qwen/qwen3-embedding-8b"})
    ```

    需要设置 `JIEKOU_API_KEY`。
