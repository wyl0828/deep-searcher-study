import { afterEach, describe, expect, it, vi } from "vitest";

import { ingestPdf, queryDeepSearcher } from "./api";

describe("浏览器 API", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("提交问题并规范化查询结果", async () => {
    const trace = { version: 1, agent: "ChainOfRAG", iterations: [] };
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ result: "答案", consume_token: 42, latency_ms: 120, trace }),
    });

    await expect(queryDeepSearcher("问题", 3)).resolves.toEqual({
      answer: "答案",
      totalTokens: 42,
      latencyMs: 120,
      trace,
    });
  });

  it("拒绝非 PDF 文件", async () => {
    const file = new File(["text"], "note.txt", { type: "text/plain" });

    await expect(ingestPdf(file, "deepsearcher")).rejects.toThrow("仅支持 PDF 文件");
  });

  it("将后端错误转换为中文消息", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "FastAPI 当前不可用" }),
    });

    await expect(queryDeepSearcher("问题", 3)).rejects.toThrow("FastAPI 当前不可用");
  });
});
