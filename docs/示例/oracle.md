# Oracle 示例

这个进阶示例展示如何调整 Python 导入路径，并详细记录查询结果和 Token 使用量。

## 执行流程

1. 配置 Python 路径，从上级目录导入代码
2. 使用默认配置初始化 DeepSearcher
3. 加载 PDF 并创建向量集合
4. 执行复杂查询，获取回答、检索结果和 Token 数量
5. 可选地监控 Token 消耗

## 示例代码

```python
import sys
import os
from pathlib import Path

script_directory = Path(__file__).resolve().parent.parent
sys.path.append(os.path.abspath(script_directory))

import logging

httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)

current_dir = os.path.dirname(os.path.abspath(__file__))

from deepsearcher.configuration import Configuration, init_config

config = Configuration()
init_config(config=config)

from deepsearcher.offline_loading import load_from_local_files

load_from_local_files(
    paths_or_directory=os.path.join(current_dir, "data/WhatisMilvus.pdf"),
    collection_name="milvus_docs",
    collection_description="全部 Milvus 文档",
    # 如需每次重建集合，可设置 force_new_collection=True
)

from deepsearcher.online_query import query

question = "请比较 Milvus 与其他向量数据库，并撰写报告。"
answer, retrieved_results, consumed_token = query(question)
print(answer)

# 使用 OpenAI GPT-4o 时，该查询大约会消耗 2.5 万～3 万 Token
# print(f"消耗的 Token：{consumed_token}")
```

## 运行方法

1. 安装 DeepSearcher：`pip install deepsearcher`
2. 确保数据目录中存在 `WhatisMilvus.pdf`，或者修改文件路径
3. 执行：`python basic_example_oracle.py`

## 关键概念

- **路径管理**：配置 Python 路径，从上级目录导入模块
- **查询结果拆包**：同时取得回答、检索上下文和 Token 数量
- **复杂查询**：提出需要综合分析的比较类问题
- **Token 成本**：监控 Token 使用量以控制费用
