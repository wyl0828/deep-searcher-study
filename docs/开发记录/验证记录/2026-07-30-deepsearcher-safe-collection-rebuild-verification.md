# DeepSearcher D-02 安全集合重建验证记录

日期：2026-07-30

## 目标

关闭 `force_new_collection=True` 原地删除已有向量数据的风险，并验证以下闭环：

1. 输入、解析或 Embedding 失败时不修改已有 Collection；
2. 重建时先写入独立候选版本，完成后再激活；
3. Milvus 切换后旧版本仍可检索并可回滚；
4. 业务整库删除具备服务端范围校验和精确确认；
5. 失败状态通过稳定错误协议返回，不伪装成成功。

## 实现结果

### 驱动层

- `Milvus.init_collection()`、`Qdrant.init_collection()` 和
  `OracleDB.init_collection()` 不再因 `force_new_collection=True` 删除已有集合。
- 已存在集合的原地覆盖会抛出
  `VECTOR_UNSAFE_COLLECTION_REPLACEMENT`。
- Milvus 支持版本候选激活与回滚：
  - 已使用别名时，通过 `alter_alias` 原子切换；
  - 首次切换时，将原物理集合改名为 `__previous_...` 保留版本；
  - 新候选使用 `__v_...` 内部名称；
  - 列表接口隐藏内部物理版本，只暴露稳定别名；
  - 删除稳定别名时只级联删除严格归属于该别名的内部版本。
- 版本归属使用完整内部命名格式校验，避免短前缀误删其他集合。
- 兼容当前 Milvus 服务将 `list_aliases` 返回为字典、而 Python 客户端类型标注为列表的差异。

### 加载层

- 本地文件入口先验证全部路径，再解析、切分和生成 Embedding。
- 网站入口先完成抓取、切分和 Embedding。
- 只有准备成功后才初始化并写入 Collection。
- 强制重建流程为：

```text
准备文档与向量
  → 创建独立候选版本
  → 写入完整候选数据
  → 切换稳定别名
  → 保留旧版本用于回滚
```

- 候选写入失败会清理未激活候选；激活失败会保留候选和旧版本，避免数据丢失。
- 不支持别名切换的驱动返回 `manual_activation_required=true`，不覆盖旧集合。
- CLI 的强制重建参数改为显式布尔开关，避免 `type=bool` 将字符串错误解析为真值。

### 整库删除边界

- 核心 API 只允许删除产品生成的 `kb_<32 位十六进制>` Collection。
- 请求必须携带 `X-Confirm-Collection`，且值必须与路径中的 Collection 完全一致。
- 产品服务已发送确认头；无确认、确认不一致和非产品 Collection 均在调用向量驱动前被拒绝。
- 请求和完成阶段写入审计日志。

## 自动化验证

定向测试覆盖：

- 已存在 Collection 的强制初始化不会调用删除；
- 输入或 Embedding 失败发生在任何存储修改之前；
- 候选写入失败只清理候选，不激活；
- 激活失败保留旧版和完整候选；
- 首次物理集合迁移、别名切换、回滚和失败恢复；
- 内部版本列表隐藏与安全范围级联删除；
- API 产品范围、精确确认头和安全错误码；
- 产品知识库删除向核心 API 传递确认头。

全量结果：

```text
Python:   582 passed, 8 skipped
Frontend: 3 test files passed, 17 tests passed
TypeScript: tsc --noEmit passed
Build:    vite build passed
Ruff:     changed D-02 files passed
```

## 真实 Milvus 验证

测试文件：
`tests/integration/test_d02_milvus_versioning_live.py`

使用随机临时集合执行：

1. 写入 `old-version`；
2. 验证直接强制初始化被安全错误阻止，旧数据仍可检索；
3. 写入 `new-version` 候选并激活稳定别名；
4. 验证稳定别名检索新版，保留版本检索旧版；
5. 验证集合列表只展示稳定别名；
6. 回滚别名并验证旧版重新生效，新版仍可通过物理版本检索；
7. 删除别名及其内部版本，确认无 `d02_` 临时集合残留。

最终结果：

```text
1 passed in 7.04s
remaining temporary collections: []
```

首次真实测试还暴露了 `list_aliases` 响应结构兼容问题。增加归一化处理后，同一测试完整通过。

## 结论

D-02 的原地误删路径已关闭。当前 Milvus 重建具备“先构建、后切换、旧版保留、可回滚、删除需确认”的完整闭环；其他不支持别名切换的驱动采用保守失败和人工激活语义，不会覆盖已有数据。
