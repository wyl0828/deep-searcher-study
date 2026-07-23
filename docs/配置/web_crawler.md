# 网页抓取器配置

DeepSearcher 支持多种网页抓取器，可从网站收集数据并进行处理和索引。

## 📝 基本配置

```python
config.set_provider_config("web_crawler", "(WebCrawlerName)", "(Arguments dict)")
```

## 📋 可用的网页抓取器

| 抓取器 | 说明 | 主要特点 |
|--------|------|----------|
| **FireCrawlCrawler** | 云端网页抓取服务 | API 简单，由服务商托管 |
| **Crawl4AICrawler** | 基于浏览器自动化的抓取器 | 完整支持 JavaScript |
| **JinaCrawler** | 内容提取服务 | 解析准确度高 |
| **DoclingCrawler** | 结合网页抓取与文档处理 | 支持多种格式 |

## 🔍 抓取器选项

### FireCrawl

[FireCrawl](https://docs.firecrawl.dev/introduction) 是面向 AI 应用的云端网页抓取服务。

主要特点：

- API 简单
- 托管式服务
- 支持高级解析

```python
config.set_provider_config("web_crawler", "FireCrawlCrawler", {})
```

??? tip "配置方法"

    1. 注册 FireCrawl 并获取 API Key
    2. 设置环境变量：

       ```bash
       export FIRECRAWL_API_KEY="your_api_key"
       ```

    3. 更多信息请参阅 [FireCrawl 文档](https://docs.firecrawl.dev/introduction)

### Crawl4AI

[Crawl4AI](https://docs.crawl4ai.com/) 是一个具备浏览器自动化能力的 Python 网页抓取包。

```python
config.set_provider_config("web_crawler", "Crawl4AICrawler", {"browser_config": {"headless": True, "verbose": True}})
```

??? tip "配置方法"

    1. 安装 Crawl4AI：

       ```bash
       pip install crawl4ai
       ```

    2. 执行初始化命令：

       ```bash
       crawl4ai-setup
       ```

    3. 更多信息请参阅 [Crawl4AI 文档](https://docs.crawl4ai.com/)

### Jina Reader

[Jina Reader](https://jina.ai/reader/) 可高精度提取网页内容。

```python
config.set_provider_config("web_crawler", "JinaCrawler", {})
```

??? tip "配置方法"

    1. 获取 Jina API Key
    2. 设置环境变量：

       ```bash
       export JINA_API_TOKEN="your_api_key"
       # 或者
       export JINAAI_API_KEY="your_api_key"
       ```

    3. 更多信息请参阅 [Jina Reader 文档](https://jina.ai/reader/)

### Docling Crawler

[Docling](https://docling-project.github.io/docling/) 除文档处理外，也提供网页抓取能力。

```python
config.set_provider_config("web_crawler", "DoclingCrawler", {})
```

??? tip "配置方法"

    1. 安装 Docling：

       ```bash
       pip install docling
       ```

    2. 支持的格式请参阅 [Docling 文档](https://docling-project.github.io/docling/usage/supported_formats/#supported-output-formats)。
