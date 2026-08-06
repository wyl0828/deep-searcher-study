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
      web_search: {
        status: "completed",
        provider: "tavily",
        query_count: 1,
        result_count: 2,
        error_code: null,
      },
      retrieved_count: 8,
      retrieved_documents: [
        {
          text: "DeepSearcher 会先生成子查询，再从向量库检索相关文档。",
          reference: "guide.pdf#page=2",
          metric_type: "L2",
          score_kind: "distance",
          distance: 0.0877,
          similarity: null,
          rank_score: null,
          higher_is_better: false,
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
  expect(screen.getByText("距离 L2 · 越小越近 0.0877")).toBeInTheDocument();
  expect(screen.getByText("继续检索")).toBeInTheDocument();
  expect(screen.getByText("已获得 2 个网页片段")).toBeInTheDocument();

  const secondRound = screen.getByRole("button", { name: /第 2 轮/ });
  expect(secondRound).toHaveAttribute("aria-expanded", "false");
  await user.click(secondRound);
  expect(screen.getAllByText("第二轮子查询")).toHaveLength(2);
  expect(screen.getByText("信息已足够")).toBeInTheDocument();
});

it("根据检索值语义展示相似度和排序分", () => {
  const semanticTrace = {
    ...trace,
    iterations: [
      {
        ...trace.iterations[0],
        retrieved_documents: [
          {
            text: "相似度结果",
            reference: "cosine.pdf",
            metric_type: "COSINE",
            similarity: 0.9123,
            supported: false,
          },
          {
            text: "混合排序结果",
            reference: "hybrid.pdf",
            metric_type: "RRF",
            rank_score: 0.0328,
            supported: true,
          },
        ],
      },
    ],
  };

  render(<TracePanel trace={semanticTrace} logs={[]} />);

  expect(screen.getByText("相似度 COSINE · 越大越近 0.9123")).toBeInTheDocument();
  expect(screen.getByText("排序分 RRF · 越大越近 0.0328")).toBeInTheDocument();
});

it("展示 Web 来源及 Provider 降级状态", () => {
  const webTrace = {
    ...trace,
    iterations: [
      {
        ...trace.iterations[0],
        web_search: {
          status: "degraded",
          provider: "tavily",
          query_count: 1,
          result_count: 0,
          error_code: "WEB_SEARCH_TIMEOUT",
        },
        retrieved_documents: [
          {
            text: "网页证据",
            reference: "https://docs.example.com/guide",
            source_type: "web",
            metric_type: "TAVILY_RELEVANCE",
            rank_score: 0.88,
            supported: true,
          },
        ],
      },
    ],
  };

  render(<TracePanel trace={webTrace} logs={[]} />);

  expect(screen.getByText("Provider 暂时不可用，已继续使用知识库")).toBeInTheDocument();
  expect(screen.getByText(/https:\/\/docs\.example\.com\/guide/)).toBeInTheDocument();
  expect(screen.getByText("排序分 TAVILY_RELEVANCE · 越大越近 0.8800")).toBeInTheDocument();
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
