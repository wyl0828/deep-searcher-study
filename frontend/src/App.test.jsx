import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { App } from "./App";

const apiMocks = vi.hoisted(() => ({
  getHealth: vi.fn(),
  ingestPdf: vi.fn(),
  queryDeepSearcher: vi.fn(),
}));

vi.mock("./api", () => apiMocks);

beforeEach(() => {
  apiMocks.getHealth.mockResolvedValue({
    services: {
      fastapi: { state: "online" },
      milvus: { state: "online" },
      llm: { state: "configured" },
      embedding: { state: "configured" },
    },
    config: {
      collection: "deepsearcher",
      llm_model: "qwen-plus",
      embedding_model: "text-embedding-v4",
    },
  });
  apiMocks.queryDeepSearcher.mockResolvedValue({
    answer: "这是来自测试的最终答案。",
    totalTokens: 88,
    latencyMs: 240,
  });
});

it("展示两条中文流程和不可观测说明", async () => {
  render(<App />);

  expect(screen.getByText("离线入库")).toBeInTheDocument();
  expect(screen.getByText("在线问答")).toBeInTheDocument();
  expect(screen.getByText(/不包含内部推理细节/)).toBeInTheDocument();
  expect(await screen.findByText("qwen-plus")).toBeInTheDocument();
});

it("运行查询后展示真实返回的答案与总 Token", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.type(screen.getByLabelText("问题"), "DeepSearcher 如何工作？");
  await user.click(screen.getByRole("button", { name: "运行查询" }));

  expect(await screen.findByText("这是来自测试的最终答案。")).toBeInTheDocument();
  expect(screen.getByText("88")).toBeInTheDocument();
  expect(apiMocks.queryDeepSearcher).toHaveBeenCalledWith("DeepSearcher 如何工作？", 3);
});
