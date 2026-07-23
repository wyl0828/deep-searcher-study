# 文件加载器配置

DeepSearcher 支持多种文件加载器，用于从不同格式的文件中提取和处理内容。

## 📝 基本配置

```python
config.set_provider_config("file_loader", "(FileLoaderName)", "(Arguments dict)")
```

## 📋 可用的文件加载器

| 加载器 | 说明 | 支持格式 |
|--------|------|----------|
| **UnstructuredLoader** | 通用文档加载器，格式支持广泛 | PDF、DOCX、PPT、HTML 等 |
| **DoclingLoader** | 具备内容提取能力的文档处理库 | 参阅[官方文档](https://docling-project.github.io/docling/usage/supported_formats/) |

## 🔍 加载器选项

### Unstructured

[Unstructured](https://unstructured.io/) 是一个功能丰富的多格式文档内容提取库。

```python
config.set_provider_config("file_loader", "UnstructuredLoader", {})
```

??? tip "配置方法"

    Unstructured 有两种使用方式：

    1. **调用 API**（推荐用于生产环境）
       - 设置 `UNSTRUCTURED_API_KEY`
       - 设置 `UNSTRUCTURED_API_URL`

    2. **本地处理**
       - 不设置上述 API 环境变量
       - 安装所需依赖：

         ```bash
         # 核心依赖
         pip install unstructured-ingest

         # 支持所有文档格式
         pip install "unstructured[all-docs]"

         # 仅支持指定格式，例如 PDF
         pip install "unstructured[pdf]"
         ```

    更多信息：

    - [Unstructured 文档](https://docs.unstructured.io/ingestion/overview)
    - [完整安装指南](https://docs.unstructured.io/open-source/installation/full-installation)

### Docling

[Docling](https://docling-project.github.io/docling/) 支持处理多种格式的文档。

```python
config.set_provider_config("file_loader", "DoclingLoader", {})
```

??? tip "配置方法"

    1. 安装 Docling：

       ```bash
       pip install docling
       ```

    2. 支持的格式请参阅 [Docling 文档](https://docling-project.github.io/docling/usage/supported_formats/#supported-output-formats)。
