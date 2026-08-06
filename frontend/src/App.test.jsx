import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { App, workspaceQueryClient } from "./App";

const productApi = vi.hoisted(() => ({
  ProductApiError: class ProductApiError extends Error {
    constructor(message, code = "UNKNOWN_ERROR", retryable = false) {
      super(message);
      this.name = "ProductApiError";
      this.code = code;
      this.retryable = retryable;
    }
  },
  listKnowledgeBases: vi.fn(),
  createKnowledgeBase: vi.fn(),
  getKnowledgeBase: vi.fn(),
  setCurrentKnowledgeBase: vi.fn(),
  reindexKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  listDocuments: vi.fn(),
  uploadDocument: vi.fn(),
  retryDocument: vi.fn(),
  deleteDocument: vi.fn(),
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  deleteConversation: vi.fn(),
  sendMessage: vi.fn(),
  streamMessage: vi.fn(),
}));

const consoleApi = vi.hoisted(() => ({
  getHealth: vi.fn(),
  ingestPdf: vi.fn(),
  queryDeepSearcher: vi.fn(),
}));

vi.mock("./product-api", () => productApi);
vi.mock("./api", () => consoleApi);

function setCompactViewport(matches) {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: query === "(max-width: 820px)" ? matches : false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

const knowledgeBase = {
  id: "kb_product",
  name: "AI 全栈学习资料",
  description: "课程和项目资料",
  document_count: 2,
  ready_document_count: 2,
  conversation_count: 1,
  is_current: true,
  index_status: "verified",
  index_manifest: {
    schema_version: 1,
    logical_collection: "kb_product",
    embedding_provider: "OpenAIEmbedding",
    embedding_model: "text-embedding-v4",
    embedding_version: "text-embedding-v4",
    embedding_fingerprint: "embedding-fingerprint",
    dimension: 1024,
    normalization: "provider_default",
    metric_type: "L2",
    chunk_size: 1500,
    chunk_overlap: 100,
    chunk_config_version: "chunk-version",
    document_version: "document-version",
    data_version: "data-version",
    created_at: "2026-07-24T09:00:00+08:00",
    manifest_fingerprint: "manifest-fingerprint",
  },
  index_previous_collection: null,
  created_at: "2026-07-24T09:00:00+08:00",
  updated_at: "2026-07-24T09:00:00+08:00",
};

beforeEach(() => {
  vi.clearAllMocks();
  setCompactViewport(false);
  Element.prototype.scrollIntoView = vi.fn();
  workspaceQueryClient.clear();
  window.history.pushState({}, "", "/");
  productApi.listKnowledgeBases.mockResolvedValue([knowledgeBase]);
  productApi.getKnowledgeBase.mockResolvedValue(knowledgeBase);
  productApi.reindexKnowledgeBase.mockResolvedValue(knowledgeBase);
  productApi.listDocuments.mockResolvedValue([]);
  productApi.deleteDocument.mockResolvedValue(undefined);
  productApi.deleteKnowledgeBase.mockResolvedValue({
    deleted_id: knowledgeBase.id,
    current_knowledge_base_id: null,
  });
  productApi.listConversations.mockResolvedValue([]);
  productApi.deleteConversation.mockResolvedValue(undefined);
  productApi.createConversation.mockResolvedValue({
    id: "conv_1",
    knowledge_base_id: knowledgeBase.id,
    knowledge_base_name: knowledgeBase.name,
    title: "新对话",
    created_at: knowledgeBase.created_at,
    updated_at: knowledgeBase.updated_at,
  });
  productApi.streamMessage.mockImplementation(async (_id, _content, onStage) => {
    onStage({
      version: 1,
      request_id: "request-test",
      sequence: 1,
      event: "started",
      data: { stage: "query_started" },
    });
    return {
    user_message: {
      id: "msg_user",
      role: "user",
      content: "DeepSearcher 如何工作？",
      status: "succeeded",
      answer_state: null,
      created_at: knowledgeBase.created_at,
      citations: [],
    },
    assistant_message: {
      id: "msg_assistant",
      role: "assistant",
      content: "DeepSearcher 会检索资料并生成答案。",
      status: "succeeded",
      answer_state: "grounded",
      created_at: knowledgeBase.created_at,
      citations: [],
    },
    };
  });
  productApi.getConversation.mockResolvedValue({
    id: "conv_1",
    title: "DeepSearcher 如何工作？",
    knowledge_base: knowledgeBase,
    messages: [
      {
        id: "msg_user",
        role: "user",
        content: "DeepSearcher 如何工作？",
        status: "succeeded",
        answer_state: null,
        created_at: knowledgeBase.created_at,
        citations: [],
      },
      {
        id: "msg_assistant",
        role: "assistant",
        content: "DeepSearcher 会检索资料并生成答案。",
        status: "succeeded",
        answer_state: "grounded",
        created_at: knowledgeBase.created_at,
        citations: [],
      },
    ],
    created_at: knowledgeBase.created_at,
    updated_at: knowledgeBase.updated_at,
  });
});

it("默认展示面向用户的新对话工作台", async () => {
  render(<App />);

  expect(await screen.findByText("向你的资料提问")).toBeInTheDocument();
  expect(screen.getAllByText("AI 全栈学习资料").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "切换引用来源" })).toBeDisabled();
  expect(screen.queryByText("学习控制台")).not.toBeInTheDocument();
  expect(screen.queryByText("Milvus 向量库")).not.toBeInTheDocument();
});

it("键盘用户可以跳过侧栏直接到主要内容", async () => {
  const user = userEvent.setup();
  render(<App />);

  const skipLink = screen.getByRole("link", { name: "跳到主要内容" });
  expect(skipLink).toHaveAttribute("href", "#workspace-main");
  expect(document.querySelector("main#workspace-main")).toHaveAttribute(
    "tabindex",
    "-1",
  );

  await user.tab();
  expect(skipLink).toHaveFocus();
});

it("创建知识库弹窗约束焦点并在 Esc 关闭后恢复触发点", async () => {
  const user = userEvent.setup();
  render(<App />);
  const trigger = await screen.findByRole("button", { name: "创建知识库" });

  await user.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "创建知识库" });
  const nameInput = within(dialog).getByLabelText("知识库名称");
  const closeButton = within(dialog).getByRole("button", { name: "关闭" });
  const cancelButton = within(dialog).getByRole("button", { name: "取消" });
  expect(nameInput).toHaveFocus();

  closeButton.focus();
  await user.tab({ shift: true });
  expect(cancelButton).toHaveFocus();
  await user.tab();
  expect(closeButton).toHaveFocus();

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "创建知识库" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it("提交问题后创建对话并恢复真实回答", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.type(
    await screen.findByLabelText("输入问题"),
    "DeepSearcher 如何工作？",
  );
  await user.click(screen.getByRole("button", { name: "联网搜索" }));
  expect(screen.getByRole("button", { name: "联网搜索" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await user.click(screen.getByRole("button", { name: "发送问题" }));

  await waitFor(() => {
    expect(productApi.createConversation).toHaveBeenCalledWith(knowledgeBase.id);
    expect(productApi.streamMessage).toHaveBeenCalledWith(
      "conv_1",
      "DeepSearcher 如何工作？",
      expect.any(Function),
      expect.anything(),
      true,
    );
  });
  expect(
    await screen.findByText("DeepSearcher 会检索资料并生成答案。"),
  ).toBeInTheDocument();
});

it("学习控制台仍可通过 console 路由访问", async () => {
  consoleApi.getHealth.mockResolvedValue({
    services: {
      fastapi: { state: "ready" },
      milvus: { state: "ready" },
      llm: { state: "unknown" },
      embedding: { state: "unknown" },
    },
    config: {
      collection: "deepsearcher",
      llm_model: "qwen-plus",
      embedding_model: "text-embedding-v4",
    },
  });
  window.history.pushState({}, "", "/console");

  render(<App />);

  expect(await screen.findByText("离线入库")).toBeInTheDocument();
  expect(screen.getByText("在线问答")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "深度检查" })).toBeInTheDocument();
});

it("回答支持重新生成和本地反馈状态", async () => {
  const user = userEvent.setup();
  window.history.pushState({}, "", "/chat/conv_1");

  render(<App />);

  expect(
    await screen.findByText("DeepSearcher 会检索资料并生成答案。"),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "重新生成" }));

  await waitFor(() => {
    expect(productApi.streamMessage).toHaveBeenCalledWith(
      "conv_1",
      "DeepSearcher 如何工作？",
      expect.any(Function),
      expect.anything(),
      false,
    );
  });

  const helpful = screen.getByRole("button", { name: "有帮助" });
  await user.click(helpful);
  expect(helpful).toHaveAttribute("aria-pressed", "true");
});

it("回答过程中逐步展示安全阶段并允许用户停止", async () => {
  const user = userEvent.setup();
  let capturedSignal;
  window.history.pushState({}, "", "/chat/conv_1");
  productApi.streamMessage.mockImplementation(
    (_conversationId, _content, onStage, signal) => {
      capturedSignal = signal;
      [
        {
          version: 1,
          request_id: "request-progress",
          sequence: 1,
          event: "started",
          data: { stage: "query_started" },
        },
        {
          version: 1,
          request_id: "request-progress",
          sequence: 2,
          event: "retrieval",
          data: { iteration: 1, retrieved_count: 7 },
        },
        {
          version: 1,
          request_id: "request-progress",
          sequence: 3,
          event: "support",
          data: { iteration: 1, supported_count: 2 },
        },
        {
          version: 1,
          request_id: "request-progress",
          sequence: 4,
          event: "reflection",
          data: { iteration: 1, has_enough_information: true },
        },
      ].forEach(onStage);
      return new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new Error("aborted")));
      });
    },
  );

  render(<App />);
  await user.click(await screen.findByRole("button", { name: "重新生成" }));

  expect(await screen.findByText("已找到 7 个候选片段")).toBeInTheDocument();
  expect(screen.getByText("其中 2 个片段通过证据核验")).toBeInTheDocument();
  expect(screen.getByText("证据检查完成，正在组织回答")).toBeInTheDocument();
  expect(
    screen.getByText("这里展示的是系统执行阶段，不是模型的思维链。"),
  ).toBeInTheDocument();
  await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: "正在生成…" })).toBeDisabled();

  await user.click(screen.getByRole("button", { name: "停止生成" }));

  expect(capturedSignal.aborted).toBe(true);
  expect(await screen.findByText("本次回答已停止")).toBeInTheDocument();
  expect(
    screen.getByText("没有保存未完成的回答，你可以重新开始。"),
  ).toBeInTheDocument();
});

it("引用抽屉打开和关闭时恢复键盘焦点", async () => {
  const user = userEvent.setup();
  window.history.pushState({}, "", "/chat/conv_1");
  productApi.getConversation.mockResolvedValue({
    id: "conv_1",
    title: "带引用的回答",
    knowledge_base: knowledgeBase,
    messages: [
      {
        id: "msg_user",
        role: "user",
        content: "依据是什么？",
        status: "succeeded",
        answer_state: null,
        created_at: knowledgeBase.created_at,
        citations: [],
      },
      {
        id: "msg_assistant",
        role: "assistant",
        content: "这是有依据的回答。",
        status: "succeeded",
        answer_state: "grounded",
        created_at: knowledgeBase.created_at,
        citations: [
          {
            id: "citation_1",
            index: 1,
            document_id: "doc_guide",
            display_name: "guide.pdf",
            page_number: 2,
            section_title: "检索流程",
            char_start: 120,
            char_end: 240,
            extraction_method: "text",
            text: "真实证据片段",
            source_type: "knowledge_base",
            source_url: null,
            source_domain: null,
            trusted: true,
          },
          {
            id: "citation_2",
            index: 2,
            document_id: null,
            display_name: "Tavily 官方文档",
            page_number: null,
            section_title: null,
            char_start: null,
            char_end: null,
            extraction_method: null,
            source_type: "web",
            source_url: "https://docs.tavily.com/search",
            source_domain: "docs.tavily.com",
            trusted: true,
            text: "网页证据片段",
          },
        ],
      },
    ],
    created_at: knowledgeBase.created_at,
    updated_at: knowledgeBase.updated_at,
  });

  render(<App />);

  const inlineCitation = await screen.findByRole("button", {
    name: "查看引用 1：guide.pdf，第 2 页",
  });
  const citationToggle = screen.getByRole("button", { name: "切换引用来源" });
  await waitFor(() => {
    expect(citationToggle).toHaveAttribute("aria-expanded", "true");
    expect(citationToggle).toHaveAttribute("aria-controls", "citation-drawer");
  });
  await user.click(inlineCitation);
  expect(
    screen.getByRole("button", { name: "选择引用 1：guide.pdf，第 2 页" }),
  ).toHaveFocus();
  expect(screen.getByText("检索流程")).toBeInTheDocument();
  expect(screen.getByText("字符 120–240")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: /打开原文第 2 页/ }),
  ).toHaveAttribute("href", "/api/documents/doc_guide/content#page=2");

  const webInlineCitation = screen.getByRole("button", {
    name: "查看引用 2：Tavily 官方文档",
  });
  await user.click(webInlineCitation);
  expect(screen.getByText("docs.tavily.com")).toBeInTheDocument();
  expect(screen.getByText("域名白名单")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "打开网页来源" })).toHaveAttribute(
    "href",
    "https://docs.tavily.com/search",
  );

  await user.click(screen.getByRole("button", { name: "关闭引用来源" }));
  await waitFor(() => {
    expect(webInlineCitation).toHaveFocus();
    expect(citationToggle).toHaveAttribute("aria-expanded", "false");
  });
});

it("没有引用的对话会禁用来源切换并说明原因", async () => {
  window.history.pushState({}, "", "/chat/conv_1");

  render(<App />);

  expect(
    await screen.findByText("DeepSearcher 会检索资料并生成答案。"),
  ).toBeInTheDocument();
  const toggle = screen.getByRole("button", { name: "切换引用来源" });
  expect(toggle).toBeDisabled();
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(toggle).not.toHaveAttribute("aria-controls");
  expect(toggle).toHaveAttribute("title", "当前对话没有引用来源");
});

it("移动端默认显示回答，并将引用来源作为模态抽屉打开", async () => {
  const user = userEvent.setup();
  setCompactViewport(true);
  window.history.pushState({}, "", "/chat/conv_1");
  productApi.getConversation.mockResolvedValue({
    id: "conv_1",
    title: "移动端引用回答",
    knowledge_base: knowledgeBase,
    messages: [
      {
        id: "msg_user",
        role: "user",
        content: "移动端先看什么？",
        status: "succeeded",
        answer_state: null,
        created_at: knowledgeBase.created_at,
        citations: [],
      },
      {
        id: "msg_assistant",
        role: "assistant",
        content: "移动端应当先显示回答。",
        status: "succeeded",
        answer_state: "grounded",
        created_at: knowledgeBase.created_at,
        citations: [
          {
            id: "citation_mobile",
            index: 1,
            document_id: "doc_mobile",
            display_name: "mobile.pdf",
            page_number: 1,
            section_title: null,
            char_start: null,
            char_end: null,
            extraction_method: "text",
            text: "移动端证据片段",
            source_type: "knowledge_base",
            source_url: null,
            source_domain: null,
            trusted: true,
          },
        ],
      },
    ],
    created_at: knowledgeBase.created_at,
    updated_at: knowledgeBase.updated_at,
  });

  render(<App />);

  expect(await screen.findByText("移动端应当先显示回答。")).toBeInTheDocument();
  expect(
    screen.queryByRole("dialog", { name: "引用来源" }),
  ).not.toBeInTheDocument();

  const toggle = screen.getByRole("button", { name: "切换引用来源" });
  await waitFor(() => {
    expect(toggle).toBeEnabled();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "citation-drawer");
  });
  await user.click(toggle);

  const drawer = screen.getByRole("dialog", { name: "引用来源" });
  expect(drawer).toHaveAttribute("aria-modal", "true");
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: "关闭引用来源" })).toHaveFocus();
  expect(document.body.style.overflow).toBe("hidden");

  await user.keyboard("{Escape}");
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "引用来源" })).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
  expect(document.body.style.overflow).toBe("");
});

it("删除文档前二次确认并在成功后刷新列表", async () => {
  const user = userEvent.setup();
  const document = {
    id: "doc_1",
    knowledge_base_id: knowledgeBase.id,
    display_name: "guide.pdf",
    size_bytes: 2048,
    page_count: 3,
    status: "ready",
    error: null,
    created_at: knowledgeBase.created_at,
    updated_at: knowledgeBase.updated_at,
  };
  window.history.pushState({}, "", `/knowledge/${knowledgeBase.id}`);
  productApi.listDocuments
    .mockResolvedValueOnce([document])
    .mockResolvedValue([]);

  render(<App />);

  expect(await screen.findByText("2 KB · 3 页")).toBeInTheDocument();
  const documentTable = screen.getByRole("table", { name: "知识库文档" });
  expect(within(documentTable).getAllByRole("columnheader")).toHaveLength(5);
  expect(within(documentTable).getAllByRole("row")).toHaveLength(2);
  expect(
    within(documentTable).getByRole("cell", {
      name: "文档状态：可用于问答",
    }),
  ).toHaveAttribute("aria-live", "polite");

  await user.click(
    screen.getByRole("button", { name: "删除 guide.pdf" }),
  );
  const dialog = screen.getByRole("alertdialog", { name: "删除文档？" });
  expect(dialog).toHaveTextContent("历史回答中的引用文字会保留");
  expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();

  await user.click(screen.getByRole("button", { name: "取消" }));
  expect(productApi.deleteDocument).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "删除 guide.pdf" }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));

  await waitFor(() => {
    expect(productApi.deleteDocument).toHaveBeenCalledWith(document.id);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});

it("删除知识库前说明级联范围并在成功后离开详情页", async () => {
  const user = userEvent.setup();
  window.history.pushState({}, "", `/knowledge/${knowledgeBase.id}`);

  render(<App />);

  await user.click(
    await screen.findByRole("button", { name: "删除知识库" }),
  );
  const dialog = screen.getByRole("alertdialog", { name: "删除整个知识库？" });
  expect(dialog).toHaveTextContent("2 份文档");
  expect(dialog).toHaveTextContent("1 段对话");
  expect(dialog).toHaveTextContent("全部文件和向量数据");

  await user.click(screen.getByRole("button", { name: "取消" }));
  expect(productApi.deleteKnowledgeBase).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "删除知识库" }));
  await user.click(
    within(screen.getByRole("alertdialog")).getByRole("button", {
      name: "删除知识库",
    }),
  );

  await waitFor(() => {
    expect(productApi.deleteKnowledgeBase).toHaveBeenCalledWith(knowledgeBase.id);
    expect(window.location.pathname).toBe("/knowledge");
  });
});

it("旧知识库显示安全提示并可完整重建索引", async () => {
  const user = userEvent.setup();
  const legacyKnowledgeBase = {
    ...knowledgeBase,
    index_status: "not_indexed",
    index_manifest: null,
  };
  const rebuiltKnowledgeBase = {
    ...knowledgeBase,
    index_previous_collection: "kb_product__v_previous",
  };
  window.history.pushState({}, "", `/knowledge/${knowledgeBase.id}`);
  productApi.getKnowledgeBase.mockResolvedValue(legacyKnowledgeBase);
  productApi.reindexKnowledgeBase.mockResolvedValue(rebuiltKnowledgeBase);

  render(<App />);

  expect(await screen.findByText("需要重建索引")).toBeInTheDocument();
  expect(screen.getByText(/问答会暂时阻止检索/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "立即重建" }));

  await waitFor(() => {
    expect(productApi.reindexKnowledgeBase).toHaveBeenCalledWith(knowledgeBase.id);
  });
  expect(await screen.findByText("索引版本已验证")).toBeInTheDocument();
  expect(screen.getAllByText("text-embedding-v4")).toHaveLength(2);
});

it("首页会阻止旧索引继续提问并引导到重建页面", async () => {
  const legacyKnowledgeBase = {
    ...knowledgeBase,
    index_status: "not_indexed",
    index_manifest: null,
  };
  productApi.listKnowledgeBases.mockResolvedValue([legacyKnowledgeBase]);

  render(<App />);

  expect(await screen.findByText("先验证这个知识库的索引")).toBeInTheDocument();
  expect(screen.getByLabelText("输入问题")).toBeDisabled();
  expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(
    screen.getByRole("link", { name: "前往重建索引" }),
  ).toHaveAttribute("href", `/knowledge/${knowledgeBase.id}`);
});

it("历史对话显示其实际知识库并提供正确的索引恢复入口", async () => {
  const user = userEvent.setup();
  const conversationKnowledgeBase = {
    ...knowledgeBase,
    id: "kb_conversation",
    name: "项目验收资料",
    is_current: false,
    index_status: "not_indexed",
    index_manifest: null,
  };
  window.history.pushState({}, "", "/chat/conv_context");
  productApi.listKnowledgeBases.mockResolvedValue([
    knowledgeBase,
    conversationKnowledgeBase,
  ]);
  productApi.listConversations.mockResolvedValue([
    {
      id: "conv_context",
      knowledge_base_id: conversationKnowledgeBase.id,
      knowledge_base_name: conversationKnowledgeBase.name,
      title: "需要恢复的对话",
      created_at: knowledgeBase.created_at,
      updated_at: knowledgeBase.updated_at,
    },
  ]);
  productApi.getConversation.mockResolvedValue({
    id: "conv_context",
    title: "需要恢复的对话",
    knowledge_base: conversationKnowledgeBase,
    messages: [],
    created_at: knowledgeBase.created_at,
    updated_at: knowledgeBase.updated_at,
  });

  render(<App />);

  const knowledgeBaseButton = await screen.findByRole("button", {
    name: "打开知识库：项目验收资料",
  });
  expect(screen.getByRole("link", { name: "前往重建" })).toHaveAttribute(
    "href",
    `/knowledge/${conversationKnowledgeBase.id}`,
  );
  expect(screen.getByRole("button", { name: "联网搜索" })).toBeDisabled();

  await user.click(knowledgeBaseButton);
  expect(window.location.pathname).toBe(
    `/knowledge/${conversationKnowledgeBase.id}`,
  );
});

it("删除对话只说明消息范围并在成功后回到新对话", async () => {
  const user = userEvent.setup();
  window.history.pushState({}, "", "/chat/conv_1");

  render(<App />);

  const deleteTrigger = await screen.findByRole("button", { name: "删除对话" });
  await user.click(deleteTrigger);
  const dialog = screen.getByRole("alertdialog", { name: "删除这段对话？" });
  expect(dialog).toHaveTextContent("2 条消息");
  expect(dialog).toHaveTextContent("知识库、文档和向量数据不会受到影响");
  const closeButton = within(dialog).getByRole("button", { name: "关闭" });
  const cancelButton = within(dialog).getByRole("button", { name: "取消" });
  const confirmButton = within(dialog).getByRole("button", { name: "确认删除" });
  expect(cancelButton).toHaveFocus();

  closeButton.focus();
  await user.tab({ shift: true });
  expect(confirmButton).toHaveFocus();
  await user.tab();
  expect(closeButton).toHaveFocus();

  await user.keyboard("{Escape}");
  expect(productApi.deleteConversation).not.toHaveBeenCalled();
  expect(deleteTrigger).toHaveFocus();

  await user.click(deleteTrigger);
  await user.click(screen.getByRole("button", { name: "确认删除" }));

  await waitFor(() => {
    expect(productApi.deleteConversation).toHaveBeenCalledWith("conv_1");
    expect(window.location.pathname).toBe("/");
  });
});

it("向量库故障明确显示为系统失败并允许重试", async () => {
  const user = userEvent.setup();
  window.history.pushState({}, "", "/chat/conv_1");
  productApi.streamMessage.mockRejectedValueOnce(
    new productApi.ProductApiError(
      "向量检索服务暂时不可用，请稍后重试。",
      "VECTOR_DB_UNAVAILABLE",
      true,
    ),
  );

  render(<App />);

  await user.click(await screen.findByRole("button", { name: "重新生成" }));

  expect(await screen.findByText("检索服务暂时不可用")).toBeInTheDocument();
  expect(
    screen.getByText("这是系统故障，不代表知识库中没有相关资料。"),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/当前资料中没有找到足够依据/),
  ).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "重新尝试" }));

  await waitFor(() => {
    expect(productApi.streamMessage).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("检索服务暂时不可用")).not.toBeInTheDocument();
  });
});

it("后端运行时未就绪时显示可恢复的服务状态", async () => {
  const user = userEvent.setup();
  window.history.pushState({}, "", "/chat/conv_1");
  productApi.streamMessage.mockRejectedValueOnce(
    new productApi.ProductApiError(
      "问答服务正在恢复依赖，请稍后重试。",
      "RUNTIME_INITIALIZATION_FAILED",
      true,
    ),
  );

  render(<App />);

  await user.click(await screen.findByRole("button", { name: "重新生成" }));

  expect(await screen.findByText("问答服务暂时未就绪")).toBeInTheDocument();
  expect(
    screen.getByText("这是系统故障，不代表知识库中没有相关资料。"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新尝试" })).toBeEnabled();
});

it("刷新后仍将失败回答标记为系统故障", async () => {
  window.history.pushState({}, "", "/chat/conv_1");
  productApi.getConversation.mockResolvedValue({
    id: "conv_1",
    title: "故障对话",
    knowledge_base: knowledgeBase,
    messages: [
      {
        id: "msg_user",
        role: "user",
        content: "资料里有答案吗？",
        status: "succeeded",
        answer_state: null,
        created_at: knowledgeBase.created_at,
        citations: [],
      },
      {
        id: "msg_failed",
        role: "assistant",
        content: "向量检索服务暂时不可用，请稍后重试。",
        status: "failed",
        answer_state: "failed",
        created_at: knowledgeBase.created_at,
        citations: [],
      },
    ],
    created_at: knowledgeBase.created_at,
    updated_at: knowledgeBase.updated_at,
  });

  render(<App />);

  expect(
    await screen.findByText("系统没有完成本次检索，这不代表知识库中没有相关资料。"),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "有帮助" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新生成" })).toBeInTheDocument();
});

it("索引不兼容时引导用户返回知识库处理", async () => {
  const user = userEvent.setup();
  window.history.pushState({}, "", "/chat/conv_1");
  productApi.streamMessage.mockRejectedValueOnce(
    new productApi.ProductApiError(
      "当前知识库索引与 Embedding 模型不兼容，需要重新处理资料。",
      "VECTOR_DIMENSION_MISMATCH",
      false,
    ),
  );

  render(<App />);

  await user.click(await screen.findByRole("button", { name: "重新生成" }));
  expect(await screen.findByText("知识库索引需要处理")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "查看知识库" }));

  await waitFor(() => {
    expect(window.location.pathname).toBe(`/knowledge/${knowledgeBase.id}`);
  });
});
