# 上游来源与维护边界

## 项目关系

DeepSearcher Study 是 [zilliztech/deep-searcher](https://github.com/zilliztech/deep-searcher) 的
Apache License 2.0 衍生项目。本仓库由独立维护者负责，不代表、隶属或代言上游项目及其维护团队。

本项目的工作重点是可信 RAG：证据管理、答案核验、时效与风险约束、可审计谱系，以及与这些能力对应的
评测和产品化路径。它不应被描述为上游 DeepSearcher 的官方版本或由上游背书的发行版。

## 可追溯性

- `upstream` Git remote 指向 `https://github.com/zilliztech/deep-searcher.git`。
- Git 历史保留项目演进记录；使用 `git remote -v`、`git log --graph --decorate --all` 与
  `git merge-base HEAD upstream/master` 可检查来源和分叉关系。
- 独立实现、设计取舍、评测数据集和后续计划记录在
  [CHANGELOG.md](CHANGELOG.md)、[docs/adr/](docs/adr/) 与
  [docs/roadmap/trustworthy-rag-roadmap.md](docs/roadmap/trustworthy-rag-roadmap.md)。

## 许可与归属

- 本仓库继续以 [Apache License 2.0](LICENSE) 发布，并保留上游代码、资源和文档中适用的版权、许可及归属信息。
- 新增代码和文档除非在文件中另有声明，均按同一许可证提供。
- 对上游的引用用于说明来源，不表示任何商标许可、合作关系或官方认可。

## 维护与同步原则

1. **透明衍生**：README、发布说明和对外材料始终说明上游来源与本仓库的独立维护身份；不通过删除归属信息制造原创误解。
2. **可核验优先**：功能主张要配套测试、ADR、评测或可复现命令。模型评测、性能与质量结论必须标注数据集和范围。
3. **安全同步**：评估上游更新时先比较依赖、许可、契约和测试影响；不为追随上游而覆盖本仓库的可信回答边界。
4. **回馈上游**：如果某项改动适合通用 DeepSearcher，会以聚焦、可独立审查的 Issue 或 Pull Request 提交；
   在被接纳之前，不把本仓库的改动表述为上游能力或上游认可。

## 对使用者的说明

请以本仓库的提交、发布标签和评测报告判断可用功能。对于高风险决策，Trust Layer 的保守拒答、未知状态和
可选语义核验是降低错误确定性的机制，不是对结果正确性的保证。
