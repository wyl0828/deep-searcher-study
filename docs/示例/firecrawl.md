# FireCrawl 集成示例

这个示例展示如何用 FireCrawl 抓取网站内容，并将结果交给 DeepSearcher 查询。

## 执行流程

FireCrawl 是面向 AI 应用的网页抓取服务。本示例会完成：

1. 在 DeepSearcher 中启用 FireCrawl
2. 配置服务所需的 API Key
3. 抓取网站并提取内容
4. 查询抓取到的数据

## 示例代码

```python
import logging
import os
from deepsearcher.offline_loading import load_from_website
from deepsearcher.online_query import query
from deepsearcher.configuration import Configuration, init_config

logging.getLogger("httpx").setLevel(logging.WARNING)

# 实际项目中请通过安全方式设置 API Key，不要直接写进源码
os.environ["OPENAI_API_KEY"] = "sk-***************"
os.environ["FIRECRAWL_API_KEY"] = "fc-***************"


def main():
    # 第 1 步：初始化配置
    config = Configuration()
    config.set_provider_config("vector_db", "Milvus", {})
    config.set_provider_config("web_crawler", "FireCrawlCrawler", {})
    init_config(config)

    # 第 2 步：抓取网站并写入 Milvus
    website_url = "https://example.com"  # 替换为目标网站
    collection_name = "FireCrawl"
    collection_description = "FireCrawl 抓取的文档"

    load_from_website(
        urls=website_url,
        collection_name=collection_name,
        collection_description=collection_description,
    )

    # FireCrawl 还支持多页抓取：
    # load_from_website(urls=website_url, max_depth=2, limit=20,
    #                   allow_backward_links=True,
    #                   collection_name=collection_name,
    #                   collection_description=collection_description)

    # 第 3 步：查询数据
    result = query("Milvus 是什么？")
    print(result)


if __name__ == "__main__":
    main()
```

## 运行方法

1. 安装 DeepSearcher：`pip install deepsearcher`
2. 在 [FireCrawl](https://docs.firecrawl.dev/introduction) 注册并获取 API Key
3. 设置真实 API Key
4. 将 `website_url` 改为要抓取的网站
5. 执行：`python load_website_using_firecrawl.py`

## 高级抓取选项

- `max_depth`：控制抓取链接的最大深度
- `limit`：限制最多抓取多少个页面
- `allow_backward_links`：是否允许访问父级或同级页面

## 关键概念

- **网页抓取**：从网站提取内容
- **深度控制**：限制抓取器沿链接继续访问的层数
- **URL 处理**：从一个入口地址处理多个页面
- **向量存储**：将网页内容写入向量数据库供后续检索
