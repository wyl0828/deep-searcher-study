# DeepSearcher 中文学习控制台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按选定的中文“双泳道画布”视觉稿构建一个可在本机浏览、并能调用真实 DeepSearcher FastAPI 的学习控制台。

**Architecture:** `frontend/` 是自包含的 React + Vite 应用。`frontend/server.py` 以同一端口托管构建产物并代理 DeepSearcher API：浏览器只访问 `/api/*`，PDF 在代理的临时目录中短暂落盘，密钥和本机路径不进入浏览器。

**Tech Stack:** React 19、Vite 6、Vitest、Testing Library、Heroicons、FastAPI、httpx、pytest。

---

### Task 1: 建立自包含前端工程

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/src/App.jsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/public/deepsearcher-logo.png`

- [ ] **Step 1: 使用 Product Design bootstrap 创建工程**

Run:

```powershell
node C:\Users\32957\.codex\plugins\cache\openai-curated-remote\product-design\0.1.47\scripts\bootstrap-prototype.mjs --dest D:\code\deep-searcher-study\frontend
```

Expected: 输出 `"status": "created"`。

- [ ] **Step 2: 安装运行与测试依赖**

Run:

```powershell
npm install @heroicons/react @fontsource/inter
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

Expected: `package-lock.json` 创建，安装命令退出码为 0。

- [ ] **Step 3: 配置测试脚本并复制真实 Logo**

`package.json` 增加：

```json
"test": "vitest run",
"test:watch": "vitest"
```

复制 `assets/pic/logo.png` 到 `frontend/public/deepsearcher-logo.png`。不得用 CSS 或自绘 SVG 替代 Logo。

- [ ] **Step 4: 验证初始工程可构建**

Run: `npm run build`

Expected: `dist/index.html` 创建且退出码为 0。

### Task 2: 以测试定义浏览器 API 契约

**Files:**
- Create: `frontend/src/api.test.js`
- Create: `frontend/src/api.js`

- [ ] **Step 1: 写入失败测试**

```javascript
import { describe, expect, it, vi } from "vitest";
import { ingestPdf, queryDeepSearcher } from "./api";

describe("浏览器 API", () => {
  it("提交问题并规范化查询结果", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ result: "答案", consume_token: 42, latency_ms: 120 }),
    });
    await expect(queryDeepSearcher("问题", 3)).resolves.toEqual({
      answer: "答案",
      totalTokens: 42,
      latencyMs: 120,
    });
  });

  it("拒绝非 PDF 文件", async () => {
    const file = new File(["text"], "note.txt", { type: "text/plain" });
    await expect(ingestPdf(file, "deepsearcher")).rejects.toThrow("仅支持 PDF 文件");
  });
});
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `npm test -- src/api.test.js`

Expected: FAIL，原因是 `./api` 不存在。

- [ ] **Step 3: 实现最小 API 客户端**

`api.js` 提供 `getHealth()`、`ingestPdf(file, collectionName)`、`queryDeepSearcher(question, maxIter)`；统一解析 `{detail}` 错误，PDF 转 Base64 后提交 `/api/ingest`，查询提交 `/api/query`。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `npm test -- src/api.test.js`

Expected: 2 tests passed。

### Task 3: 以测试定义本地代理行为

**Files:**
- Create: `frontend/tests/test_server.py`
- Create: `frontend/server.py`
- Create: `frontend/__init__.py`

- [ ] **Step 1: 写入失败测试**

```python
import base64

import pytest

from frontend.server import decode_pdf, validate_collection_name


def test_decode_pdf_rejects_non_pdf():
    with pytest.raises(ValueError, match="仅支持 PDF 文件"):
        decode_pdf("note.txt", base64.b64encode(b"text").decode())


def test_decode_pdf_accepts_pdf_signature():
    assert decode_pdf("paper.pdf", base64.b64encode(b"%PDF-1.7\n").decode()).startswith(b"%PDF")


def test_collection_name_rejects_unsafe_characters():
    with pytest.raises(ValueError, match="Collection"):
        validate_collection_name("bad/name")
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `uv run --frozen pytest frontend/tests/test_server.py -q`

Expected: FAIL，原因是 `frontend.server` 不存在。

- [ ] **Step 3: 实现代理与校验**

`server.py` 实现：

```python
MAX_PDF_BYTES = 20 * 1024 * 1024

def validate_collection_name(value: str) -> str: ...
def decode_pdf(filename: str, content_base64: str) -> bytes: ...
async def health() -> dict: ...
async def ingest(request: IngestRequest) -> dict: ...
async def query(request: QueryRequest) -> dict: ...
```

`/api/health` 探测 FastAPI 与 Milvus 并读取模型名；`/api/ingest` 在临时目录写入后调用 `/load-files/`；`/api/query` 调用 `/query/` 并添加代理测得的 `latency_ms`。所有后端错误映射为安全中文摘要。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `uv run --frozen pytest frontend/tests/test_server.py -q`

Expected: 3 tests passed。

### Task 4: 实现中文双泳道界面

**Files:**
- Create: `frontend/src/App.test.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/main.jsx`

- [ ] **Step 1: 写入失败的界面测试**

```javascript
import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("./api", () => ({
  getHealth: vi.fn().mockResolvedValue({ services: {}, config: {} }),
  ingestPdf: vi.fn(),
  queryDeepSearcher: vi.fn(),
}));

it("展示两条中文流程和不可观测说明", async () => {
  render(<App />);
  expect(screen.getByText("离线入库")).toBeInTheDocument();
  expect(screen.getByText("在线问答")).toBeInTheDocument();
  expect(screen.getByText(/不包含内部推理细节/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `npm test -- src/App.test.jsx`

Expected: FAIL，因为界面尚未包含目标文案与结构。

- [ ] **Step 3: 实现截图中的完整界面**

构建 Header、FlowLane、FlowNode、IngestionForm、QueryForm、AnswerPanel、EventLog、StatusRail。使用 Heroicons，严格复现截图的两栏比例、节点连线、细分隔线、青蓝色令牌和中文层级；所有按钮、文件选择、数字输入、复制和清空日志均可操作。

状态遵守真实 API 边界：入库和查询只显示整体“处理中/成功/失败”，页数、切片数、向量数、子查询数、命中片段和 Token 分类均显示“API 未提供”。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `npm test`

Expected: 全部前端测试通过。

- [ ] **Step 5: 构建生产资源**

Run: `npm run build`

Expected: Vite 构建退出码为 0。

### Task 5: 运行真实服务与视觉 QA

**Files:**
- Create: `frontend/screenshots/learning-console-1440x1024.png`
- Create: `design-qa.md`
- Modify: `README.md`

- [ ] **Step 1: 启动依赖服务**

启动 `infra/milvus/docker-compose.yml`，再用现有 `.env` 启动 DeepSearcher FastAPI 8500；不得改写用户当前的 `deepsearcher/config.yaml`。

- [ ] **Step 2: 启动学习控制台**

Run:

```powershell
uv run --frozen uvicorn frontend.server:app --host 127.0.0.1 --port 8600
```

Expected: `http://127.0.0.1:8600` 返回 200。

- [ ] **Step 3: 使用应用内浏览器验证**

在 1440 × 1024 打开页面，验证服务状态、PDF 选择、Collection、问题、`max_iter`、复制答案和清空日志；保存同视口截图。

- [ ] **Step 4: 完成设计 QA**

把源视觉稿与实现截图放在同一比较输入中，检查排版、间距、颜色、资产、图标、中文文案和交互状态。修复全部 P0/P1/P2 后在根目录写入 `design-qa.md`，最后一行必须是：

```text
final result: passed
```

- [ ] **Step 5: 完整验证**

Run:

```powershell
uv run --frozen pytest frontend/tests -q
npm test
npm run build
git diff --check
```

Expected: 所有命令退出码为 0，且用户的 `deepsearcher/config.yaml` 改动保持未被覆盖。
