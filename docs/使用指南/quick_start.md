# 🚀 快速开始

## 前置条件

✅ 开始前，请在环境变量中准备好 `OPENAI_API_KEY`。如果配置中使用了其他大模型，也要设置对应的 API Key。

## 基本用法

```python
# 导入配置模块
from deepsearcher.configuration import Configuration, init_config
from deepsearcher.online_query import query

# 初始化配置
config = Configuration()

# 在此自定义配置
config.set_provider_config("llm", "OpenAI", {"model": "o1-mini"})
config.set_provider_config("embedding", "OpenAIEmbedding", {"model": "text-embedding-ada-002"})
init_config(config=config)

# 加载本地文件
from deepsearcher.offline_loading import load_from_local_files
load_from_local_files(paths_or_directory=your_local_path)

# 可选：加载网页数据，需要设置 FIRECRAWL_API_KEY
from deepsearcher.offline_loading import load_from_website
load_from_website(urls=website_url)

# 查询已加载的数据
result = query("请撰写一份关于 xxx 的报告。")  # 替换为你的问题
print(result)
```

## 下一步

完成快速体验后，可以继续阅读：

- [命令行工具](cli.md)：不编写程序也能使用 DeepSearcher
- [部署](deployment.md)：将 DeepSearcher 启动为 Web 服务
