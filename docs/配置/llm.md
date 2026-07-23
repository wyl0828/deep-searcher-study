# 大语言模型（LLM）配置

DeepSearcher 支持多种大语言模型，用于处理查询和生成回答。

## 📝 基本配置

```python
config.set_provider_config("llm", "(LLMName)", "(Arguments dict)")
```

## 📋 可用的 LLM Provider

| Provider | 说明 | 代表模型 |
|----------|------|----------|
| **OpenAI** | OpenAI GPT 模型 API | o1-mini、GPT-4 |
| **DeepSeek** | DeepSeek 模型服务 | deepseek-reasoner、coder |
| **Anthropic** | Anthropic Claude 模型 | claude-sonnet-4-0 |
| **Gemini** | Google Gemini 模型 | gemini-1.5-pro、gemini-2.0-flash |
| **XAI** | X.AI Grok 模型 | grok-2-latest |
| **Ollama** | 本地部署大语言模型 | llama3、qwq 等 |
| **SiliconFlow** | 企业 AI 推理平台 | deepseek-r1 |
| **TogetherAI** | 提供多种模型 | llama-4、deepseek |
| **PPIO** | 云端 AI 基础设施 | deepseek、llama |
| **Volcengine** | 火山引擎大模型平台 | deepseek-r1 |
| **GLM** | ChatGLM 模型 | glm-4-plus |
| **Bedrock** | Amazon Bedrock 大模型 | anthropic.claude、ai21.j2 |
| **Novita** | Novita AI 模型 | 多种模型 |
| **IBM watsonx.ai** | IBM 企业 AI 平台 | 多种模型 |
| **Jiekou.AI** | Jiekou.AI 模型服务 | Claude、OpenAI、DeepSeek、Grok、Qwen 等 |

## 🔍 常用配置示例

### OpenAI

```python
config.set_provider_config("llm", "OpenAI", {"model": "o1-mini"})
```

需要设置 `OPENAI_API_KEY`。

### DeepSeek

```python
config.set_provider_config("llm", "DeepSeek", {"model": "deepseek-reasoner"})
```

需要设置 `DEEPSEEK_API_KEY`。

### IBM WatsonX

```python
config.set_provider_config("llm", "WatsonX", {"model": "ibm/granite-3-3-8b-instruct"})
```

需要设置 `WATSONX_APIKEY`、`WATSONX_URL` 和 `WATSONX_PROJECT_ID`。

## 📚 其他 Provider

??? example "通过 SiliconFlow 使用 DeepSeek"

    ```python
    config.set_provider_config("llm", "SiliconFlow", {"model": "deepseek-ai/DeepSeek-R1"})
    ```

    需要设置 `SILICONFLOW_API_KEY`。详情参阅 [SiliconFlow 文档](https://docs.siliconflow.cn/quickstart)。

??? example "通过 TogetherAI 使用 DeepSeek 或 Llama"

    需要设置 `TOGETHER_API_KEY`，并执行 `pip install together`。

    DeepSeek R1：

    ```python
    config.set_provider_config("llm", "TogetherAI", {"model": "deepseek-ai/DeepSeek-R1"})
    ```

    Llama 4：

    ```python
    config.set_provider_config("llm", "TogetherAI", {"model": "meta-llama/Llama-4-Scout-17B-16E-Instruct"})
    ```

    详情参阅 [TogetherAI 官网](https://www.together.ai/)。

??? example "XAI Grok"

    ```python
    config.set_provider_config("llm", "XAI", {"model": "grok-2-latest"})
    ```

    需要设置 `XAI_API_KEY`。详情参阅 [XAI Grok 文档](https://docs.x.ai/docs/overview#featured-models)。

??? example "Claude"

    ```python
    config.set_provider_config("llm", "Anthropic", {"model": "claude-sonnet-4-0"})
    ```

    需要设置 `ANTHROPIC_API_KEY`。详情参阅 [Anthropic Claude 文档](https://docs.anthropic.com/en/home)。

??? example "Google Gemini"

    ```python
    config.set_provider_config("llm", "Gemini", {"model": "gemini-2.0-flash"})
    ```

    需要设置 `GEMINI_API_KEY`，并执行 `pip install google-genai`。详情参阅 [Gemini API 文档](https://ai.google.dev/gemini-api/docs)。

??? example "通过 PPIO 使用 DeepSeek"

    ```python
    config.set_provider_config("llm", "PPIO", {"model": "deepseek/deepseek-r1-turbo"})
    ```

    需要设置 `PPIO_API_KEY`。详情参阅 [PPIO 文档](https://ppinfra.com/docs/get-started/quickstart.html)。

??? example "Ollama"

    ```python
    config.set_provider_config("llm", "Ollama", {"model": "qwq"})
    ```

    按照 [Ollama 说明](https://github.com/jmorganca/ollama)配置本地实例：

    1. [下载](https://ollama.ai/download)并安装 Ollama
    2. 在[模型库](https://ollama.ai/library)中选择模型
    3. 执行 `ollama pull <name-of-model>` 下载模型
    4. Ollama 默认 REST API 地址为 [http://localhost:11434](http://localhost:11434)

??? example "火山引擎"

    ```python
    config.set_provider_config("llm", "Volcengine", {"model": "deepseek-r1-250120"})
    ```

    需要设置 `VOLCENGINE_API_KEY`。详情参阅 [火山引擎文档](https://www.volcengine.com/docs/82379/1099455)。

??? example "GLM"

    ```python
    config.set_provider_config("llm", "GLM", {"model": "glm-4-plus"})
    ```

    需要设置 `GLM_API_KEY`，并执行 `pip install zhipuai`。详情参阅 [GLM 文档](https://bigmodel.cn/dev/welcome)。

??? example "Amazon Bedrock"

    ```python
    config.set_provider_config("llm", "Bedrock", {"model": "us.deepseek.r1-v1:0"})
    ```

    需要设置 `AWS_ACCESS_KEY_ID` 和 `AWS_SECRET_ACCESS_KEY`，并执行 `pip install boto3`。详情参阅 [Amazon Bedrock 文档](https://docs.aws.amazon.com/bedrock/)。

??? example "阿里云百炼"

    ```python
    config.set_provider_config("llm", "OpenAI", {
        "model": "deepseek-r1",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
    })
    ```

    需要设置 `OPENAI_API_KEY`。详情参阅 [阿里云百炼控制台](https://bailian.console.aliyun.com)。

??? example "IBM watsonx.ai LLM"

    ```python
    config.set_provider_config("llm", "WatsonX", {"model": "ibm/granite-3-3-8b-instruct"})
    ```

    自定义生成参数：

    ```python
    config.set_provider_config("llm", "WatsonX", {
        "model": "ibm/granite-3-3-8b-instruct",
        "max_new_tokens": 1000,
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 50
    })
    ```

    使用 `space_id` 代替 `project_id` 时，也可以通过参数传入。需要设置 `WATSONX_APIKEY`、`WATSONX_URL`，以及 `WATSONX_PROJECT_ID` 或相应的空间 ID，并执行 `pip install ibm-watsonx-ai`。

    详情参阅 [WatsonX 模型说明](https://www.ibm.com/products/watsonx-ai/foundation-models)。

??? example "Jiekou.AI"

    ```python
    config.set_provider_config("llm", "JiekouAI", {"model": "claude-sonnet-4-5-20250929"})
    ```

    需要设置 `JIEKOU_API_KEY`。详情参阅 [Jiekou.AI 快速开始](https://docs.jiekou.ai/docs/support/quickstart)。
