# 为 DeepSearcher 贡献代码

欢迎所有人参与 DeepSearcher。本指南介绍基本贡献流程和项目约定。

## Pull Request 流程

1. Fork 仓库，并从 `master` 创建自己的分支
2. 完成代码修改
3. 运行测试、代码检查和格式化
4. 如有必要，同步更新文档
5. 提交 Pull Request

## 代码检查与格式化

统一的代码、注释、提交信息和 PR 描述风格，可以显著提高审核效率。提交 PR 前必须运行代码检查和格式化。

检查代码风格：

```shell
make lint
```

自动修复格式：

```shell
make format
```

CI 也会在每个 PR 中自动运行这些检查，以保证代码质量和一致性。

## 使用 uv 配置开发环境

DeepSearcher 推荐使用 [uv](https://github.com/astral-sh/uv) 管理依赖。`pyproject.toml` 已针对 uv 配置；与传统工具相比，uv 的依赖解析和安装速度通常更快。

### 以开发模式安装项目

1. 按照 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装 uv。

2. 克隆仓库并进入项目目录：

   ```shell
   git clone https://github.com/zilliztech/deep-searcher.git && cd deep-searcher
   ```

3. 同步依赖并激活虚拟环境：

   ```shell
   uv sync
   source .venv/bin/activate
   ```

   `uv sync` 会安装 `uv.lock` 中记录的依赖；`source .venv/bin/activate` 用于激活虚拟环境。

   - 安装全部可选依赖和开发依赖：

     ```shell
     uv sync --all-extras --dev
     ```

   - 安装指定的可选依赖，以 `ollama` 为例：

     ```shell
     uv sync --extra ollama
     ```

   其他可选依赖请查看 `pyproject.toml` 中的 `[project.optional-dependencies]`。

### 添加依赖

向 `pyproject.toml` 添加普通依赖：

```shell
uv add <package_name>
```

DeepSearcher 通过可选依赖保持默认安装轻量。可选功能的安装格式为 `deepsearcher[<extra>]`。向某个可选组添加依赖：

```shell
uv add <package_name> --optional <extra>
```

详情参阅 [uv 依赖管理文档](https://docs.astral.sh/uv/concepts/projects/dependencies/)。

### 锁定依赖

项目通过锁文件保证不同开发环境中的依赖版本一致。检查锁文件是否最新：

```shell
uv lock --check
```

修改依赖后，下一次执行 uv 命令时会自动更新锁文件，也可以手动执行：

```shell
uv lock
```

手动同步虚拟环境：

```shell
uv sync
```

当编辑器中的依赖版本不正确时，手动同步尤其有用。详情参阅 [uv 锁定与同步文档](https://docs.astral.sh/uv/concepts/projects/sync/)。

## 运行测试

提交 PR 前，请运行测试套件，确认修改没有引入回归。

### 安装测试依赖

如果尚未安装开发依赖，可执行：

```shell
uv sync --all-extras --dev
```

该命令会安装 pytest、开发依赖和所有可选依赖。

### 执行测试

运行 `tests` 目录下的全部测试：

```shell
uv run pytest tests
```

显示每个测试的详细结果：

```shell
uv run pytest tests -v
```

也可以只运行指定目录、文件、测试类或测试方法：

```shell
# 指定目录
uv run pytest tests/embedding

# 指定文件
uv run pytest tests/embedding/test_bedrock_embedding.py

# 指定测试类
uv run pytest tests/embedding/test_bedrock_embedding.py::TestBedrockEmbedding

# 指定测试方法
uv run pytest tests/embedding/test_bedrock_embedding.py::TestBedrockEmbedding::test_init_default
```

`-v` 表示详细模式，适合定位具体通过或失败的测试。

## 开发者原创声明（DCO）

所有贡献都需要签署 [Developer Certificate of Origin](https://developercertificate.org/)，即在提交信息中加入 `Signed-off-by`：

```text
Signed-off-by: Your Name <your.email@example.com>
```
