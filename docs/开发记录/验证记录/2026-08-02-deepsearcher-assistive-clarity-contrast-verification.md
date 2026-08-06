# DeepSearcher 工作台辅助技术语义与视觉清晰度验证

日期：2026-08-02

范围：引用来源开关与引用按钮、知识库文档列表和异步状态、小字号文本对比度、移动端紧凑操作目标。

## 1. 审计发现

- 引用开关原先没有 `aria-expanded` 和 `aria-controls`；当前对话没有引用时仍可操作，辅助技术无法
  判断“已关闭”还是“没有来源”。
- 行内引用仅提供“引用 N”，多个来源不能凭名称区分；文档列表虽然视觉上是表格，但没有表格、
  列标题和单元格语义，状态变化也没有 live region。
- 真实浏览器计算样式抽样中，时间、历史标签、操作文字、引用说明、状态标签等关键小字号文本约为
  `2.58:1`～`3.99:1`；行内引用、回答操作和来源链接的部分高度约为 `17px`～`19px`。

## 2. 实现契约

- 顶部来源按钮暴露可用性、`aria-expanded` 和 `aria-controls="citation-drawer"`；无引用时禁用并通过
  `title` 解释原因。ChatPage 在加载、切换对话、开关抽屉和卸载时同步来源状态。
- 行内引用和抽屉来源按钮均使用“引用编号 + 文件名 + 页码”的可区分名称；来源计数提供中文名称。
- 文档列表增加 table/rowgroup/row/columnheader/cell 语义；文档状态与索引状态通过 `role=status`、
  `aria-live` 和 `aria-atomic` 表达，异步区域增加 `aria-busy`，切换期间禁用重复操作。
- 辅助文字、成功、警告和危险色加深；紧凑操作目标提高到至少 `28px` 高，行内引用同时保证
  `28px × 28px`。

## 3. 自动化验证

```text
npm test -- --run
4 test files passed, 30 tests passed

npm run typecheck
passed

npm run build
587 modules transformed; production build passed

uv run pytest -q
732 passed, 10 skipped

uv run ruff check .
All checks passed

uv lock --check
passed

git diff --check
passed（仅现有 LF/CRLF 提示）

uv run --frozen mkdocs build
passed（仅现有导航与历史截图链接提示）
```

新增组件测试覆盖：有引用时开关的展开/受控区域状态；无引用时按钮禁用和原因；移动端打开、Esc
关闭后的状态；行内引用的文件名/页码名称；文档列表列标题、行/单元格与 live 状态语义。

## 4. 真实浏览器验证

- 知识库页可访问树包含名为“知识库文档”的 table、5 个 columnheader，以及带“文档状态：可用于
  问答”名称的状态 cell。
- 对话页顶部开关能在 `aria-expanded=true/false` 间同步；行内引用可打开抽屉并把焦点移到对应来源，
  关闭后焦点回到开关。真实无引用对话中按钮禁用、不再声明受控抽屉，并解释“当前对话没有引用来源”。
- `390 × 844` 下抽屉默认关闭；打开后具有 dialog/aria-modal、初始关闭焦点和背景滚动锁；Esc 后
  恢复按钮焦点和页面滚动。最终可见交互目标扫描没有低于 `24px` 的项目。
- 在首页、知识库、对话和移动抽屉审计状态中，对可见、非禁用、直接文本节点的前景/不透明背景
  计算样式抽样未发现低于 `4.5:1` 的项目。

审计截图和逐步笔记保存在 `output/product-audit-2026-08-02-a11y/`，其中最终证据为
`05-knowledge-improved.png`、`06-chat-improved.png` 和 `08-mobile-citations-final.png`。

## 5. 证据边界

本记录证明所列状态的组件契约、浏览器计算样式抽样、键盘基本行为和目标尺寸，不等同于完整 WCAG
合规。尚未覆盖 NVDA、JAWS、VoiceOver 实机播报时序，Windows 强制颜色/高对比模式，200%/400%
缩放的全部业务流程，触控辅助功能，以及所有动态数据组合。
