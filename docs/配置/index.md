# 配置概览

DeepSearcher 的各个组件都支持灵活配置，可以按需调整以下部分。

## 📋 组件

| 组件 | 用途 | 文档 |
|------|------|------|
| **LLM** | 使用大语言模型处理查询 | [LLM 配置](llm.md) |
| **Embedding 模型** | 将文本转换为向量，用于向量检索 | [Embedding 模型](embedding.md) |
| **向量数据库** | 存储和检索文本向量 | [向量数据库](vector_db.md) |
| **文件加载器** | 加载并处理不同格式的文件 | [文件加载器](file_loader.md) |
| **网页抓取器** | 从网页收集信息 | [网页抓取器](web_crawler.md) |

## 🔄 通用配置方式

所有组件采用一致的配置方法：

```python
from deepsearcher.configuration import Configuration, init_config

# 创建配置对象
config = Configuration()

# 设置组件的 provider 和参数
config.set_provider_config("[component]", "[provider]", {"option": "value"})

# 应用配置
init_config(config=config)
```

各组件的详细选项请参阅上表中的对应页面。
