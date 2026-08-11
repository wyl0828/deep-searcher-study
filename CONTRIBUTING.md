# 贡献指南

感谢你关注 DeepSearcher Study。它是一个独立维护的 Apache-2.0 衍生项目，重点是可信 RAG 的证据管理、
答案核验与质量评测；项目与上游的关系、许可和同步原则见 [UPSTREAM.md](UPSTREAM.md)。

## 开始之前

1. 阅读 [README.md](README.md)、[路线图](docs/roadmap/trustworthy-rag-roadmap.md) 和相关 ADR，确认改动符合当前维护方向。
2. 对于通用且适合上游的修复，请保持为小而独立的变更，考虑向上游项目单独提交；不要把尚未被上游接纳的内容表述为上游能力。
3. 不要提交 API Key、服务令牌、用户资料、学习记录、面试材料、真实业务文档、运行日志或本地数据库。

## 本地环境

项目主要在 Windows 本地工作台环境中维护，需要 Python 3.10+、[uv](https://docs.astral.sh/uv/getting-started/)、
Node.js 20+，以及 Docker Desktop（用于 Milvus）。

```powershell
git clone https://github.com/wyl0828/deep-searcher-study.git
Set-Location deep-searcher-study
Copy-Item env.example .env
uv sync --frozen
```

当前默认配置使用 DeepSeek 生成模型和 OpenAI Embedding；按 `deepsearcher/config.yaml` 与 `.env` 配置所需密钥。
不要把 `.env` 或本地数据加入提交。

## 提交前检查

请先运行快速质量门禁：

```powershell
.\scripts\run-quality-gate.ps1 -Mode Fast
```

它覆盖 Python 检查与测试、前端测试/类型检查/构建、Chromium E2E、迁移、Trust 金标评测、文档构建和
`git diff --check`。如果改动影响真实检索、模型调用或评测阈值，请额外阅读
[evaluation/README.md](evaluation/README.md)，明确记录数据集、配置和成本边界。

## Pull Request 要求

- 从 `study-baseline` 创建分支，并说明问题、设计取舍、测试命令和结果。
- 改动 Trust、Policy、Risk、Freshness 或 Provenance 时，同步更新对应 ADR、数据集或测试；不要用 Stub
  结果声称真实模型质量。
- 保持声明可核验：性能、质量和安全结论必须标明运行条件与已知限制。
- 每个提交应聚焦单一主题；避免格式化无关文件或混入本地学习材料。

维护者会根据项目路线、质量证据、可维护性和安全边界进行审查。
