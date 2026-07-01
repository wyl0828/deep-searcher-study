# DeepSearcher Original Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在原生 Windows 上以官方源码、Python 3.10 和 Docker Milvus Standalone 2.5.8 跑通 DeepSearcher 的 PDF 入库与问答闭环。

**Architecture:** DeepSearcher 与 FastAPI 运行在 Windows 的 uv 虚拟环境中，Milvus、etcd 和 MinIO 运行在 Docker Desktop 的 Linux 容器中。DeepSearcher 只修改提供商和连接配置，不修改 Agent、Loader、Vector DB 或 Query 核心实现。

**Tech Stack:** Python 3.10、uv、FastAPI、PyMilvus 2.5.8、Milvus Standalone 2.5.8、Docker Compose、PowerShell

---

## 文件结构

- Create: `infra/milvus/docker-compose.yml` — 固定版本的 Milvus、etcd、MinIO 本地基础设施。
- Modify: `.gitignore` — 排除 Milvus 数据、日志、测试 PDF 和本地密钥。
- Modify: `deepsearcher/config.yaml` — 将向量库从本地文件切换到 Docker Milvus；模型提供商固定为 SiliconFlow。
- Create: `data/baseline/aurora-facts.pdf` — 不进入 Git 的端到端测试 PDF。
- Create: `docs/verification/2026-07-01-original-baseline.md` — 保存实际版本、命令和验收结果。
- Preserve: `deepsearcher/agent/`、`deepsearcher/loader/`、`deepsearcher/vector_db/`、`deepsearcher/offline_loading.py`、`deepsearcher/online_query.py`。

### Task 1: 建立官方源码基线

**Files:**
- Preserve: `docs/superpowers/specs/2026-06-28-deepsearcher-original-baseline-design.md`
- Preserve: `docs/superpowers/plans/2026-07-01-deepsearcher-original-baseline.md`

- [ ] **Step 1: 检查远程仓库**

Run:

```powershell
git remote -v
```

Expected: `origin` 指向 `wyl0828/deep-searcher-study`，`upstream` 指向 `zilliztech/deep-searcher`。

- [ ] **Step 2: 获取官方 master**

Run:

```powershell
git fetch upstream master --prune
```

Expected: 生成或更新 `upstream/master`。

- [ ] **Step 3: 检查文档路径冲突**

Run:

```powershell
git ls-tree -r --name-only upstream/master -- docs/superpowers
```

Expected: 无输出；若存在相同路径，停止切换并先比较内容。

- [ ] **Step 4: 创建学习分支**

Run:

```powershell
git switch -c study-baseline --track upstream/master
```

Expected: 当前分支为 `study-baseline`，现有 `docs/superpowers/` 仍保留。

- [ ] **Step 5: 记录基线**

Run:

```powershell
git rev-parse HEAD
git status --short --branch
```

Expected: HEAD 指向官方提交；只有本项目设计和计划文档未跟踪。

- [ ] **Step 6: 提交设计与计划**

Run:

```powershell
git add docs/superpowers/specs/2026-06-28-deepsearcher-original-baseline-design.md docs/superpowers/plans/2026-07-01-deepsearcher-original-baseline.md
git commit -m "docs: add original baseline design and plan"
```

Expected: 本地提交成功，不推送。

### Task 2: 安装并锁定 Python 环境

**Files:**
- Preserve: `uv.lock`
- Create (ignored): `.venv/`

- [ ] **Step 1: 安装与上游锁文件同期的 uv 0.7.8**

Run:

```powershell
$env:UV_NO_MODIFY_PATH = "1"
Invoke-RestMethod https://astral.sh/uv/0.7.8/install.ps1 | Invoke-Expression
```

Expected: `C:\Users\32957\.local\bin\uv.exe` 安装成功。

- [ ] **Step 2: 验证 uv**

Run:

```powershell
& "C:\Users\32957\.local\bin\uv.exe" --version
```

Expected: 输出 `uv 0.7.8`。

- [ ] **Step 3: 安装 Python 3.10**

Run:

```powershell
& "C:\Users\32957\.local\bin\uv.exe" python install 3.10
```

Expected: uv 报告 Python 3.10 已安装。

- [ ] **Step 4: 严格按锁文件同步依赖**

Run:

```powershell
& "C:\Users\32957\.local\bin\uv.exe" sync --frozen --python 3.10
```

Expected: 创建 `.venv`，且 `uv.lock` 未发生变化。

- [ ] **Step 5: 验证解释器和关键包**

Run:

```powershell
$uv = "C:\Users\32957\.local\bin\uv.exe"
& $uv run --frozen python --version
& $uv run --frozen python -c "import deepsearcher; print(deepsearcher.__file__)"
& $uv run --frozen python -c "import pymilvus; print(pymilvus.__version__)"
git diff --exit-code -- uv.lock
```

Expected: Python 为 3.10.x，DeepSearcher 从当前仓库导入，PyMilvus 为 2.5.8，锁文件无差异。

### Task 3: 加入固定版本的 Milvus 基础设施

**Files:**
- Create: `infra/milvus/docker-compose.yml`
- Modify: `.gitignore`

- [ ] **Step 1: 创建 Compose 文件**

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

- [ ] **Step 2: 排除本地运行数据**

Append to `.gitignore`:

```gitignore

# DeepSearcher local baseline
/infra/milvus/volumes/
/data/
/logs/
```

- [ ] **Step 3: 验证 Compose 配置**

Run:

```powershell
docker compose -f infra/milvus/docker-compose.yml config --quiet
```

Expected: 退出码为 0。

- [ ] **Step 4: 提交基础设施**

Run:

```powershell
git add .gitignore infra/milvus/docker-compose.yml
git commit -m "chore: add Milvus 2.5.8 local infrastructure"
```

Expected: 提交成功；`infra/milvus/volumes/` 不在提交中。

### Task 4: 启动并验证 Milvus

**Files:**
- Create (ignored): `infra/milvus/volumes/`

- [ ] **Step 1: 验证 Docker Engine**

Run:

```powershell
docker info --format "{{.ServerVersion}}"
```

Expected: 输出 Docker Server 版本。若连接失败，启动 Docker Desktop 后重试。

- [ ] **Step 2: 验证端口空闲**

Run:

```powershell
Get-NetTCPConnection -LocalPort 19530 -ErrorAction SilentlyContinue
```

Expected: 启动前无监听进程。

- [ ] **Step 3: 启动 Milvus**

Run:

```powershell
docker compose -f infra/milvus/docker-compose.yml up -d
```

Expected: 创建 `milvus-etcd`、`milvus-minio`、`milvus-standalone`。

- [ ] **Step 4: 验证容器健康**

Run:

```powershell
docker compose -f infra/milvus/docker-compose.yml ps
Test-NetConnection 127.0.0.1 -Port 19530
```

Expected: 三个容器运行，Milvus 最终为 healthy，`TcpTestSucceeded` 为 `True`。

- [ ] **Step 5: 验证 PyMilvus 客户端**

Run:

```powershell
& "C:\Users\32957\.local\bin\uv.exe" run --frozen python -c "from pymilvus import MilvusClient; c=MilvusClient(uri='http://127.0.0.1:19530', token='root:Milvus'); print(c.get_server_version()); print(c.list_collections())"
```

Expected: 服务端版本为 2.5.8，并返回 Collection 列表。

### Task 5: 配置 DeepSearcher

**Files:**
- Modify: `deepsearcher/config.yaml`

- [ ] **Step 1: 将向量库连接改为 Docker Milvus**

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

- [ ] **Step 2: 固定一个同时提供 LLM 与 Embedding 的提供商**

Set the active providers to:

```yaml
  llm:
    provider: "SiliconFlow"
    config:
      model: "deepseek-ai/DeepSeek-R1"
  embedding:
    provider: "SiliconflowEmbedding"
    config:
      model: "BAAI/bge-m3"
```

- [ ] **Step 3: 验证 YAML 和连接配置**

Run:

```powershell
& "C:\Users\32957\.local\bin\uv.exe" run --frozen python -c "from deepsearcher.configuration import Configuration; c=Configuration(); v=c.get_provider_config('vector_db'); assert v['provider']=='Milvus'; assert v['config']['uri']=='http://127.0.0.1:19530'; print(v)"
```

Expected: 断言通过并输出 Docker Milvus URI。

- [ ] **Step 4: 提交配置差异**

Run:

```powershell
git add deepsearcher/config.yaml
git commit -m "config: connect DeepSearcher to local Milvus"
```

Expected: 只提交提供商和连接配置，不包含密钥。

### Task 6: 启动官方 FastAPI

**Files:**
- Create (ignored): `logs/backend.stdout.log`
- Create (ignored): `logs/backend.stderr.log`

- [ ] **Step 1: 验证模型密钥已在当前进程设置**

Run:

```powershell
if ([string]::IsNullOrWhiteSpace($env:SILICONFLOW_API_KEY)) { throw "SILICONFLOW_API_KEY is required for the end-to-end baseline" }
```

Expected: 无异常。密钥不得打印。

- [ ] **Step 2: 启动 FastAPI**

Run:

```powershell
New-Item -ItemType Directory -Force logs | Out-Null
$uv = "C:\Users\32957\.local\bin\uv.exe"
$backend = Start-Process -FilePath $uv -ArgumentList @("run","--frozen","uvicorn","main:app","--host","127.0.0.1","--port","8000") -WorkingDirectory "D:\code\deep-searcher-study" -RedirectStandardOutput "D:\code\deep-searcher-study\logs\backend.stdout.log" -RedirectStandardError "D:\code\deep-searcher-study\logs\backend.stderr.log" -WindowStyle Hidden -PassThru
$backend.Id
```

Expected: 返回后台进程 PID。

- [ ] **Step 3: 验证 OpenAPI**

Run:

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/openapi.json).StatusCode
```

Expected: `200`。

- [ ] **Step 4: 检查启动日志**

Run:

```powershell
Get-Content logs/backend.stderr.log -Tail 100
```

Expected: 无模型、Embedding 或 Milvus 初始化异常。

### Task 7: 完成真实 PDF 入库与问答

**Files:**
- Create (ignored): `data/baseline/aurora-facts.pdf`

- [ ] **Step 1: 创建固定事实 PDF**

Run:

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

Expected: 生成 `data/baseline/aurora-facts.pdf`，文件大小大于 0。

- [ ] **Step 2: 调用官方入库接口**

Run:

```powershell
$pdf = (Resolve-Path "data\baseline\aurora-facts.pdf").Path
$body = @{
  paths = @($pdf)
  collection_name = "deepsearcher"
  collection_description = "DeepSearcher original baseline facts"
  batch_size = 256
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/load-files/ -ContentType "application/json" -Body $body
```

Expected: 返回 `Files loaded successfully.`。

- [ ] **Step 3: 验证 Milvus Collection**

Run:

```powershell
& "C:\Users\32957\.local\bin\uv.exe" run --frozen python -c "from pymilvus import MilvusClient; c=MilvusClient(uri='http://127.0.0.1:19530', token='root:Milvus'); print(c.list_collections()); print(c.get_collection_stats('deepsearcher'))"
```

Expected: 列表包含 `deepsearcher`，实体数量大于 0。

- [ ] **Step 4: 调用官方查询接口**

Run:

```powershell
$response = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/query/?original_query=Who%20owns%20Project%20Aurora%20and%20what%20is%20its%20approved%20production%20launch%20date%3F&max_iter=3"
$response | ConvertTo-Json -Depth 8
```

Expected: `result` 同时包含 `Lin Qiao` 和 `September 15, 2026`，并返回 `consume_token`。

- [ ] **Step 5: 验证持久化**

Run:

```powershell
$connection = Get-NetTCPConnection -LocalPort 8000 -State Listen
Stop-Process -Id $connection.OwningProcess
Start-Sleep -Seconds 2
$uv = "C:\Users\32957\.local\bin\uv.exe"
$backend = Start-Process -FilePath $uv -ArgumentList @("run","--frozen","uvicorn","main:app","--host","127.0.0.1","--port","8000") -WorkingDirectory "D:\code\deep-searcher-study" -RedirectStandardOutput "D:\code\deep-searcher-study\logs\backend.stdout.log" -RedirectStandardError "D:\code\deep-searcher-study\logs\backend.stderr.log" -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 5
$response = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/query/?original_query=Who%20owns%20Project%20Aurora%20and%20what%20is%20its%20approved%20production%20launch%20date%3F&max_iter=3"
$response | ConvertTo-Json -Depth 8
```

Expected: 无需重新入库即可返回相同事实。

### Task 8: 记录结果并完成审计

**Files:**
- Create: `docs/verification/2026-07-01-original-baseline.md`

- [ ] **Step 1: 写入实际验证记录**

The report must contain:

```markdown
# DeepSearcher Original Baseline Verification

## Versions

- DeepSearcher upstream commit
- Python
- uv
- PyMilvus
- Milvus server

## Runtime checks

- Docker containers
- Port 19530
- FastAPI OpenAPI
- Loaded collection and entity count

## End-to-end evidence

- Test PDF facts
- Query
- Answer
- Token consumption
- Persistence re-query result

## Git safety

- No API key tracked
- No .env or .venv tracked
- No PDF tracked
- No Milvus volume tracked
- No core DeepSearcher business source modified
```

- [ ] **Step 2: 运行安全扫描**

Run:

```powershell
git status --short
git diff --name-only upstream/master...HEAD
git ls-files | Select-String -Pattern "(^|/)\.env$|\.venv|aurora-facts\.pdf|infra/milvus/volumes"
git grep -n -E "sk-[A-Za-z0-9]{16,}|SILICONFLOW_API_KEY="
```

Expected: 只有设计、计划、Compose、忽略规则、配置和验证报告差异；密钥及运行数据扫描无结果。

- [ ] **Step 3: 验证核心源码未修改**

Run:

```powershell
git diff --exit-code upstream/master...HEAD -- deepsearcher/agent deepsearcher/loader deepsearcher/vector_db deepsearcher/offline_loading.py deepsearcher/online_query.py
```

Expected: 退出码为 0。

- [ ] **Step 4: 提交验证记录**

Run:

```powershell
git add docs/verification/2026-07-01-original-baseline.md
git commit -m "docs: record DeepSearcher baseline verification"
```

Expected: 本地提交成功，不自动推送。
