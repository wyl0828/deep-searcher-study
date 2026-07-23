# 🔧 安装

DeepSearcher 提供多种安装方式，以满足不同使用需求。

## 📋 安装方式

| 方式 | 适合人群 | 说明 |
|------|----------|------|
| [📦 使用 pip 安装](pip.md) | 大多数用户 | 使用 pip 包管理器快速安装 |
| [🛠️ 开发模式安装](development.md) | 贡献者和开发者 | 适合修改源码或参与项目开发 |

## 🚀 快速验证

安装完成后，可用以下代码验证：

```python
from deepsearcher.configuration import Configuration
from deepsearcher.online_query import query

# 使用默认配置初始化
config = Configuration()
print("DeepSearcher 安装成功！")
```

## 💻 系统要求

- Python 3.10 或更高版本
- 至少 4GB 内存，建议 8GB 以上
- 可访问网络，以便下载模型和依赖
