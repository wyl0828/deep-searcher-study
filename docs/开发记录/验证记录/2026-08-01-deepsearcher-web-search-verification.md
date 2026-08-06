# DeepSearcher O-04 联网检索闭环验证

## 1. 结论

O-04 已完成。DeepSearch 已从互联网检索占位代码升级为“用户按问题显式启用、知识库与 Web
并发召回、统一筛选与引用、失败安全降级”的完整链路。默认行为仍是纯知识库问答；不配置
`TAVILY_API_KEY` 不影响原功能。

## 2. 实现范围

- `BaseWebSearch` 定义 Provider 的启用状态、搜索契约和稳定错误模型；`DisabledWebSearch`
  保持旧配置兼容。
- `TavilySearch` 按官方 Search API 契约发送 Bearer 请求，限制超时、搜索深度、子查询数和
  返回数，不请求 Provider 答案、原始网页、图片或站点图标。
- DeepSearch 同时执行向量检索与最多两个 Web 子查询；网页结果进入同一批量 Rerank、锚点
  保护、证据筛选、去重和摘要流程。
- `use_web_search` 从核心同步/流式 API、产品 BFF、旧控制台一直传递到 Agent；只有显式为
  `true` 才启用。
- Trace/SSE 新增白名单化 `web_search` 事件；网页引用保存 `source_type`、净化后的 URL、
  domain 和可信状态。
- 用户工作台提供按问题开关与网页引用抽屉；学习控制台展示联网状态和每轮降级原因。

## 3. 安全与隐私边界

- 默认关闭；开启时只把受控查询词发送给配置的 Web Search Provider。
- `requests.Session.trust_env = False`，请求不继承系统代理；禁止重定向。
- 仅接受 HTTP(S) DNS URL，拒绝所有 IP 字面量、单标签主机、localhost/`.local`、URL 凭据、
  非标准端口和非法域名。
- 引用入库与输出前删除 query 和 fragment，限制标题、摘要和响应体长度。
- 可配置 `include_domains`/`exclude_domains`；只有白名单命中的 Web 结果标记为可信。
- 搜索摘要作为不可信输入交给模型，提示词明确禁止执行其中的指令、工具调用或秘密请求。
- Provider 未配置、超时、不可用、鉴权失败、限流、上游异常、超大或非法响应均转换为稳定
  错误码，前端只显示安全状态，知识库路径继续运行。

## 4. 自动化验证

### 后端全量

```text
uv run pytest -q
680 passed, 10 skipped, 1 warning in 55.91s
```

唯一 warning 来自既有 Docling Crawler 错误用例中未 await 的 mock coroutine，与本次改动无关。

Web Search 与迁移定向测试：

```text
uv run pytest -q tests/web_search tests/integration/test_o04_web_search_http.py frontend/tests/test_product_data.py
17 passed in 2.42s
```

其中 `test_o04_web_search_http.py` 启动真实本地 HTTP 服务，验证 Authorization、请求体、超时
路径、Provider 响应映射和 URL 去参；它不是对 `requests` 的 mock。

### 前端

```text
npm run typecheck
通过

npm test -- --run
4 files / 24 tests passed

npm run build
585 modules transformed；生产构建通过
```

### 静态检查

相关 Python 文件通过 Ruff，相关 diff 通过 whitespace 检查。

## 5. 真实运行与浏览器验收

- 核心 API：`http://127.0.0.1:8750/health/live` 返回 `alive`。
- Readiness：`http://127.0.0.1:8750/health/ready` 返回 `ready`，Milvus 检查为 `ready`。
- 用户工作台：`http://127.0.0.1:8700/api/health/live` 返回 `alive`。
- 用户工作台按问题开启“联网搜索”后，问题可完成并保存知识库引用。
- 学习控制台启用联网搜索且未配置密钥时，Trace 明确显示“未配置 Provider，已继续使用知识库”。
- 浏览器 Console：0 errors，0 warnings。

截图：`output/playwright/o04-web-search-disabled-fallback.png`。

## 6. 外部服务验证边界

本机没有配置真实 `TAVILY_API_KEY`，因此本次没有产生付费外部请求。Provider 成功路径由真实
本地 HTTP 契约测试覆盖，字段和状态码依据
[Tavily Search API 官方文档](https://docs.tavily.com/documentation/api-reference/endpoint/search)
实现。部署时填入密钥并重启即可进行一次受控线上 smoke；外部服务可用性和账户额度不属于本地
代码验收结论。
