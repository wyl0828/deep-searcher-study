# DeepSearcher 整体优化阶段收口

日期：2026-08-02

状态：本次整体优化阶段已完成并暂时封版。

## 1. 本阶段完成范围

- **核心 RAG 可靠性**：补齐路由输出校验、Collection 范围、向量库异常、Embedding 版本治理、
  PDF 页级定位、Trace 脱敏和评测闭环，避免把失败伪装成空结果或把距离误读成相似度。
- **运行与并发**：引入请求级 runtime/配置快照、租户和 Collection 边界、SSE 增量事件、协作取消、
  查询准入、稳定错误协议、健康检查和本地运行脚本。
- **产品工作台**：默认入口从学习控制台升级为知识库、文档生命周期、持久化对话、引用、联网检索
  和删除闭环；旧控制台保留在 `/console` 供学习 Trace。
- **浏览器安全与边界**：本地默认拒绝非 loopback Host 和跨站写请求，补响应头、可信 CORS、服务
  令牌和安全日志；明确当前仍是单用户本地应用，未用硬编码令牌冒充最终用户 RBAC。
- **用户体验与可访问性**：修复历史对话知识库上下文、键盘跳过与模态焦点、移动端回流和引用抽屉，
  补齐引用状态、文档表格语义、动态状态、小字号对比度和紧凑操作目标。

## 2. 最终验证基线

```text
uv run pytest -q
732 passed, 10 skipped

uv run ruff check .
All checks passed

uv lock --check
passed

frontend: npm test -- --run
4 test files passed, 30 tests passed

frontend: npm run typecheck
passed

frontend: npm run build
587 modules transformed; production build passed

git diff --check
passed（仅现有 LF/CRLF 提示）

uv run --frozen mkdocs build
passed（仅现有导航与历史截图链接提示）
```

真实链路还覆盖：Milvus/模型产品 SSE 查询与停止、文档上传/重试/删除、知识库删除、对话删除、
网页检索降级、核心/Worker 停止与恢复、错误码与泄漏扫描、桌面/移动浏览器布局、键盘焦点和本轮
可访问性抽样。各项细节位于同目录的专项验证记录。

## 3. 封版边界

- `S-04` 最终用户认证、Workspace 归属和 RBAC 仍等待“本地密码、OIDC、可信反向代理”身份源决策；
  当前只能绑定 `127.0.0.1` 作为单用户本地应用使用。
- `O-05` 已完成本地小文件生命周期；浏览器到对象存储的大文件直传、多段上传和 CDN 不在本阶段。
- 当前自动化和真实运行证明的是这份工作区在已覆盖场景下通过，不是生产容量、正式 SLO、完整业务
  黄金集质量基线或 WCAG 合规认证。
- 工作区包含本阶段累计的未提交改动；收口不代表已经提交、推送或发布。

## 4. 后续默认动作

除非出现真实缺陷或新的产品目标，不再继续泛化优化。后续工作转为第二遍模拟面试、项目演示和对
现有证据的复习；若重启产品建设，先从上述保留边界中选择一个明确目标并重新定义验收口径。
