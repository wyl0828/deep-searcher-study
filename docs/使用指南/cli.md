# 💻 命令行工具

DeepSearcher 提供了便捷的命令行工具，可用于加载数据和执行查询。

## 📥 加载数据

从文件或 URL 加载数据：

```shell
deepsearcher load "your_local_path_or_url"
```

加载到指定集合：

```shell
deepsearcher load "your_local_path_or_url" --collection_name "your_collection_name" --collection_desc "your_collection_description"
```

### 示例

#### 加载本地文件

```shell
# 加载单个文件
deepsearcher load "/path/to/your/local/file.pdf"

# 一次加载多个文件
deepsearcher load "/path/to/your/local/file1.pdf" "/path/to/your/local/file2.md"
```

#### 加载网页

> **注意：** 请在环境变量中设置 `FIRECRAWL_API_KEY`。详情参阅 [FireCrawl 文档](https://docs.firecrawl.dev/introduction)。

```shell
deepsearcher load "https://www.wikiwand.com/en/articles/DeepSeek"
```

## 🔍 查询数据

查询已经加载的数据：

```shell
deepsearcher query "请撰写一份关于 xxx 的报告。"
```

## ❓ 查看帮助

查看总体帮助：

```shell
deepsearcher --help
```

查看子命令帮助：

```shell
# load 命令帮助
deepsearcher load --help

# query 命令帮助
deepsearcher query --help
```
