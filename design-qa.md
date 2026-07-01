# DeepSearcher 学习控制台 Design QA

- Source visual truth: `docs/design-references/2026-07-01-deepsearcher-learning-console.png`
- Final implementation screenshot: `frontend/screenshots/learning-console-final-1440x1024.png`
- Verified success-state screenshot: `frontend/screenshots/learning-console-1440x1024.png`
- Full-view comparison evidence: `frontend/screenshots/design-comparison-final.png`
- Viewport: 1440 × 1024
- State: 最终截图为真实后端查询失败态；补充成功态截图记录了真实返回 `Lin Qiao`、14.02 秒和总 Token 1928。源视觉稿为演示成功态。

## Findings

没有未解决的 P0、P1 或 P2 问题。

- [Accepted difference] 数据状态与演示稿不同
  - Location: 离线入库、最终回答、事件日志与查询统计。
  - Evidence: 演示稿包含虚构的页数、切片数、命中片段数和 Token 分类；实现只显示 DeepSearcher API 实际返回的最终答案与总 Token，并在未返回的数据处标记“API 未提供”。
  - Reason: 这是产品真实性约束，不是设计漂移。真实成功态已经通过浏览器验证并保存在补充截图中。

- [Accepted difference] 使用仓库原始 Logo
  - Location: 顶部品牌区。
  - Evidence: 视觉稿中的圆点图形来自生成稿；实现使用 `assets/pic/logo.png` 的真实 DeepSearcher 品牌资产。
  - Reason: 真实资产优先于生成稿中的近似品牌图形。

- [P3] 图标字形存在轻微差异
  - Location: 两条流程和服务状态栏。
  - Evidence: 视觉稿使用生成式线性图标；实现使用 Heroicons 的同语义线性图标。
  - Impact: 不影响层级、含义或操作识别。
  - Follow-up: 如后续获得官方完整图标集，可做一次纯视觉替换。

## Required Fidelity Surfaces

- Fonts and typography: 中文使用苹方/微软雅黑系统栈，英文与数字使用本地打包的 Inter；字号、粗细和层级与源稿一致。右侧小字保持 10–12px 的高密度控制台风格。
- Spacing and layout rhythm: 顶部 72px，主体左/右栏约 77%/23%；两条泳道、控制区、结果区和状态栏与源稿同序。最终测量为 `clientHeight=1024`、`scrollHeight=1024`，没有多余整页滚动条。
- Colors and visual tokens: 白色底、深灰文字、青色/蓝色流程强调、绿色成功和红色失败均与源稿语义一致；边框和阴影保持克制。
- Image quality and asset fidelity: Logo 使用仓库原始 PNG，没有 CSS 绘图、占位资产或手写 SVG；界面图标来自 Heroicons。
- Copy and content: 全部用户界面文案为中文，必要技术名词保留英文；不可观测数据有明确说明，不包含伪造 Trace。

## Focused Region Comparison

未额外创建裁切图。`design-comparison-final.png` 以原始 1487 × 1058 分辨率并排保留两侧完整页面，流程节点、表单、右侧指标和底部日志均可直接辨认，因此全视图比较已覆盖关键高密度区域。

## Patches Made During QA

- 将结果区底部间距从 22px 收紧到 16px，消除 1440 × 1024 下的 5px 页面溢出。
- 将浏览器标题从模板名 `Prototype` 改为 `DeepSearcher 学习控制台`。
- 本地 httpx 调用增加 `trust_env=False`，避免 Windows 系统代理把 `127.0.0.1` 请求错误路由为 502。

## Implementation Checklist

- [x] 中文双泳道结构与右侧状态栏
- [x] PDF 文件校验、临时落盘与 `/load-files/` 代理
- [x] `/query/` 真实问答、总 Token 与耗时显示
- [x] 在线、离线、加载、成功和失败状态
- [x] 复制答案、刷新状态和清空日志交互
- [x] 1440 × 1024 无整页溢出
- [x] 浏览器成功态与失败态验证

## Follow-up Polish

- P3：获得官方图标资产后，可进一步收敛各节点图标的笔画与源稿差异。

final result: passed
