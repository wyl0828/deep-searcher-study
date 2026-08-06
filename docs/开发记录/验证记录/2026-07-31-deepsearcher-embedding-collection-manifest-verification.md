# DeepSearcher D-01 Embedding/Collection 版本治理验证记录

日期：2026-07-31

## 目标

关闭“只比较向量维度、无法证明查询模型与索引语义空间一致”的数据治理缺口，并验证：

1. Collection 能持久化不含密钥的 Embedding、分块和数据版本契约；
2. 查询在生成 query embedding 和访问向量库前完成兼容性校验；
3. 相同维度、不同模型或版本不能混用；
4. 旧 Collection 默认不可信，用户可用完整原始资料安全重建；
5. 新版本激活失败不影响旧版本，激活后仍有可回滚物理版本。

## 实现结果

### 版本清单

新增 `EmbeddingProfile` 和 `CollectionManifest`，记录：

```text
embedding_provider
embedding_model
embedding_version
embedding_fingerprint
dimension
normalization
metric_type
chunk_size
chunk_overlap
chunk_config_version
document_version
data_version
manifest_fingerprint
```

身份信息只来自 provider、模型配置、显式版本和归一化设置，不保存 API Key、Base URL
或其他 provider 凭据。Embedding profile 和完整 Manifest 分别计算规范化 JSON 的 SHA-256
指纹；缺少必需字段、缺少指纹、字段被修改或使用未知 schema 时均按不可信处理。

### 存储与数据生命周期

- Milvus：使用 `deepsearcher_collection_manifest` Collection property；稳定别名读写时解析到
  当前物理集合。
- Qdrant：使用固定保留 point 保存 Manifest，所有向量查询显式排除该 point。
- Oracle：使用 Collection info 的 description CLOB 保存 Manifest。
- 新建集合在插入向量前写入 Manifest，完成后读回并比较完整指纹。
- 追加资料必须同时匹配 Embedding、metric 和分块配置，成功后更新文档和数据版本。
- 文档向量删除成功后推进 Manifest 数据版本，并同步回产品数据库。
- Qdrant 写入、搜索和列表错误不再被吞掉或转换成空结果。

### 查询前保护

DeepSearch、ChainOfRAG 和 NaiveRAG 在所有正式查询入口执行：

```text
解析并限制 Collection 范围
  → 读取每个 Collection Manifest
  → 比较 provider / model / version / dimension / normalization
  → 全部兼容后才生成 query embedding
  → 执行向量检索
```

缺少 Manifest、Manifest 损坏、存储不支持治理或 profile 不兼容都会返回稳定的 409
错误。测试确认同维度的 `embedding-a` 与 `embedding-b` 在 `embed_query()` 和向量搜索调用前
被拒绝。

### 迁移与用户界面

- 核心重建 API 仅接受产品生成的 `kb_<32 hex>` Collection，并要求
  `X-Confirm-Collection` 精确匹配。
- 重建参数限制路径数量、描述长度、chunk 和 batch 边界。
- 重建使用 D-02 的候选写入、读回验证、稳定别名切换和旧版本保留机制。
- 产品数据库增加 `index_manifest` 与 `index_previous_collection`，启动时可自动升级旧
  SQLite schema，同时提供 Alembic migration。
- 知识库列表标记“需重建”；详情页解释保护原因并提供“立即重建”。
- 未验证索引会禁用首页输入、历史对话重新生成和“开始提问”，避免发起已知无效请求。
- 重建成功后页面即时显示模型、版本、维度和分块参数；完整重建成功的旧失败文档会恢复为
  ready。

## 自动化验证

全量结果：

```text
Python:      612 passed, 9 skipped
Frontend:    3 files passed, 19 tests passed
TypeScript:  tsc --noEmit passed
Build:       vite build passed
Ruff:        D-01 修改范围 passed
```

覆盖范围包括 Manifest 指纹与防篡改、旧集合保护、追加兼容性、三种 Agent 查询前拒绝、
Milvus/Qdrant/Oracle 存储、核心 Manifest 与重建 API、产品数据库迁移、产品重建服务和
React 用户引导。

全仓 Ruff 还报告了本次范围外的
`scripts/generate_mobile_learning_pdf.py` 原有 import 排序和未使用 import；为避免改动用户的
无关文件，本次未处理。Python 全量测试中的两个 crawler coroutine warning 也与 D-01 无关，
不影响测试通过。

## 真实 Milvus 与浏览器验证

### 隔离集成测试

`tests/integration/test_d01_collection_manifest_live.py` 和 D-02 回归同时执行：

```text
2 passed in 10.26s
remaining d01_/d02_ temporary collections: []
```

D-01 场景写入 profile A，确认同维度 profile B 在查询前失败；随后用 B 构建候选并激活，
分别验证新旧物理版本，回滚后 A 恢复可用且 B 被阻止。

### 实际用户链路

对升级前的“Milvus 学习资料”知识库执行：

1. 知识库列表显示“需重建”；
2. 详情页显示“需要重建索引”和保护原因，不显示“开始提问”；
3. 点击“立即重建”，使用原始 PDF 构建候选；
4. 候选验证成功后，页面变为“索引版本已验证”；
5. 页面显示 `text-embedding-v4`、`1024` 维和 `1500 / 100` 分块配置；
6. 核心 Manifest API 返回 `compatible=true`；
7. Milvus 中稳定别名指向新 `__v_...` 集合，旧 `__previous_...` 集合仍存在；
8. 对该稳定 Collection 发起真实问答，成功返回有依据的 Milvus 说明。

界面截图：

- [重建前：旧索引保护提示](../../../output/playwright/2026-07-31-d01-legacy-index-rebuild.png)
- [重建后：已验证索引状态](../../../output/playwright/2026-07-31-d01-verified-index.png)

## 结论

D-01 验收标准已满足：查询能够校验模型与数据契约，同维度不同模型不会进入旧语义空间；
旧知识库具有用户可见的安全迁移入口；Milvus 候选切换后保留上一物理版本，并已真实验证
profile 切换和回滚。默认策略是 fail closed：无法证明兼容的旧索引必须完整重建，不使用
“维度相同即兼容”的推测。
