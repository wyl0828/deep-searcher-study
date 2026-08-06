# DeepSearcher 用户工作台 P0 前置能力验证

> 日期：2026-07-24  
> 范围：显式知识库检索范围、真实 PDF 引用元数据  
> 不在本轮范围：用户界面、产品数据层、异步入库任务、实时 Milvus 服务验收

## 1. 验证结论

两个用户工作台 P0 阻塞项已经在核心代码层打通：

1. 调用方可以传入 `collection_names`，NaiveRAG、ChainOfRAG 和 DeepSearch 都只检索指定集合，并绕过 Collection Router。
2. PDFLoader 按真实页拆分文档，生成稳定内容哈希 `document_id` 和真实 `page_number`；切片阶段补充跨页连续的 `chunk_index`。
3. Milvus 适配器现有 JSON `metadata` 字段可以保存并恢复上述元数据。
4. Trace 只白名单输出 `display_name/document_id/page_number/chunk_index`，不会把其他元数据或本地目录直接返回。
5. 未传 `collection_names` 时保留原有自动路由行为；显式传空列表时不会回退到全部知识库。

这说明“用户选择哪个知识库，就只查询哪个知识库”和“引用展示真实文件页码”已经具备核心实现基础。它还不等于完整用户产品已完成。

## 2. 首次复现

修正前运行定向验证：

```powershell
uv run --frozen pytest tests/agent/test_collection_scope.py tests/test_p0_knowledge_citations.py tests/test_trace.py tests/loader/file_loader/test_pdf_loader.py -q
```

结果：`8 failed, 10 passed`。

失败与预期一致：

- 三种 Agent 没有统一接受显式知识库范围。
- `query_with_trace` 没有转发知识库范围。
- PDFLoader 把 4 页 PDF 合并成单个 Document。
- Trace 没有输出真实引用定位字段。

## 3. 修正后验证

### 3.1 定向验证

使用仓库中的真实 4 页文件 `examples/data/WhatisMilvus.pdf`，覆盖：

```text
PDF 页面解析
→ 文档与切片元数据
→ Milvus 插入数据结构
→ Milvus 检索结果恢复
→ Trace 安全引用输出
```

结果：`18 passed`。

### 3.2 相关回归

```powershell
uv run --frozen pytest tests/agent tests/loader/test_splitter.py tests/loader/file_loader/test_pdf_loader.py tests/test_trace.py tests/test_p0_knowledge_citations.py -q
```

结果：`58 passed`。

变更文件 Ruff 检查结果：`All checks passed`。  
`git diff --check` 无空白错误。

### 3.3 扩大测试范围

排除 Windows 当前不支持的 Milvus Lite 测试后，运行其余测试：

```powershell
uv run --frozen pytest tests -q --ignore=tests/vector_db/test_milvus.py
```

结果：`428 passed, 1 skipped, 7 failed`。默认跳过项是需要显式环境变量启用的实时 Milvus 测试。

7 个失败位于既有 JiekouAI、OpenAI 和 XAI 默认配置断言，涉及默认模型或本地环境 `base_url`，本轮未修改这些模块，与知识库范围和 PDF 引用链路无关。

## 4. 实时 Milvus 纵向验收

第二轮恢复 Docker Desktop，并启动仓库的 Milvus Compose 环境：

- Docker Server：`29.4.1`
- Milvus Server：`2.5.8`
- Milvus URI：`http://127.0.0.1:19530`
- `milvus-standalone`：`healthy`

新增可选择执行的实时测试：

```powershell
$env:DEEPSEARCHER_RUN_LIVE_MILVUS='1'
uv run --frozen pytest tests/integration/test_p0_milvus_live.py -q -s
```

结果：`1 passed`。

该测试实际完成：

1. 创建两个唯一命名的临时 Milvus 集合。
2. 将真实 4 页 PDF 的切片写入选中集合。
3. 在另一个集合写入可识别的诱饵文档。
4. 从 `query_with_trace` 传入显式 `collection_names`。
5. 确认 Collection Router 没有执行，Milvus 只收到一次对选中集合的搜索。
6. 确认结果不包含诱饵文档。
7. 确认 Trace 返回真实文件名、文档 ID、1～4 页内的真实页码和切片编号。
8. 无论测试成功或失败，都在 `finally` 中删除两个临时集合。

测试后重新列出集合，只剩原有的 `deepsearcher/day1_milvus/test_milvus`，没有残留 `p0_scope_*` 数据。

HTTP `/query/` 同步增加重复查询参数形式的 `collection_names`，并验证能够完整转发到核心查询入口：

```powershell
uv run --frozen pytest tests/test_query_api.py -q
```

结果：`4 passed`。

## 5. 当前边界与下一步

“显式知识库范围”和“真实 PDF 引用元数据”两个核心前置能力现已完成单元、适配器和实时 Milvus 三层验证。

用户工作台 P0 仍未完成，后续还需要：

1. 建立产品数据库和知识库稳定 ID。
2. 由产品 API 将知识库 ID 安全映射为内部 `collection_name`。
3. 实现真实文档任务状态、会话和消息保存。
4. 实现用户页面并完成浏览器纵向验收。

当前 Windows 环境仍不支持仓库的 Milvus Lite 测试路径，但不影响已经通过的 Docker Milvus 2.5.8 产品基线路径。
