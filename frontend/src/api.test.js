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

    await expect(queryDeepSearcher("问题", 3, "kb_verified", true)).resolves.toEqual({
      answer: "答案",
      totalTokens: 42,
      latencyMs: 120,
      trace,
    });
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
      question: "问题",
      max_iter: 3,
      collection_name: "kb_verified",
      use_web_search: true,
    });
  });

  it("拒绝非 PDF 文件", async () => {
    const file = new File(["text"], "note.txt", { type: "text/plain" });

    await expect(ingestPdf(file, "deepsearcher")).rejects.toThrow("仅支持 PDF 文件");
  });

  it("使用 multipart 上传 PDF，不再编码 Base64", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: "入库请求成功" }),
    });
    const file = new File(["%PDF-1.7"], "paper.pdf", { type: "application/pdf" });

    await ingestPdf(file, "kb_streamed");

    const [url, options] = global.fetch.mock.calls[0];
    expect(url).toBe("/api/ingest");
    expect(options.method).toBe("POST");
    expect(options.headers).toBeUndefined();
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("file")).toBe(file);
    expect(options.body.get("collection_name")).toBe("kb_streamed");
  });

  it("将后端错误转换为中文消息", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "FastAPI 当前不可用" }),
    });

    await expect(queryDeepSearcher("问题", 3)).rejects.toThrow("FastAPI 当前不可用");
  });
});
