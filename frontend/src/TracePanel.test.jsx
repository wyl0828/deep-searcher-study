import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { TracePanel } from "./TracePanel";

const trace = {
  version: 1,
  agent: "ChainOfRAG",
  original_query: "DeepSearcher 如何工作？",
  iterations: [
    {
      index: 1,
      subquery: "DeepSearcher 的核心流程",
      collections: ["deepsearcher"],
      retrieved_count: 8,
      retrieved_documents: [
        {
          text: "DeepSearcher 会先生成子查询，再从向量库检索相关文档。",
          reference: "guide.pdf#page=2",
          score: 0.9123,
          supported: true,
        },
      ],
      intermediate_answer: "已找到核心流程说明。",
      has_enough_information: false,
      token_usage: { total: 120 },
    },
    {
      index: 2,
      subquery: "第二轮子查询",
      collections: ["deepsearcher"],
      retrieved_count: 2,
      retrieved_documents: [],
      intermediate_answer: "信息已经足够。",
      has_enough_information: true,
      token_usage: { total: 64 },
    },
  ],
  summary: {
    iteration_count: 2,
    supported_document_count: 1,
    routing_tokens: 12,
    final_answer_tokens: 32,
    total_tokens: 228,
  },
};

it("用中文分轮展示 Agent、检索文档与反思结果", async () => {
  const user = userEvent.setup();
  render(<TracePanel trace={trace} logs={[]} />);

  expect(screen.getByText("ChainOfRAG")).toBeInTheDocument();
  expect(screen.getAllByText("DeepSearcher 的核心流程")).toHaveLength(2);
  expect(screen.getByText("命中 8 条，仅展示前 1 条")).toBeInTheDocument();
  expect(screen.getByText(/guide\.pdf#page=2/)).toBeInTheDocument();
  expect(screen.getByText("继续检索")).toBeInTheDocument();

  const secondRound = screen.getByRole("button", { name: /第 2 轮/ });
  expect(secondRound).toHaveAttribute("aria-expanded", "false");
  await user.click(secondRound);
  expect(screen.getAllByText("第二轮子查询")).toHaveLength(2);
  expect(screen.getByText("信息已足够")).toBeInTheDocument();
});

it("可切换到系统日志并清空", async () => {
  const user = userEvent.setup();
  let cleared = false;
  render(
    <TracePanel
      trace={trace}
      logs={[{ id: "1", time: "10:21:15", message: "开始查询", tone: "info" }]}
      onClearLogs={() => {
        cleared = true;
      }}
    />,
  );

  await user.click(screen.getByRole("tab", { name: "系统日志" }));
  expect(screen.getByText("开始查询")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "清空" }));
  expect(cleared).toBe(true);
});
