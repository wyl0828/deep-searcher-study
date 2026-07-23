# DeepSearcher 原版基线验证记录

## 验证状态

已于 2026-07-01 通过验证。已确认官方源码基线、锁定的 Python 环境、Docker Milvus、FastAPI、真实 PDF 入库、Agent 查询、重启后的持久化查询、重点测试和 Git 安全检查均可正常工作。

## 版本信息

- DeepSearcher 上游提交：`d89e37cdfbbef5e44ae6162ce9cc2c627a69b7e1`
- 当前工作分支：`study-baseline`
- uv：`0.7.8`
- Python：`3.10.20`
- PyMilvus：`2.5.8`
- Milvus 服务端：`2.5.8`
- Docker Engine：`29.4.1`
- LLM：阿里云百炼 OpenAI 兼容接口，模型为 `qwen-plus`
- Embedding：阿里云百炼 OpenAI 兼容接口，模型为 `text-embedding-v4`，维度为 1024

基线使用 uv 0.7.8，因为当前的 uv 0.11 会重写仓库中较旧的锁文件。所有项目命令都使用 `--frozen`，因此 `uv.lock` 与上游保持一致。

## 运行环境检查

- Docker Desktop 正在使用 WSL2 Linux 引擎运行
- `milvus-etcd`、`milvus-minio` 和 `milvus-standalone` 均为健康状态
- TCP `127.0.0.1:19530` 可连接
- PyMilvus 检测到 Milvus 服务端版本 `2.5.8`
- 通过 DeepSearcher 的 Milvus 封装成功插入、检索并删除了一条测试向量
- FastAPI 已使用当前配置的 LLM、Embedding、文件加载器和 Milvus 模块完成初始化
- 启动冒烟测试期间，`GET http://127.0.0.1:8500/openapi.json` 返回 HTTP 200

本机的 Windows/Hyper-V 预留了 TCP 端口 `7942-8041`，其中包含上游默认端口 8000。直接套接字测试在 8000 端口复现了 `WinError 10013`，而 8500 可正常使用，因此本地基线使用 8500，未修改 `main.py` 的默认端口。

## 自动化检查

以下重点上游测试无需调用真实模型服务，且全部通过：

```text
tests/llm/test_siliconflow.py
tests/embedding/test_siliconflow_embedding.py
tests/agent
tests/loader/test_splitter.py
tests/loader/file_loader/test_pdf_loader.py
tests/utils/test_log.py
```

最终结果：`78 passed in 1.95s`。

## 测试 PDF

- 本地路径：`data/baseline/aurora-facts.pdf`
- SHA-256：`2267DFC442A999B58E9AED4CD7DA970586F5FB5B9DAF6B51D92B2F2D6F13A99A`
- Git 状态：已被 `/data/` 忽略
- 视觉检查：一页可正常阅读的 A4 页面，没有裁切或重叠
- 文档中的事实：
  - Project Aurora 的负责人是 Lin Qiao（林乔）
  - 批准的生产上线日期是 2026 年 9 月 15 日
  - 批准预算为 240 万元人民币
  - 主要部署区域是上海

## 模型服务问题定位

第一次真实入库时，PDF 解析和 `deepsearcher` 集合创建成功，但在 Embedding 环节失败：

- `POST https://api.siliconflow.cn/v1/embeddings`：HTTP 401
- 单独请求 `GET https://api.siliconflow.cn/v1/models`：HTTP 401
- `.env` 检查：只有一个 Key 配置项；没有 BOM、包围引号、首尾空格或占位文本
- 此时 Milvus 集合 `deepsearcher` 的 `row_count = 0`

原因是用户提供的凭据属于阿里云百炼北京工作空间，并不是 SiliconFlow。切换到该工作空间的 OpenAI 兼容接口后：

- `GET /models` 返回 HTTP 200，共列出 220 个模型
- `text-embedding-v4` 探测成功，返回 1024 维向量
- `qwen-plus` 探测成功，准确返回 `OK`，消耗 14 Token
- `.env` 现在保存 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`；该文件保持忽略状态，配置值从未输出
- 未使用 Anthropic 兼容接口，因为 OpenAI 兼容接口同时支持聊天和 Embedding

## 端到端验证证据

- FastAPI 启动：`GET http://127.0.0.1:8500/openapi.json` 返回 HTTP 200
- PDF 入库：`POST /load-files/` 返回 `Files loaded successfully.`
- 直接查询 Milvus 能返回 PDF 的完整文本
- flush 后，集合 `deepsearcher` 的 `row_count = 1`
- 查询问题：`Project Aurora 的负责人是谁，批准的生产上线日期是什么？`
- Agent 路由：`ChainOfRAG`，共执行三轮搜索，命中一个文档块
- 回答：Project Aurora 的负责人是 Lin Qiao，批准的生产上线日期是 2026 年 9 月 15 日
- Token 消耗：`1795`
- 持久化：停止并重启 FastAPI 后，无需重新入库，同一个 `max_iter=3` 查询仍能返回相同事实

## Git 安全检查

- `.env`、`.venv`、`data/`、`logs/` 和 `infra/milvus/volumes/` 均已忽略
- 测试 PDF 和 Milvus 数据未被 Git 跟踪
- 当时未修改 DeepSearcher 的 Agent、Loader、Vector DB、离线加载或查询核心实现
- 没有 API Key 被写入受 Git 跟踪的文件
- 最终受跟踪文件运行风险扫描结果：`0`
- 最终受跟踪文件敏感信息模式扫描结果：`0`
- `uv.lock` 与 `upstream/master` 一致
- 当时相对上游的变更仅包括 `.gitignore`、`deepsearcher/config.yaml`、设计/计划/验证文档和 `infra/milvus/docker-compose.yml`
