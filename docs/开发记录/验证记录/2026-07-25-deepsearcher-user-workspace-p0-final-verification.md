# DeepSearcher 用户工作台 P0 最终验证

验证日期：2026-07-25  
验证环境：Windows 11、Python 3.10、Node/Vite、Docker Desktop、Milvus standalone、SQLite  
结论：P0 完成，主流程和设计 QA 均通过。

## 1. 完成定义核对

| 完成条件 | 证据 | 结果 |
| --- | --- | --- |
| 从空数据创建知识库并上传真实 PDF | 浏览器创建弹窗、上传入口；产品 API 与持久化测试；真实 `DeepSearcher第一阶段学习讲义-手机阅读版.pdf` 已处理完成 | 通过 |
| 文档状态来自服务端持久化记录 | `documents / ingest_jobs` 数据模型、重启恢复、轮询与重试测试；浏览器显示“可用于问答” | 通过 |
| 查询受当前知识库范围限制 | `collection_names` 贯穿查询入口和三类 Agent；本地 Milvus 双集合隔离集成测试 | 通过 |
| 引用来自真实检索结果 | 最终会话返回原文件名、真实页码与片段；旧引用名称在启动时幂等修复 | 通过 |
| 页码只来自 PDF 页级元数据 | PDFLoader、Chunk、Trace、Citation 全链路测试 | 通过 |
| 最近对话刷新后仍存在 | SQLite 会话/消息持久化；`/chat/:id` 深链接刷新 200 | 通过 |
| 连续追问使用真实历史上下文 | 实测同一会话从“Milvus 是什么”追问“它支持混合搜索吗”，得到上下文相关答案和 `WhatisMilvus.pdf` 引用 | 通过 |
| 无证据、服务离线和处理失败可恢复 | 安全错误模型、输入保留、失败文档重试、服务重启恢复测试 | 通过 |
| 学习控制台继续可用 | `/console` 浏览器实测，离线入库、在线问答和服务状态界面正常 | 通过 |
| 自动测试和生产构建通过 | 见第 2 节 | 通过 |
| 浏览器主流程通过 | 见第 3 节 | 通过 |
| 设计 QA 通过 | 根目录 `design-qa.md` 为 `final result: passed` | 通过 |

## 2. 自动化验证

```text
uv run --frozen pytest -q
458 passed, 7 skipped

DEEPSEARCHER_RUN_LIVE_MILVUS=1 pytest tests/integration/test_p0_milvus_live.py -q
1 passed

npm test -- --run
3 test files, 10 tests passed

npx tsc --noEmit
passed

npm run build
583 modules transformed, production build passed

ruff check（本次产品层、服务层和相关测试）
passed
```

7 个跳过项均有明确原因：1 个本地 Milvus 实测默认需要显式环境变量启用，已另行启用并通过；6 个 Milvus Lite 测试因该可选依赖在当前 Windows 环境不可用而跳过，Docker Milvus 实测已覆盖当前产品运行路径。

## 3. 浏览器验证

### 桌面主流程

- `/chat/:conversationId` 直接打开和刷新均成功。
- 真实问题、真实 Markdown 回答、3 条真实引用和页码正常。
- 点击 `[1] / [2] / [3]` 能选中对应来源。
- 顶部引用按钮可关闭并重新打开抽屉。
- 引用抽屉关闭后焦点返回触发编号。
- “有帮助”可切换 `aria-pressed` 状态。
- “重新生成”具备真实提交行为，并有前端回归测试。
- 输入非空时发送按钮启用，清空后禁用。
- 知识库列表、详情、创建弹窗、上传入口和文档就绪状态可用。
- `/console` 保留原学习与调试能力。
- 浏览器 `error` / `warn` 日志为空。

### 响应式

| CSS 视口 | 结果 |
| --- | --- |
| 1487 × 1058 | 三栏布局与设计基准对齐 |
| 1024 × 768 | 三栏保持可用，无水平溢出 |
| 768 × 900 | 左侧栏收起，引用抽屉覆盖显示，无水平溢出 |
| 390 × 844 | 基础问答、阅读、输入和发送状态可用，无水平溢出 |

## 4. 设计证据

- 视觉基准：`docs/设计参考/2026-07-23-deepsearcher-user-workspace.png`
- 最终实现：`docs/设计参考/2026-07-25-deepsearcher-workspace-final-3.jpg`
- 全景对照：`docs/设计参考/2026-07-25-deepsearcher-workspace-final-3-comparison.jpg`
- 正文聚焦对照：`docs/设计参考/2026-07-25-deepsearcher-workspace-final-3-focused-comparison.jpg`
- 审计报告：`design-qa.md`

## 5. 当前运行状态

- 用户工作台：`http://127.0.0.1:8600`
- DeepSearcher API：`http://127.0.0.1:8500`
- Milvus：`127.0.0.1:19530`
- `/api/health`：FastAPI online、Milvus online、LLM configured、Embedding configured
