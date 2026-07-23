# 🛠️ 开发模式安装

本指南适合希望修改 DeepSearcher 源码或开发新功能的贡献者。

## 📋 前置条件

- Python 3.10 或更高版本
- git
- 推荐使用 [uv](https://github.com/astral-sh/uv) 包管理器，安装速度更快

## 🔄 安装步骤

### 第 1 步：安装 uv（推荐）

[uv](https://github.com/astral-sh/uv) 是速度更快的 Python 包管理工具，可替代 pip。

=== "使用 pip"
    ```bash
    pip install uv
    ```

=== "使用 curl（Unix/macOS）"
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

=== "使用 PowerShell（Windows）"
    ```powershell
    irm https://astral.sh/uv/install.ps1 | iex
    ```

其他安装方式请参阅 [uv 官方安装指南](https://docs.astral.sh/uv/getting-started/installation/)。

### 第 2 步：克隆仓库

```bash
git clone https://github.com/zilliztech/deep-searcher.git
cd deep-searcher
```

### 第 3 步：配置开发环境

=== "使用 uv（推荐）"
    ```bash
    uv sync
    source .venv/bin/activate
    ```

=== "使用 pip"
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # Windows：.venv\Scripts\activate
    pip install -e ".[dev,all]"
    ```

## 🧪 运行测试

```bash
pytest tests/
```

## 📚 更多资料

更详细的开发环境、贡献规范、代码风格和测试说明，请参阅仓库中的 [CONTRIBUTING.md](https://github.com/zilliztech/deep-searcher/blob/main/CONTRIBUTING.md)。
