# Unstructured 集成示例

这个示例展示如何将 Unstructured 与 DeepSearcher 结合，用于更强的文档解析。

## 执行流程

Unstructured 可以从多种文档格式中提取内容。本示例会完成：

1. 在 DeepSearcher 中启用 Unstructured
2. 可选地配置 Unstructured API Key
3. 使用 Unstructured 解析器加载文档
4. 查询提取后的内容

## 示例代码

```python
import logging
import os
from deepsearcher.offline_loading import load_from_local_files
from deepsearcher.online_query import query
from deepsearcher.configuration import Configuration, init_config

logging.getLogger("httpx").setLevel(logging.WARNING)

# 可选：使用云服务时设置；实际项目中请安全保存 API Key
os.environ["UNSTRUCTURED_API_KEY"] = "***************"
os.environ["UNSTRUCTURED_API_URL"] = "***************"


def main():
    # 第 1 步：初始化配置
    config = Configuration()
    config.set_provider_config("vector_db", "Milvus", {})
    config.set_provider_config("file_loader", "UnstructuredLoader", {})
    init_config(config)

    # 第 2 步：加载本地文件或目录
    input_file = "your_local_file_or_directory"  # 替换为实际路径
    collection_name = "Unstructured"
    collection_description = "使用 Unstructured 加载的文档"

    load_from_local_files(
        paths_or_directory=input_file,
        collection_name=collection_name,
        collection_description=collection_description,
    )

    # 第 3 步：查询数据
    result = query("Milvus 是什么？")
    print(result)


if __name__ == "__main__":
    main()
```

## 运行方法

1. 安装 DeepSearcher 和 Unstructured：`pip install deepsearcher "unstructured[all-docs]"`
2. 如需使用云服务，可在 [unstructured.io](https://unstructured.io) 注册并获取 API Key
3. 将 `your_local_file_or_directory` 替换为自己的文件或目录
4. 执行：`python load_local_file_using_unstructured.py`

## Unstructured 的两种模式

1. **API 模式**：设置 `UNSTRUCTURED_API_KEY` 和 `UNSTRUCTURED_API_URL`，使用云端服务
2. **本地模式**：不设置上述环境变量，直接在本机处理文档

## 关键概念

- **文档处理**：解析多种格式的文档
- **API/本地模式**：可以根据部署需求灵活选择
- **系统集成**：与 DeepSearcher 的向量数据库和查询流程协同工作
