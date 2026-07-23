# DeepSearcher 查询过程可视化 · Design QA

- source visual truth path: `D:\code\deep-searcher-study\design-qa\source-reference.png`
- implementation screenshot path: `D:\code\deep-searcher-study\design-qa\implementation-query-viewport.png`
- viewport: 1488 × 1058（实现截图含 15px 浏览器滚动条区域）
- state: 本地 Milvus `deepsearcher` 集合 + 千问 `qwen-plus` 的真实三轮查询完成态
- full-view comparison evidence: `D:\code\deep-searcher-study\design-qa\comparison.png`
- focused region comparison evidence: `D:\code\deep-searcher-study\design-qa\focused-result-comparison.png`
- responsive evidence: `D:\code\deep-searcher-study\design-qa\implementation-760.png`

## Findings

没有尚未解决的 P0、P1 或 P2 问题。

- 字体与排版：使用 Inter + 中文系统字体回退；标题、标签、流程节点和小字层级与参考设计一致，真实长查询在流程节点内截断，在详情区完整显示。
- 间距与布局：桌面端保持双泳道、右侧状态栏和底部双栏结果结构；查询过程替代原事件日志后仍遵循原面板边界与密度。760px 窄屏下控制区纵向排列，流程泳道保留局部横向滚动，未发现控件重叠。
- 颜色与视觉令牌：蓝色主操作、青色查询流程、绿色成功态和浅灰分隔线延续参考设计；查询过程的选中标签和状态徽标使用同一语义色系。
- 图片与图标：沿用项目现有 DeepSearcher 标志，功能图标统一使用 Heroicons，无占位图、手绘 SVG 或 CSS 图形替代。
- 文案与内容：页面保持中文；Agent、轮次、集合、检索数量、相似度、阶段回答、Token 和反思执行状态均来自真实结构化响应。未展示提示词、向量、原始元数据或隐藏推理。
- 交互与可访问性：轮次支持展开/收起，文档片段支持展开，查询过程/系统日志可切换；按钮、标签页、输入框和展开状态具备语义与可访问名称。

## Patches made since the previous QA pass

- 将底部事件区升级为“查询过程 / 系统日志”双标签。
- 增加分轮查询、检索集合、文档证据、阶段回答和 Token 汇总。
- 将未启用 early stopping 的空反思结果明确显示为“未执行反思”。
- 本地文件引用仅显示文件名，移除无关临时目录路径。

## Follow-up polish

- P3：后端未来若分别提供提示词与完成 Token，可替换当前“未提供”占位并补齐参考设计中的统计细度。

final result: passed
