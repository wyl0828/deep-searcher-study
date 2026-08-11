# DeepSearcher 原版基线实施计划

> **供自动化执行者使用：** 必须使用 `superpowers:executing-plans`，按任务逐项实施本计划。使用复选框（`- [ ]`）跟踪进度。

**目标：** 在原生 Windows 上以官方源码、Python 3.10 和 Docker Milvus Standalone 2.5.8 跑通 DeepSearcher 的 PDF 入库与问答闭环。

**架构：** DeepSearcher 与 FastAPI 运行在 Windows 的 uv 虚拟环境中，Milvus、etcd 和 MinIO 运行在 Docker Desktop 的 Linux 容器中。DeepSearcher 只修改提供商和连接配置，不修改 Agent、Loader、Vector DB 或 Query 核心实现。

**技术栈：** Python 3.10、uv、FastAPI、PyMilvus 2.5.8、Milvus Standalone 2.5.8、Docker Compose、PowerShell

---

## 文件结构

- 新建： `infra/milvus/docker-compose.yml` — 固定版本的 Milvus、etcd、MinIO 本地基础设施。
- 修改： `.gitignore` — 排除 Milvus 数据、日志、测试 PDF 和本地密钥。
- 修改： `deepsearcher/config.yaml` — 将向量库从本地文件切换到 Docker Milvus；模型通过阿里云百炼 OpenAI 兼容入口调用。
- 新建： `data/baseline/aurora-facts.pdf` — 不进入 Git 的端到端测试 PDF。
- 本地记录：实际版本、命令和验收结果保存在私有验证笔记中，不纳入公开仓库。
- 保留： `deepsearcher/agent/`、`deepsearcher/loader/`、`deepsearcher/vector_db/`、`deepsearcher/offline_loading.py`、`deepsearcher/online_query.py`。

### 任务 1: 建立官方源码基线

**文件：**
- 保留： `docs/开发记录/设计规范/2026-06-28-deepsearcher-original-baseline-design.md`
- 保留： `docs/开发记录/实施计划/2026-07-01-deepsearcher-original-baseline.md`

- [x] **步骤 1: 检查远程仓库**

运行：

```powershell
git remote -v
```

预期： `origin` 指向 `wyl0828/deep-searcher-study`，`upstream` 指向 `zilliztech/deep-searcher`。

- [x] **步骤 2: 获取官方 master**

运行：

```powershell
git fetch upstream master --prune
```

预期： 生成或更新 `upstream/master`。

- [x] **步骤 3: 检查文档路径冲突**

运行：

```powershell
git ls-tree -r --name-only upstream/master -- docs/开发记录
```

预期： 无输出；若存在相同路径，停止切换并先比较内容。

- [x] **步骤 4: 创建学习分支**

运行：

```powershell
git switch -c study-baseline --track upstream/master
```

预期： 当前分支为 `study-baseline`，现有 `docs/开发记录/` 仍保留。

- [x] **步骤 5: 记录基线**

运行：

```powershell
git rev-parse HEAD
git status --short --branch
```

预期： HEAD 指向官方提交；只有本项目设计和计划文档未跟踪。

- [x] **步骤 6: 提交设计与计划**

运行：

```powershell
git add docs/开发记录/设计规范/2026-06-28-deepsearcher-original-baseline-design.md docs/开发记录/实施计划/2026-07-01-deepsearcher-original-baseline.md
git commit -m "docs: add original baseline design and plan"
```

预期： 本地提交成功，不推送。

### 任务 2: 安装并锁定 Python 环境

**文件：**
- 保留： `uv.lock`
- Create (ignored): `.venv/`

- [x] **步骤 1: 安装与上游锁文件同期的 uv 0.7.8**

运行：

```powershell
$env:UV_NO_MODIFY_PATH = "1"
Invoke-RestMethod https://astral.sh/uv/0.7.8/install.ps1 | Invoke-Expression
```

预期： `C:\Users\32957\.local\bin\uv.exe` 安装成功。

- [x] **步骤 2: 验证 uv**

运行：

```powershell
& "C:\Users\32957\.local\bin\uv.exe" --version
```

预期： 输出 `uv 0.7.8`。

- [x] **步骤 3: 安装 Python 3.10**

运行：

```powershell
& "C:\Users\32957\.local\bin\uv.exe" python install 3.10
```

预期： uv 报告 Python 3.10 已安装。

- [x] **步骤 4: 严格按锁文件同步依赖**

运行：

```powershell
& "C:\Users\32957\.local\bin\uv.exe" sync --frozen --python 3.10
```

预期： 创建 `.venv`，且 `uv.lock` 未发生变化。

- [x] **步骤 5: 验证解释器和关键包**

运行：

```powershell
$uv = "C:\Users\32957\.local\bin\uv.exe"
& $uv run --frozen python --version
& $uv run --frozen python -c "import deepsearcher; print(deepsearcher.__file__)"
& $uv run --frozen python -c "import pymilvus; print(pymilvus.__version__)"
git diff --exit-code -- uv.lock
```

预期： Python 为 3.10.x，DeepSearcher 从当前仓库导入，PyMilvus 为 2.5.8，锁文件无差异。

### 任务 3: 加入固定版本的 Milvus 基础设施

**文件：**
- 新建： `infra/milvus/docker-compose.yml`
- 修改： `.gitignore`

- [x] **步骤 1: 创建 Compose 文件**

Create `infra/milvus/docker-compose.yml` with:

```yaml
version: "3.5"

services:
  etcd:
    container_name: milvus-etcd
    image: quay.io/coreos/etcd:v3.5.18
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
      - ETCD_SNAPSHOT_COUNT=50000
    volumes:
      - ${DOCKER_VOLUME_DIRECTORY:-.}/volumes/etcd:/etcd
    command: etcd -advertise-client-urls=http://etcd:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd
    healthcheck:
      test: ["CMD", "etcdctl", "endpoint", "health"]
      interval: 30s
      timeout: 20s
      retries: 3

  minio:
    container_name: milvus-minio
    image: minio/minio:RELEASE.2023-03-20T20-16-18Z
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    ports:
      - "9001:9001"
      - "9000:9000"
    volumes:
      - ${DOCKER_VOLUME_DIRECTORY:-.}/volumes/minio:/minio_data
    command: minio server /minio_data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3

  standalone:
    container_name: milvus-standalone
    image: milvusdb/milvus:v2.5.8
    command: ["milvus", "run", "standalone"]
    security_opt:
      - seccomp:unconfined
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
    volumes:
      - ${DOCKER_VOLUME_DIRECTORY:-.}/volumes/milvus:/var/lib/milvus
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 30s
      start_period: 90s
      timeout: 20s
      retries: 3
    ports:
      - "19530:19530"
      - "9091:9091"
    depends_on:
      - etcd
      - minio

networks:
  default:
    name: milvus
```

- [x] **步骤 2: 排除本地运行数据**

Append to `.gitignore`:

```gitignore

# DeepSearcher local baseline
/infra/milvus/volumes/
/data/
/logs/
```

- [x] **步骤 3: 验证 Compose 配置**

运行：

```powershell
docker compose -f infra/milvus/docker-compose.yml config --quiet
```

预期： 退出码为 0。

- [x] **步骤 4: 提交基础设施**

运行：

```powershell
git add .gitignore infra/milvus/docker-compose.yml
git commit -m "chore: add Milvus 2.5.8 local infrastructure"
```

预期： 提交成功；`infra/milvus/volumes/` 不在提交中。

### 任务 4: 启动并验证 Milvus

**文件：**
- Create (ignored): `infra/milvus/volumes/`

- [x] **步骤 1: 验证 Docker Engine**

运行：

```powershell
docker info --format "{{.ServerVersion}}"
```

预期： 输出 Docker Server 版本。若连接失败，启动 Docker Desktop 后重试。

- [x] **步骤 2: 验证端口空闲**

运行：

```powershell
Get-NetTCPConnection -LocalPort 19530 -ErrorAction SilentlyContinue
```

预期： 启动前无监听进程。

- [x] **步骤 3: 启动 Milvus**

运行：

```powershell
docker compose -f infra/milvus/docker-compose.yml up -d
```

预期： 创建 `milvus-etcd`、`milvus-minio`、`milvus-standalone`。

- [x] **步骤 4: 验证容器健康**

运行：

```powershell
docker compose -f infra/milvus/docker-compose.yml ps
Test-NetConnection 127.0.0.1 -Port 19530
```

预期： 三个容器运行，Milvus 最终为 healthy，`TcpTestSucceeded` 为 `True`。

- [x] **步骤 5: 验证 PyMilvus 客户端**

运行：

```powershell
& "C:\Users\32957\.local\bin\uv.exe" run --frozen python -c "from pymilvus import MilvusClient; c=MilvusClient(uri='http://127.0.0.1:19530', token='root:Milvus'); print(c.get_server_version()); print(c.list_collections())"
```

预期： 服务端版本为 2.5.8，并返回 Collection 列表。

### 任务 5: 配置 DeepSearcher

**文件：**
- 修改： `deepsearcher/config.yaml`

- [x] **步骤 1: 将向量库连接改为 Docker Milvus**

Replace the active `vector_db` block with:

```yaml
  vector_db:
    provider: "Milvus"
    config:
      default_collection: "deepsearcher"
      uri: "http://127.0.0.1:19530"
      token: "root:Milvus"
      db: "default"
```

- [x] **步骤 2: 固定一个同时提供 LLM 与 Embedding 的提供商**

将当前 Provider 设置为：

```yaml
  llm:
    provider: "OpenAI"
    config:
      model: "qwen-plus"
  embedding:
    provider: "OpenAIEmbedding"
    config:
      model: "text-embedding-v4"
      dimension: 1024
```

- [x] **步骤 3: 验证 YAML 和连接配置**

运行：

```powershell
& "C:\Users\32957\.local\bin\uv.exe" run --frozen python -c "from deepsearcher.configuration import Configuration; c=Configuration(); v=c.get_provider_config('vector_db'); assert v['provider']=='Milvus'; assert v['config']['uri']=='http://127.0.0.1:19530'; print(v)"
```

预期： 断言通过并输出 Docker Milvus URI。

- [x] **步骤 4: 提交配置差异**

运行：

```powershell
git add deepsearcher/config.yaml
git commit -m "config: connect DeepSearcher to local Milvus"
```

预期： 只提交提供商和连接配置，不包含密钥。

### 任务 6: 启动官方 FastAPI

**文件：**
- Create (ignored): `logs/backend.stdout.log`
- Create (ignored): `logs/backend.stderr.log`

- [x] **步骤 1: 验证模型密钥已在当前进程设置**

Create an ignored `.env` file containing `OPENAI_API_KEY` and the user-provided Beijing workspace `OPENAI_BASE_URL`, then run:

```powershell
$key = Get-Content .env | Where-Object { $_ -match '^OPENAI_API_KEY=.+' } | Select-Object -First 1
$base = Get-Content .env | Where-Object { $_ -match '^OPENAI_BASE_URL=https://.+/compatible-mode/v1$' } | Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($key) -or [string]::IsNullOrWhiteSpace($base)) { throw "OPENAI_API_KEY and OPENAI_BASE_URL are required in .env" }
git check-ignore .env
```

预期： 无异常且 `.env` 被 Git 忽略。命令不得输出密钥内容。

- [x] **步骤 2: 启动 FastAPI**

运行：

```powershell
New-Item -ItemType Directory -Force logs | Out-Null
$uv = "C:\Users\32957\.local\bin\uv.exe"
$backend = Start-Process -FilePath $uv -ArgumentList @("run","--frozen","--env-file",".env","uvicorn","main:app","--host","127.0.0.1","--port","8500") -WorkingDirectory "D:\code\deep-searcher-study" -RedirectStandardOutput "D:\code\deep-searcher-study\logs\backend.stdout.log" -RedirectStandardError "D:\code\deep-searcher-study\logs\backend.stderr.log" -WindowStyle Hidden -PassThru
$backend.Id
```

预期： 返回后台进程 PID。

- [x] **步骤 3: 验证 OpenAPI**

运行：

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8500/openapi.json).StatusCode
```

预期： `200`。

- [x] **步骤 4: 检查启动日志**

运行：

```powershell
Get-Content logs/backend.stderr.log -Tail 100
```

预期： 无模型、Embedding 或 Milvus 初始化异常。

Execution note: 阿里云百炼工作空间的 `/models`、`qwen-plus` 聊天和 `text-embedding-v4` 1024 维向量探测均通过。FastAPI 在 8500 返回 OpenAPI 200。

### 任务 7: 完成真实 PDF 入库与问答

**文件：**
- Create (ignored): `data/baseline/aurora-facts.pdf`

- [x] **步骤 1: 创建固定事实 PDF**

运行：

```powershell
@'
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

path = Path("data/baseline/aurora-facts.pdf")
path.parent.mkdir(parents=True, exist_ok=True)
pdf = canvas.Canvas(str(path), pagesize=A4)
lines = [
    "Project Aurora Baseline Facts",
    "",
    "Project Aurora is owned by Lin Qiao.",
    "The approved production launch date is September 15, 2026.",
    "The approved budget is 2.4 million yuan.",
    "The primary deployment region is Shanghai.",
]
y = 800
for line in lines:
    pdf.drawString(72, y, line)
    y -= 24
pdf.save()
print(path.resolve())
'@ | & "C:\Users\32957\.local\bin\uv.exe" run --frozen --with reportlab python -
```

预期： 生成 `data/baseline/aurora-facts.pdf`，文件大小大于 0。

- [x] **步骤 2: 调用官方入库接口**

运行：

```powershell
$pdf = (Resolve-Path "data\baseline\aurora-facts.pdf").Path
$body = @{
  paths = @($pdf)
  collection_name = "deepsearcher"
  collection_description = "DeepSearcher original baseline facts"
  batch_size = 256
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8500/load-files/ -ContentType "application/json" -Body $body
```

预期： 返回 `Files loaded successfully.`。

- [x] **步骤 3: 验证 Milvus Collection**

运行：

```powershell
& "C:\Users\32957\.local\bin\uv.exe" run --frozen python -c "from pymilvus import MilvusClient; c=MilvusClient(uri='http://127.0.0.1:19530', token='root:Milvus'); print(c.list_collections()); print(c.get_collection_stats('deepsearcher'))"
```

预期： 列表包含 `deepsearcher`，实体数量大于 0。

- [x] **步骤 4: 调用官方查询接口**

运行：

```powershell
$response = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8500/query/?original_query=Who%20owns%20Project%20Aurora%20and%20what%20is%20its%20approved%20production%20launch%20date%3F&max_iter=3"
$response | ConvertTo-Json -Depth 8
```

预期： `result` 同时包含 `Lin Qiao` 和 `September 15, 2026`，并返回 `consume_token`。

- [x] **步骤 5: 验证持久化**

运行：

```powershell
$connection = Get-NetTCPConnection -LocalPort 8500 -State Listen
Stop-Process -Id $connection.OwningProcess
Start-Sleep -Seconds 2
$uv = "C:\Users\32957\.local\bin\uv.exe"
$backend = Start-Process -FilePath $uv -ArgumentList @("run","--frozen","--env-file",".env","uvicorn","main:app","--host","127.0.0.1","--port","8500") -WorkingDirectory "D:\code\deep-searcher-study" -RedirectStandardOutput "D:\code\deep-searcher-study\logs\backend.stdout.log" -RedirectStandardError "D:\code\deep-searcher-study\logs\backend.stderr.log" -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 5
$response = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8500/query/?original_query=Who%20owns%20Project%20Aurora%20and%20what%20is%20its%20approved%20production%20launch%20date%3F&max_iter=3"
$response | ConvertTo-Json -Depth 8
```

预期： 无需重新入库即可返回相同事实。

Execution note: 首次查询返回 `Lin Qiao owns Project Aurora, and its approved production launch date is September 15, 2026.`，消耗 1795 Token。重启 FastAPI 后，未重新入库的 `max_iter=3` 查询再次返回相同事实。

### 任务 8: 记录结果并完成审计

**文件：**
- 本地记录：实际版本、命令和验收结果保存在私有验证笔记中。

- [x] **步骤 1: 写入实际验证记录**

报告必须包含：

```markdown
# DeepSearcher 原版基线验证

## 版本信息

- DeepSearcher 上游提交
- Python
- uv
- PyMilvus
- Milvus 服务端

## 运行环境检查

- Docker 容器
- 19530 端口
- FastAPI OpenAPI
- 已加载的集合及实体数量

## 端到端证据

- 测试 PDF 中的事实
- 查询问题
- 最终回答
- Token 消耗
- 重启后的持久化查询结果

## Git 安全检查

- 未跟踪 API Key
- 未跟踪 `.env` 或 `.venv`
- 未跟踪 PDF
- 未跟踪 Milvus 数据卷
- 未修改 DeepSearcher 核心业务源码
```

- [x] **步骤 2: 运行安全扫描**

运行：

```powershell
git status --short
git diff --name-only upstream/master...HEAD
git ls-files | Select-String -Pattern "(^|/)\.env$|\.venv|aurora-facts\.pdf|infra/milvus/volumes"
git grep -n -E "sk-[A-Za-z0-9_-]{16,}"
```

预期： 只有设计、计划、Compose、忽略规则、配置和验证报告差异；密钥及运行数据扫描无结果。

- [x] **步骤 3: 验证核心源码未修改**

运行：

```powershell
git diff --exit-code upstream/master...HEAD -- deepsearcher/agent deepsearcher/loader deepsearcher/vector_db deepsearcher/offline_loading.py deepsearcher/online_query.py
```

预期： 退出码为 0。

- [x] **步骤 4: 归档验证记录**

验证记录保留在本地私有目录，并由 `.gitignore` 排除；公开仓库只保留可复现的配置、测试和评测材料。
