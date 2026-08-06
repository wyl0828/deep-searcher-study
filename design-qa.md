# DeepSearcher 用户工作台 · Design QA

- source visual truth path: `D:\code\deep-searcher-study\docs\设计参考\2026-07-23-deepsearcher-user-workspace.png`
- implementation screenshot path: `D:\code\deep-searcher-study\docs\设计参考\2026-07-25-deepsearcher-workspace-final-3.jpg`
- viewport: 1487 × 1058 CSS px
- pixel output: 1487 × 1058 px JPEG
- device pixel ratio: 1.25
- density: desktop three-column workspace with 300px navigation, 809px conversation area, and 378px citation drawer
- state: 本地真实知识库“AI 全栈学习资料”，已完成 PDF 处理；真实问答完成态，包含 3 条可核对的原文件引用（第 33、34、36 页）
- full-view comparison evidence: `D:\code\deep-searcher-study\docs\设计参考\2026-07-25-deepsearcher-workspace-final-3-comparison.jpg`
- focused region comparison evidence: `D:\code\deep-searcher-study\docs\设计参考\2026-07-25-deepsearcher-workspace-final-3-focused-comparison.jpg`
- responsive evidence:
  - `D:\code\deep-searcher-study\docs\设计参考\2026-07-25-deepsearcher-workspace-tablet.jpg`（1024 × 768）
  - `D:\code\deep-searcher-study\docs\设计参考\2026-07-25-deepsearcher-workspace-768.jpg`（768 × 900）
  - `D:\code\deep-searcher-study\docs\设计参考\2026-07-25-deepsearcher-workspace-mobile.jpg`（390 × 844）

## Findings

没有尚未解决的 P0、P1 或 P2 视觉问题。

- 布局与密度：顶部栏、左侧导航、正文列、引用抽屉和底部追问框与参考图的主要边界对齐；正文宽度控制在约 748px，引用抽屉为 378px，桌面状态没有水平溢出。
- 字体与间距：标题、正文、元信息、引用片段和操作区形成稳定层级；对话正文使用真实 Markdown 列表与行内代码，长文件名和片段安全换行。
- 色彩与视觉令牌：蓝色主操作、浅蓝选中态、浅灰分隔线和白色内容面保持参考图的低噪声知识工作台风格。
- 品牌与图标：使用项目现有 DeepSearcher Logo、真实徽标资源和 Heroicons；未使用占位图、手绘 SVG、CSS 图形或文本符号模拟可见资产。
- 真实内容：实现截图来自真实 PDF、真实 Milvus 检索和真实模型回答，因此回答措辞、文件名与页码不复制设计稿示例；这些属于产品数据差异，不是视觉偏差。
- 交互：引用编号与顶部面板按钮都能打开或关闭引用抽屉，并保持对应来源选中；反馈按钮有明确选中态；重新生成、知识库导航、创建弹窗、上传入口和追问输入均可操作。
- 响应式：1024px 保持三栏，768px 将引用抽屉改为覆盖层，390px 保留基础问答和固定输入框；三个宽度都未发现水平溢出。
- 可访问性：图标按钮都有可访问名称，禁用搜索明确使用 `disabled`，输入和发送状态可由键盘触发，文档状态同时使用文字而非只依赖颜色。
- 运行质量：会话深链接刷新返回 200；浏览器 `error`/`warn` 日志为空。

## Iteration history

### Iteration 1 — 2026-07-24

- comparison: `D:\code\deep-searcher-study\docs\设计参考\2026-07-24-deepsearcher-workspace-comparison.jpg`
- implementation: `D:\code\deep-searcher-study\docs\设计参考\2026-07-24-deepsearcher-workspace-implementation.jpg`
- findings:
  - P1：引用展示内部文档标识，不是原始文件名。
  - P2：知识库文档计数刷新滞后。
  - P2：顶部栏、引用抽屉和输入框高度与参考图不一致。
  - P2：回答操作只有静态外观，重新生成和反馈没有状态变化。

### Iteration 2 — 2026-07-25

- 修复引用到原始文件名与真实页码的映射。
- 修复文档处理完成后的知识库计数失效。
- 按参考图重新校准 100px 顶栏、300px 左栏、378px 引用抽屉和底部输入框。
- 使用真实 DeepSearcher 徽标作为助手头像。
- 实现重新生成、本地反馈、引用选中和顶部引用抽屉开关。
- 移除普通用户顶部栏中的技术控制台入口，只保留左下角开发入口。
- 修复 `/chat/:conversationId`、`/knowledge/:knowledgeBaseId` 的 SPA 深链接刷新。
- 完成桌面、平板、768px 临界宽度和移动端浏览器复核。

## Final comparison judgment

参考图与最终实现已在同一全景对照图和同一正文聚焦对照图中复核。最终实现保留了参考图的三栏信息架构、空间比例、颜色、排版层级与交互密度；可见差异仅来自真实品牌资源与真实运行数据。全部 P0、P1、P2 差异已关闭。

final result: passed
