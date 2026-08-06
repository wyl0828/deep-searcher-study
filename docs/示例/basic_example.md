# 基础示例

这个示例展示 DeepSearcher 的核心功能：加载文档并进行语义检索。

## 执行流程

脚本会依次完成：

1. 使用默认设置配置 DeepSearcher
2. 加载一份介绍 Milvus 的 PDF
3. 提问 Milvus 与其他向量数据库的区别
4. 显示 Token 使用量

## 示例代码

```python
import logging
import os

from deepsearcher.offline_loading import load_from_local_files
from deepsearcher.online_query import query
from deepsearcher.configuration import Configuration, init_config

# 减少 httpx/OpenAI 的日志输出
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)

current_dir = os.path.dirname(os.path.abspath(__file__))

# 可以在这里自定义配置
config = Configuration()
init_config(config=config)

# 也可以先克隆 Milvus 文档仓库，然后批量加载 Markdown：
# git clone https://github.com/milvus-io/milvus-docs.git
# import glob
# all_md_files = glob.glob('xxx/milvus-docs/site/en/**/*.md', recursive=True)
# load_from_local_files(paths_or_directory=all_md_files, collection_name="milvus_docs", collection_description="全部 Milvus 文档")

# 加载单个 PDF；请在 DeepSearcher 项目根目录执行脚本
load_from_local_files(
    paths_or_directory=os.path.join(current_dir, "data/WhatisMilvus.pdf"),
    collection_name="milvus_docs",
    collection_description="全部 Milvus 文档",
    # 如需安全重建，可设置 force_new_collection=True：
    # 系统会先构建候选版本，完成后切换别名，并保留旧版本用于回滚。
)

question = "请比较 Milvus 与其他向量数据库，并撰写报告。"

_, _, consumed_token = query(question, max_iter=1)
print(f"消耗的 Token：{consumed_token}")
```

## 运行方法

1. 安装 DeepSearcher：`pip install deepsearcher`
2. 创建数据目录，并放入介绍 Milvus 的 PDF，也可以换成自己的文档
3. 执行：`python basic_example.py`

## 关键概念

- **配置**：使用默认配置启动 DeepSearcher
- **文档加载**：加载单个 PDF 文件
- **查询**：提出需要综合多处信息的问题
- **Token 统计**：记录大模型的 Token 使用量
