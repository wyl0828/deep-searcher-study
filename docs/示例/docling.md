# Docling 集成示例

这个示例展示如何使用 Docling 加载本地文件和抓取网页内容。

## 执行流程

脚本会完成：

1. 为文件加载和网页抓取配置 Docling
2. 使用 Docling 文档解析器加载本地文件
3. 从 Markdown、PDF 等多种网页来源抓取内容
4. 查询已经加载的数据

## 示例代码

```python
import logging
from deepsearcher.offline_loading import load_from_local_files, load_from_website
from deepsearcher.online_query import query
from deepsearcher.configuration import Configuration, init_config

# 减少第三方库日志输出
logging.getLogger("httpx").setLevel(logging.WARNING)

def main():
    # 第 1 步：初始化配置
    config = Configuration()
    config.set_provider_config("vector_db", "Milvus", {})
    config.set_provider_config("file_loader", "DoclingLoader", {})
    config.set_provider_config("web_crawler", "DoclingCrawler", {})
    init_config(config)

    # 第 2a 步：使用 DoclingLoader 加载本地文件
    local_file = "your_local_file_or_directory"
    local_collection_name = "DoclingLocalFiles"
    local_collection_description = "使用 DoclingLoader 加载的 Milvus 文档"

    print("\n=== 使用 DoclingLoader 加载本地文件 ===")

    try:
        load_from_local_files(
            paths_or_directory=local_file,
            collection_name=local_collection_name,
            collection_description=local_collection_description,
            force_new_collection=True,
        )
        print(f"加载成功：{local_file}")
    except ValueError as error:
        print(f"参数校验失败：{error}")
    except Exception as error:
        print(f"加载失败：{error}")

    # 第 2b 步：使用 DoclingCrawler 抓取网页
    urls = [
        "https://milvus.io/docs/quickstart.md",
        "https://milvus.io/docs/overview.md",
        "https://arxiv.org/pdf/2408.09869",
    ]
    web_collection_name = "DoclingWebCrawl"
    web_collection_description = "使用 DoclingCrawler 抓取的 Milvus 文档"

    print("\n=== 使用 DoclingCrawler 抓取网页 ===")
    load_from_website(
        urls=urls,
        collection_name=web_collection_name,
        collection_description=web_collection_description,
        force_new_collection=True,
    )

    # 第 3 步：查询数据
    result = query("Milvus 是什么？")
    print(result)


if __name__ == "__main__":
    main()
```

## 运行方法

1. 安装 DeepSearcher 和 Docling：`pip install deepsearcher docling`
2. 将 `your_local_file_or_directory` 替换为实际文件或目录
3. 执行：`python load_and_crawl_using_docling.py`

## 关键概念

- **多个 Provider**：同时把 Docling 配置为文件加载器和网页抓取器
- **本地文件**：从本机文件系统加载文档
- **网页抓取**：处理不同格式的网页 URL
- **异常处理**：在加载失败时输出明确错误
