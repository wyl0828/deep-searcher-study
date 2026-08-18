# P2-A 多格式解析与分块验证记录

> 日期：2026-08-17
> 阶段：对标 Ragent 企业级缺口优化计划 P2-A（企业文档能力·多格式解析/分块）
> 参考：`D:\code\reference\ragent`（提交 `020e5c3`）的
> `core/parser/`（MimeTypeDetector / ParserRegistry / ExcelDocumentParser / ExcelTableNormalizer）与
> `core/chunk/blockaware/`（BlockAwareChunkerDispatcher / TableChunker / CodeChunker / ImageChunker）
> 结论：✅ 全部通过

---

## 1. 目标

补齐 Excel/PPTX/图片解析与表格/代码分块，职责边界固定为：
**Loader 识别结构 → Splitter 保护结构（table/code 原子块 + 生成 embedding-only `vector_text`）→
Embedding 唯一入口选择 `chunk.vector_text or chunk.text` → vector store/retrieval/citation 只认识既有 `Chunk.text`**。

## 2. 实现清单

| 项 | 文件 | 说明 |
|---|---|---|
| 注册表 | `deepsearcher/loader/file_loader/registry.py` | `LoaderRegistry`：**序列注册**（重复认领启动失败，可真实检测，避免 dict 静默覆盖）；`REQUIRED_EXTENSIONS` 自检全覆盖；未知扩展名显式报错；可选依赖 loader（docling/unstructured）**惰性实例化**，路由时才加载 |
| MIME | `deepsearcher/loader/file_loader/mime_type.py` | 容器/signature 族校验覆盖全集合：PDF/PNG/JPEG 签名、OOXML ZIP+`[Content_Types].xml`+`xl/word/ppt`、OLE CFB（doc/xls/ppt 仅验容器）、RTF、ODT/EPUB（mimetype 条目）、SVG/HTML/文本族（可打印性阈值）；**扩展名为最终路由，MIME 只做上传真实性校验** |
| Excel | `deepsearcher/loader/file_loader/excel_loader.py` | 每可见 sheet → `kind="table"` block；**双 workbook 读取**（`data_only=True` 缓存优先、为空回退公式串）；合并单元格在 matrix 展开左上角值（不写回 MergedCell）；`header_rows` 默认 1、隐藏 sheet 跳过、多行表头 `|` 展平、超链接 `[text](url)`、cell `\|`/换行转义、全空行列丢弃 |
| PPTX | `deepsearcher/loader/file_loader/pptx_loader.py` | slide 级多 block（`kind="text"`/`kind="table"`），共享 slide 元数据；表格整体成块 |
| 图片 | `deepsearcher/loader/file_loader/image_loader.py` | 仅 OCR（不引入视觉模型）：`text=OCR 文本`、`vector_text=None`；OCR 无文本返回空列表；`confidence`=非空 line score 均值 |
| OCR | `deepsearcher/loader/file_loader/ocr.py` | 抽取共享 `create_ocr_engine`；PDFLoader 行为不变（内部改调共享 factory），ImageLoader 共用 |
| 表格文本 | `deepsearcher/loader/file_loader/table_text.py` | `render_markdown_table`（展示/引用）+ `render_key_value_rows`（向量）共用实现（对齐 TableChunker 双渲染） |
| 分块 | `deepsearcher/loader/splitter.py` | `Chunk.vector_text`（embedding-only transient）；`split_docs_to_chunks` API 不变，内部 `kind` 分发 + **code extraction 先于 `_section_documents`**（fenced 代码先保护，prose 走原 section/window 链路；无代码文档行为逐字节不变）；TableAwareSplitter（整表成块/超长按行组分块重带表头/单行原子）对齐 TableChunker；CodeBlockSplitter（整块保留/超长按行切）对齐 CodeChunker |
| Embedding | `deepsearcher/embedding/base.py` | 唯一改动点：`texts = [getattr(chunk, "vector_text", None) or chunk.text …]`（getattr 兼容 duck-typed mock） |
| 配置 | `deepsearcher/config.yaml` | `file_loader.provider` 默认 `LoaderRegistry`（drop-in，`load_from_local_files` 零改动） |
| 上传 | `frontend/product/services/documents.py` | `stage_upload`（扩展名白名单 + MIME 容器校验，415 拒绝）、`inspect_document_pages`（pdf=页数、xlsx=可见 sheet 数、pptx=slide 数、图片/其他=1）；`server.py` 直连端点同步更新 |
| 依赖 | `pyproject.toml`/`uv.lock` | 新增 `openpyxl`、`python-pptx`（运行时）+ `xlsxwriter`（dev，fixture 生成） |

## 3. 验证矩阵

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | Registry：默认矩阵全覆盖、未认领报错、**重复认领启动失败**、惰性实例化缓存 | ✅ | `test_registry.py` 6 用例（REQUIRED 22 扩展名全覆盖；`a.xlsx`→ExcelLoader 等；`file.xyz` 抛 LoaderUnclaimedError；序列内双认领抛 LoaderRegistrationConflict；工厂只 resolve 一次） |
| 2 | MIME：签名/容器族全覆盖 + 冲突拒绝 | ✅ | `test_mime_type.py` 12 用例（PDF/PNG/JPEG/RTF/CFB/OOXML/ODT/EPUB/SVG/HTML/文本族；`validate_upload` 匹配接受、`.md` 内容二进制拒绝、未知扩展名拒绝） |
| 3 | Excel：公式缓存/回退、合并展开、隐藏 sheet、多行表头、转义 | ✅ | `test_excel_loader.py` 6 用例（**checked-in fixture `formula-cached.xlsx`** 由 xlsxwriter 预计算含 `<v>`：缓存值 300 读取；openpyxl 无缓存公式回退 `=B2+B3`；隐藏 sheet 跳过且 `sheet_count`=可见数；`header_rows=2` → `财务|收入`；`a\|b`/`<br>` 转义） |
| 4 | PPTX：slide 级 text/table 多 block、共享元数据 | ✅ | `test_pptx_loader.py`（表格独立 `kind="table"` + `_table_headers/_table_rows`；slide_number/total_slides 共享） |
| 5 | 图片：OCR 注入、confidence 均值、无文本空列表 | ✅ | `test_image_loader.py` 3 用例（0.9/0.8→0.85；低分 line 丢弃；boxes=None→`[]`） |
| 6 | 分块：表格双文本、超长分块重带表头、代码围栏+裸码、**fenced 代码先于 section 保护**、无代码回归 | ✅ | `test_structured_splitter.py` 6 用例（`vector_text="指标: 收入; 数值: 100"`；40 行表→多块每块 `| c1 | c2 |` 开头；`# 这不是 Markdown 标题` 不产生 section 且进 `vector_text`；普通文档行为不变） |
| 7 | Embedding 契约：`vector_text` 优先/回退、**不泄漏到 payload/identity/manifest** | ✅ | `test_embedding_contract.py`（embed_documents 收到 `["指标:…", "plain text"]`；payload 用 `chunk.text` 且无 `vector_text`） |
| 8 | 上传 e2e：xlsx/pptx/图片 202 + `page_count` 语义 + 未知/冲突 415 | ✅ | `test_product_api.py` 3 个新用例（xlsx `page_count=2`、pptx `=1`、png `=1`；`.xyz` 415；`.md` 二进制内容 415） |
| 9 | 全量 pytest | ✅ | `pytest tests frontend/tests`：1107 passed，11 skipped |
| 10 | 完整 quality_gate | ✅ | `scripts/quality_gate.py` 全 18 步 PASS（ruff/pytest/前端/迁移/评估门/mkdocs/git-diff） |

## 4. 过程中发现并就地修复的问题

1. **`JsonFileLoader` 必需 `text_key` 参数**：默认注册表改为 `JsonFileLoader(text_key="")`。
2. **可选依赖（docling/unstructured）未安装**：LoaderRegistry 默认注册改为**惰性工厂**（类/lambda），首次路由才实例化并缓存；缺失时给出明确 `LoaderUnclaimedError`（"可能缺少可选依赖"），不阻塞启动。
3. **staging `.part` 后缀被 openpyxl 拒绝**：`_inspect_xlsx_sheets` 改经 `BytesIO` 读字节，绕过 openpyxl 按扩展名校验。
4. **GeminiEmbedding 测试的 `MockChunk` 无 `vector_text`**：`embed_chunks` 用 `getattr(chunk, "vector_text", None)` 兼容鸭子类型。
5. **git-diff-check（CRLF）**：既有 `tests/loader` 文件（ruff exclude 范围外）被显式 ruff 修复误改 → `git checkout` 还原；`embedding/base.py` 行尾还原为 LF 后重新最小修改，`git diff --check` 干净。

## 5. 结论

P2-A 已闭环：Excel/PPTX/图片可上传入库且可检索；表格整体成块并生成 key-value 向量文本、
markdown 展示文本（双文本契约经 embedding 契约测试锁死不泄漏）；fenced 代码先于 section/window
被保护；`LoaderRegistry` 显式扩展名所有权 + 自检 + 惰性可选依赖；MIME 容器族校验覆盖全集；
`split_docs_to_chunks` API 与普通文档行为不变；全量门禁 18 步全绿。

## 6. 遗留

- `xls/doc/ppt` 走 UnstructuredLoader（CFB 仅验容器，未做内部 subtype 精确识别）；`xlsx` 走专用 ExcelLoader。
- 缩进代码块（非 fenced）不做启发式识别（避免误判，仅 fenced 可靠识别）。
- 图片理解/描述（caption）不在 P2-A（属未来 Image Understanding，届时升级 `text=caption+OCR`、`vector_text=caption+OCR`）。
- P2-B（入库可编排 IngestionEngine）未开始，属下一阶段。