# 📦 使用 pip 安装

如果只想使用 DeepSearcher、不需要修改源码，推荐采用这种方式。

## 📋 前置条件

- Python 3.10 或更高版本
- pip 包管理器（Python 自带）
- 建议使用虚拟环境

## 🔄 安装步骤

### 第 1 步：创建虚拟环境

```bash
python -m venv .venv
```

### 第 2 步：激活虚拟环境

=== "Linux/macOS"
    ```bash
    source .venv/bin/activate
    ```

=== "Windows"
    ```bash
    .venv\Scripts\activate
    ```

### 第 3 步：安装 DeepSearcher

```bash
pip install deepsearcher
```

## 🧩 可选依赖

DeepSearcher 通过可选依赖支持多种集成。

| 集成 | 安装命令 | 说明 |
|------|----------|------|
| Ollama | `pip install "deepsearcher[ollama]"` | 用于本地部署大语言模型 |
| 全部扩展 | `pip install "deepsearcher[all]"` | 安装所有可选依赖 |

## ✅ 验证安装

```python
from deepsearcher import __version__
print(f"DeepSearcher 版本：{__version__}")
```
