# DeepSearcher 原版基线设计（Windows + Docker Milvus）

> 修订日期：2026-07-01

## 1. 文档定位

本阶段只解决一件事：在原生 Windows 环境中从 DeepSearcher 官方源码跑通一次真实的 PDF 入库与问答闭环。

本阶段不开发 Streamlit 或其他前端。最小演示前端是独立子项目，只有本基线验收通过且用户再次确认后，才另写设计与实施计划。

## 2. 决策记录

### 采用方案：Docker Milvus Standalone

DeepSearcher 当前官方 `uv.lock` 将 PyMilvus 固定为 2.5.8，并且在 `win32` 平台明确不安装 `milvus-lite`。Milvus 官方也没有把原生 Windows 列入 Milvus Lite 支持范围。因此，本阶段不在原生 Windows 上使用 `uri: ./milvus.db`。

本项目采用：

- Windows 上运行 DeepSearcher Python 进程。
- Docker Desktop 使用 WSL2 后端。
- Docker 中运行 Milvus Standalone 2.5.8。
- DeepSearcher 通过 `http://127.0.0.1:19530` 连接 Milvus。

Milvus 服务端固定为 2.5.8，与上游锁文件中的 PyMilvus 2.5.8 对齐。不得直接使用当前 Milvus 3.0 beta Compose 文件，也不得在未验证兼容性时升级客户端或服务端。

### 未采用方案

1. **原生 Windows + Milvus Lite**：上游锁文件不安装 Lite，官方未列为支持环境，兼容风险高。
2. **整个项目迁入 WSL2 + Milvus Lite**：可作为 Docker 无法使用时的回退方案，但会改变开发路径和文件系统位置。
3. **Zilliz Cloud**：能减少本地部署，但引入外部账号、网络和费用，不适合作为首个本地基线。

## 3. 目标

在 `D:\code\deep-searcher-study` 中完成以下链路：

```text
测试 PDF
→ DeepSearcher FastAPI /load-files/
→ PDFLoader 解析
→ 文本切分
→ Embedding 向量化
→ Docker Milvus Standalone
→ FastAPI /query/
→ DeepSearcher 检索与反思迭代
→ 返回与 PDF 事实一致的答案和 Token 消耗
```

成功标准是官方 FastAPI 闭环可复现，而不是页面效果。

## 4. 当前环境

- 工作目录已有本设计文档，尚未初始化 Git。
- Git 2.45.1 已安装。
- 系统 Python 为 3.13.0；项目统一使用 uv 管理的 Python 3.10。
- uv 尚未安装。
- Docker CLI 29.4.1 已安装。
- Docker Desktop 的 Linux Engine 当前未启动。
- WSL2 已启用，现有 `docker-desktop` 发行版。
- 主机约 15.7 GB 内存、12 个逻辑处理器，达到 Milvus Standalone 最低 8 GB 内存要求，但运行期间需要控制其他高内存程序。

## 5. 范围

### 本阶段包含

- 获取并固定 DeepSearcher 官方 `master` 基线提交。
- 建立 Python 3.10 隔离环境。
- 启动并验证 Milvus Standalone 2.5.8。
- 配置一个官方支持的 LLM 和一个 Embedding 模型。
- 启动官方 FastAPI。
- 导入一个小型 PDF。
- 完成至少一次有效问答。
- 记录版本、命令、配置差异和验证结果。

### 本阶段不包含

- Streamlit、React、Next.js 或其他前端。
- 登录、权限、多用户和知识库管理。
- 引用展示、Agent Trace 和检索评测。
- 修改 DeepSearcher Agent、Loader、Vector DB 或 Query 核心逻辑。
- Milvus Distributed、Kubernetes 和公网部署。
- 简历包装与二次开发功能。

## 6. 源码与版本控制

当前目录已有 `docs/`，不能直接执行 `git clone ... .`。执行阶段采用以下策略：

1. 在当前目录执行 `git init`。
2. 将官方仓库配置为只用于同步的 `upstream`。
3. 获取 `upstream/master`。
4. 检查上游是否存在与当前 `docs/superpowers/` 冲突的路径。
5. 从 `upstream/master` 创建本地 `study-baseline` 分支。
6. 记录实际基线提交 SHA。
7. 将本设计文档作为学习分支上的独立文档提交。

未来创建个人 GitHub 仓库时再配置 `origin`。官方仓库不得被配置成个人推送目标。

## 7. Python 环境

- 使用与上游锁文件最后更新时间一致的 uv 0.7.8，不使用全局 pip 修改系统 Python。最新 uv 0.11 会重写该旧锁文件，不能作为本基线执行器。
- 由 uv 安装并选择 Python 3.10。
- 使用官方 `uv.lock` 执行 `uv sync --frozen`，所有 `uv run` 同样带 `--frozen`，首次基线不更新锁文件。
- 验证实际解释器版本、`deepsearcher` 导入和 PyMilvus 版本。
- 若 `uv sync --frozen` 失败，先定位平台依赖；不得立即删除锁文件或升级依赖。

基线必须保留以下版本证据：

- Python 版本。
- uv 版本。
- DeepSearcher 上游提交 SHA。
- PyMilvus 版本。
- Milvus 服务端版本。

## 8. Docker Milvus 设计

### 8.1 部署文件

使用 Milvus 官方 v2.5.8 Standalone Docker Compose 文件，并保存到：

```text
infra/milvus/docker-compose.yml
```

运行数据保存在：

```text
infra/milvus/volumes/
```

`volumes/` 必须加入 Git 忽略规则；Compose 文件应进入版本控制，以保证环境可复现。

### 8.2 启动前预检

启动前依次确认：

1. Docker Desktop 已启动且使用 Linux Containers。
2. `docker info` 可以连接 Server。
3. WSL2 后端可用。
4. 端口 19530 未被占用。
5. Docker 可用内存满足 Milvus Standalone 要求。
6. 磁盘剩余空间足够保存镜像和向量数据。

### 8.3 健康验证

Milvus 只有同时满足以下条件才算可用：

- Compose 服务处于 running/healthy 状态。
- 主机端口 19530 可连接。
- 使用项目 `.venv` 中的 `MilvusClient` 能执行 `list_collections()`。
- 服务端日志没有持续重启、etcd 超时或存储错误。

不得仅凭“容器已创建”判定成功。

### 8.4 数据保留

- 日常停止使用不会删除数据卷的命令。
- 不使用 `docker compose down -v`。
- 不手动删除 `infra/milvus/volumes/`。
- 若必须清空数据，先说明将删除的 Collection 或目录并再次确认。

## 9. DeepSearcher 配置

官方默认配置使用本地文件：

```yaml
vector_db:
  provider: "Milvus"
  config:
    uri: "./milvus.db"
```

原生 Windows 基线需要做一处明确的配置差异：

```yaml
vector_db:
  provider: "Milvus"
  config:
    default_collection: "deepsearcher"
    uri: "http://127.0.0.1:19530"
    token: "root:Milvus"
    db: "default"
```

该差异只改变 Milvus 连接位置，不修改 DeepSearcher 的向量库实现。配置变更必须单独记录，不能与业务代码修改混在一起。

## 10. 模型与密钥

- LLM 使用阿里云百炼北京工作空间的 OpenAI 兼容接口，模型为 `qwen-plus`。
- Embedding 使用同一兼容接口的 `text-embedding-v4`，固定输出 1024 维向量。
- 工作空间地址通过 `OPENAI_BASE_URL` 注入，API Key 通过 `OPENAI_API_KEY` 注入；Anthropic 兼容入口不参与本基线。
- API Key 只通过 PowerShell 会话环境变量或本地 `.env` 注入。
- `.env` 必须保持在 Git 忽略范围内。
- 密钥不得写入 `config.yaml`、命令历史示例、测试报告或截图。
- 首次入库后不随意更换 Embedding 模型；模型维度变化会导致现有 Collection 不兼容。

如果用户没有可用的 LLM 与 Embedding API，环境和 Milvus 可以完成验证，但端到端基线不能判定通过。

## 11. 执行阶段

### 阶段 A：源码与环境（1–2 小时）

1. 初始化 Git 并获取上游源码。
2. 固定基线 SHA。
3. 安装 uv 和 Python 3.10。
4. 使用锁文件同步依赖。
5. 验证核心包与 PyMilvus 导入。

### 阶段 B：Milvus Standalone（30–90 分钟）

1. 启动 Docker Desktop。
2. 加入固定版本的官方 Compose 文件。
3. 启动 Milvus 2.5.8。
4. 完成容器、端口和客户端三层健康检查。
5. 确认持久化目录未进入 Git。

### 阶段 C：官方 FastAPI（1–2 小时）

1. 设置模型 API 环境变量。
2. 将 Milvus URI 指向 `127.0.0.1:19530`。
3. 启动官方 FastAPI。
4. 验证 `GET /openapi.json`。
5. 检查启动日志中不存在模型或 Milvus 初始化错误。

### 阶段 D：真实闭环（1–2 小时）

1. 准备一份内容明确且较小的 PDF。
2. 调用 `POST /load-files/` 写入固定 Collection。
3. 检查 Milvus 中出现 Collection 和数据。
4. 调用 `GET /query/` 提交可由 PDF 明确回答的问题。
5. 对照 PDF 核验答案事实。
6. 重启 FastAPI 后再次查询，验证 Milvus 数据持久化。

### 阶段 E：记录与收口（约 30 分钟）

1. 记录全部版本、命令和基线 SHA。
2. 记录测试 PDF、问题、预期事实和实际答案。
3. 检查 Git 差异仅包含计划、基础设施、忽略规则和必要配置。
4. 扫描工作区，确认没有 API Key、上传文档和向量数据被跟踪。

正常情况下总时间为半天到一天半；镜像下载和模型接口问题可能延长时间。

## 12. 故障定位顺序

必须一次只解决一个边界问题：

1. **Docker 不可用**：检查 Docker Desktop、WSL2 和 Linux Engine。
2. **镜像拉取失败**：检查网络、代理和 Docker Hub/GitHub 可达性。
3. **容器不健康**：检查内存、端口、etcd、MinIO 和 Milvus 日志。
4. **客户端连接失败**：检查 19530、URI、服务版本和 PyMilvus 版本。
5. **FastAPI 启动失败**：检查 Python 3.10 环境、依赖和模型配置。
6. **入库失败**：按 PDF 解析、Embedding、Collection 创建和 Milvus 写入顺序排查。
7. **查询失败**：按 Collection 数据、Embedding 查询、LLM 鉴权和 Agent 日志顺序排查。
8. **向量维度不一致**：确认 Embedding 模型是否在建库后发生变化；不得用异常捕获掩盖结构冲突。

Docker 路线确实无法使用时，才暂停并重新设计“WSL2 内运行完整项目 + Milvus Lite”的回退方案；不得自动切换到原生 Windows Lite。

## 13. 验收标准

- 已记录 DeepSearcher 上游提交 SHA。
- 项目运行在 uv 管理的 Python 3.10 环境。
- `uv sync --frozen` 成功，官方锁文件未被更新。
- PyMilvus 版本为上游锁定版本。
- Milvus Standalone 2.5.8 容器健康。
- 端口 19530 可连接，`list_collections()` 成功。
- FastAPI 在 `127.0.0.1:8500` 启动。当前 Windows/Hyper-V 将 8000 纳入 TCP 排除端口范围，因此本地基线显式使用已通过绑定测试的 8500。
- `GET /openapi.json` 返回成功。
- PDF 成功入库，Milvus 中存在对应 Collection。
- 至少一个问题返回与 PDF 明确事实一致的答案。
- 重启 FastAPI 后仍可查询已入库数据。
- DeepSearcher 核心业务源码未修改。
- Git 中没有 API Key、`.env`、`.venv`、PDF 和 Milvus 数据卷。
- 运行记录足以让另一个终端会话复现整个流程。

## 14. 后续入口

本基线全部验收通过后，下一阶段才讨论最小 Streamlit 前端。前端必须作为独立设计，继续通过官方 FastAPI 调用 DeepSearcher，不直接侵入核心模块。

## 15. 依据

- DeepSearcher 官方仓库：https://github.com/zilliztech/deep-searcher
- Milvus Lite 官方说明：https://milvus.io/docs/milvus_lite.md
- Windows Docker 部署 Milvus：https://milvus.io/docs/install_standalone-windows.md
- Milvus Standalone 环境要求：https://milvus.io/docs/prerequisite-docker.md
- PyMilvus 兼容性说明：https://milvus.io/api-reference/pymilvus/v3.0.x/About.md
