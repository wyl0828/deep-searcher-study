# DeepSearcher P-01 PDF 解析与来源定位验证记录

日期：2026-07-31

## 目标

关闭“只能引用文件名、无法可靠定位原文”的缺口，并验证：

1. 文本 PDF、扫描件、表格和多栏 PDF 能进入各自适合的解析路径；
2. Chunk 保留页码、章节、字符区间、版面坐标和解析器版本；
3. 定位字段能经过 Milvus、Trace、产品持久化和 API 到达用户界面；
4. 用户能从引用卡片打开原始 PDF 的对应页；
5. 新分块算法不会破坏升级前的 Collection Manifest。

## 实现结果

### 自适应 PDF 解析

`PDFLoader` 现在每页输出一个 LangChain `Document`，并记录：

```text
page_number / page_label / total_pages
parser_version
extraction_method / extraction_confidence
layout_type
page_width / page_height
line spans / section spans
```

- 文本页使用 pdfplumber 的单词坐标、字体名和字号重建行，并去除重复字符。
- 按字体比例、粗体和句末特征识别章节层级，章节栈可以跨页延续。
- 版面检测发现稳定中缝后，按左栏再右栏输出；跨栏标题作为阅读顺序分隔点。
- 有效二维表格转换为带 `[Table N]` 标识的 Markdown 表格，并从普通文本行中去重。
- 文本层少于阈值时惰性加载 RapidOCR，渲染当前页后识别文字，把 OCR 像素框换算回
  PDF 页面坐标。

解析器版本为 `pdfplumber-layout-v2+rapidocr-v3`。

### 章节感知分块与稳定定位

`split_docs_to_chunks()` 先按解析器给出的章节边界拆分，再使用带 `start_index` 的递归字符
分块器，避免相同文本导致 offset 命中错误。每个 Chunk 增加：

```text
section_title / section_path
char_start / char_end
bbox
location_id
source_locator
parser_version / extraction_method
```

`bbox` 为 `[x0, y0, x1, y1]` 的 0～1 归一化坐标；`location_id` 由文档、页码、字符区间和
章节生成稳定 SHA-256 前 24 位。内部 `_line_spans` 和 `_section_spans` 在写入向量库前移除，
避免放大 metadata。

### 引用持久化与用户界面

- Trace 仅序列化白名单中的定位字段，同时校验字符范围、bbox 有限值和控制字符。
- 产品 Citation 增加 9 个可空定位列，并提供 Alembic migration 与旧 SQLite 启动升级。
- 产品原文接口只允许读取上传根目录内的文件，以 `inline application/pdf` 返回，并设置
  private cache 与 `nosniff`。
- 引用抽屉显示页码、章节、字符区间和 OCR 标记；“打开原文第 N 页”使用新标签打开
  `/api/documents/{id}/content#page=N`。
- 删除文档后历史引用仍保留快照，但明确显示原文已从知识库删除。
- 引用卡片与原文链接都有键盘焦点样式；选中引用后焦点仍落在可操作按钮上。

### Manifest 向后兼容

章节感知分块会改变 `chunk_config_version`。Collection Manifest 因此升级到 schema v2，
显式保存 `chunk_algorithm=section-aware-recursive-character-window-v2`。读取 v1 时固定采用
旧算法 `recursive-character-window-v1` 重算并校验指纹：

- 合法 v1 索引仍可启动、查询和完整重建；
- 损坏或被篡改的 v1/v2 清单仍 fail closed；
- v1 与 v2 分块契约不能直接追加混用；
- 新建和重建索引写入 v2 清单。

## 自动化验证

解析回归文件：

```text
tests/loader/file_loader/test_pdf_layout_regression.py
```

覆盖：

1. 文本 PDF：标题识别、章节边界、页内 offset、bbox 和稳定 locator；
2. 扫描 PDF：真实 RapidOCR 回退、识别置信度和坐标；
3. 表格 PDF：表格识别、Markdown 序列化和表格 bbox；
4. 多栏 PDF：左栏优先的阅读顺序；
5. `examples/data/WhatisMilvus.pdf`：真实页码、标题和 Chunk 定位。

生成的四类 PDF 均使用 Poppler 渲染并逐页视觉检查，确认样例本身没有裁切、遮挡或错误
布局。全量回归结果：

```text
Python:      621 passed, 9 skipped
Frontend:    3 files passed, 19 tests passed
Build:       vite build passed
Ruff:        P-01 修改范围 passed
```

Python 全量测试有 1 条现存 crawler coroutine warning，与本次 PDF 和引用链路无关。

## 真实产品链路

对“Milvus 学习资料”执行完整重建：

```text
schema_version:       2
chunk_algorithm:      section-aware-recursive-character-window-v2
chunk_size/overlap:   1500 / 100
embedding_model:      text-embedding-v4
dimension:            1024
index_status:         verified
```

真实问题：

```text
What is Milvus? Answer only with the definition stated in the document.
```

真实引用结果：

```text
document:             WhatisMilvus.pdf
page:                 1
section:              What is Milvus?
char range:           0–646
bbox:                 [0.063176, 0.074733, 0.929675, 0.253912]
location_id:          d252f440457403b75408df3e
parser:               pdfplumber-layout-v2+rapidocr-v3
extraction:           text
```

原文接口返回 `200 application/pdf`、`inline; filename="WhatisMilvus.pdf"` 和 57,338 字节。
Playwright 验证引用抽屉内容正确、页面无 console error 或错误遮罩；点击原文链接后新标签
URL 以 `#page=1` 结尾，`document.contentType` 为 `application/pdf`。

界面截图：

- [真实引用抽屉与原文入口](../../../output/playwright/p01-citation-drawer.png)

## 附加基础设施观察

附加的临时 Collection 压力式集成测试在连续创建、flush、load 和删除集合时遇到本地
etcd `request timed out`，因此未把该次测试计入通过结果。容器重启后，实际产品知识库的
候选构建、Manifest 写入、别名切换、真实检索、引用持久化和原文打开均已成功，最终四个
服务健康。该现象属于 Milvus 本地基础设施稳定性观察，不改变本次 PDF 与引用闭环的功能
验收结论。

## 结论

P-01 验收标准已满足：四类 PDF 均有真实解析回归；引用不再停留在文件级，而是可追溯到
页码、章节、字符区间和版面坐标，并能从用户界面直接打开对应原文页。旧 v1 索引不会因
算法升级被误判为损坏，新旧分块契约仍保持明确隔离。
