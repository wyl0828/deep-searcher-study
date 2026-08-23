import { render, screen, waitFor } from "@testing-library/react";
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
  getAuthStatus: vi.fn(),
  setupWorkspace: vi.fn(),
  loginWorkspace: vi.fn(),
  logoutWorkspace: vi.fn(),
  getQueryScope: vi.fn(),
  listUsers: vi.fn(),
  createUser: vi.fn(),
  listAdminKnowledgeBases: vi.fn(),
  listAdminKnowledgeBaseDocuments: vi.fn(),
  listAdminKnowledgeBasePermissions: vi.fn(),
  listAdminUsers: vi.fn(),
  listAdminDepartments: vi.fn(),
  listDepartmentUsers: vi.fn(),
  getCompanyWideKnowledgeAccess: vi.fn(),
  getDepartmentKnowledgeAccess: vi.fn(),
  getUserKnowledgeAccess: vi.fn(),
  setCompanyWideKnowledgeAccess: vi.fn(),
  setDepartmentKnowledgeAccess: vi.fn(),
  setUserKnowledgeAccess: vi.fn(),
  updateUser: vi.fn(),
  listWorkspaceMembers: vi.fn(),
  addWorkspaceMember: vi.fn(),
  getAdminKnowledgeHealth: vi.fn(),
  listAdminIngestJobs: vi.fn(),
  retryAdminDocument: vi.fn(),
  listAdminRuns: vi.fn(),
  getAdminRun: vi.fn(),
  getSystemDiagnostics: vi.fn(),
  getDashboardOverview: vi.fn(),
  getDashboardTrends: vi.fn(),
  listAuditLogs: vi.fn(),
  listKnowledgeBases: vi.fn(),
  createKnowledgeBase: vi.fn(),
  listWorkspaces: vi.fn(),
  getKnowledgeBase: vi.fn(),
  setCurrentKnowledgeBase: vi.fn(),
  reindexKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  listDocuments: vi.fn(),
  uploadDocument: vi.fn(),
  updateDocumentGovernanceMetadata: vi.fn(),
  retryDocument: vi.fn(),
  deleteDocument: vi.fn(),
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  deleteConversation: vi.fn(),
  sendMessage: vi.fn(),
  streamMessage: vi.fn(),
  submitMessageFeedback: vi.fn(),
  cancelMessageFeedback: vi.fn(),
  addKnowledgeBaseMember: vi.fn(),
  addWorkspaceGroupMember: vi.fn(),
  createKnowledgeHealthSnapshot: vi.fn(),
  createWorkspaceGroup: vi.fn(),
  createWorkspace: vi.fn(),
  deleteWorkspaceGroup: vi.fn(),
  getKnowledgeHealth: vi.fn(),
  getKnowledgeHealthTrend: vi.fn(),
  getWorkspaceGroup: vi.fn(),
  listKnowledgeBaseMembers: vi.fn(),
  listKnowledgeHealthHistory: vi.fn(),
  listWorkspaceGroups: vi.fn(),
  removeWorkspaceMember: vi.fn(),
  removeKnowledgeBaseMember: vi.fn(),
  removeWorkspaceGroupMember: vi.fn(),
  runKnowledgeHealthActions: vi.fn(),
  updateKnowledgeBaseMemberRole: vi.fn(),
  updateWorkspaceGroupRole: vi.fn(),
  updateWorkspaceMemberRole: vi.fn(),
}));

vi.mock("./product-api", () => productApi);

const timestamp = "2026-08-20T10:00:00+08:00";

const knowledgeBase = {
  id: "kb_product",
  name: "企业知识库",
  description: "企业内部资料",
  document_count: 2,
  ready_document_count: 2,
  conversation_count: 1,
  is_current: true,
  index_status: "verified",
  index_manifest: { logical_collection: "collection_v1" },
  index_previous_collection: null,
  collection_name: "collection_v1",
  created_at: timestamp,
  updated_at: timestamp,
};

const readyScope = {
  state: "ready",
  askable: true,
  accessible_knowledge_base_count: 2,
  usable_knowledge_base_count: 2,
  status_counts: {
    usable: 2,
    no_documents: 0,
    processing: 0,
    failed: 0,
    needs_rebuild: 0,
    unknown: 0,
  },
  primary_action: null,
};

const noAccessScope = {
  ...readyScope,
  state: "no_access",
  askable: false,
  accessible_knowledge_base_count: 0,
  usable_knowledge_base_count: 0,
  status_counts: {
    usable: 0,
    no_documents: 0,
    processing: 0,
    failed: 0,
    needs_rebuild: 0,
    unknown: 0,
  },
  primary_action: "contact_admin",
};

const adminUser = {
  id: "usr_admin",
  username: "wyl",
  display_name: "管理员",
  role: "admin",
  is_active: true,
  created_at: timestamp,
};

const memberUser = {
  id: "usr_member",
  username: "hyd",
  display_name: "普通用户",
  role: "member",
  is_active: true,
  created_at: timestamp,
};

const message = {
  id: "msg_trace",
  role: "assistant",
  content: "这是依据企业资料生成的回答。",
  status: "succeeded",
  answer_state: "fully_grounded",
  trust_status: "fully_grounded",
  policy_action: "allow",
  risk_level: "low",
  query_type: "policy",
  provenance_digest: null,
  trust_details: null,
  citations: [],
  claims: [],
  created_at: timestamp,
};

const adminRun = {
  question: "公司的报销流程是什么？",
  message,
  conversation: {
    id: "conv_trace",
    title: "公司的报销流程是什么？",
    created_at: timestamp,
    updated_at: timestamp,
  },
  knowledge_base: null,
  scope: {
    schema_version: 1,
    mode: "auto",
    knowledge_base_ids: [knowledgeBase.id],
    collection_names: [knowledgeBase.collection_name],
    resolved_at: timestamp,
    status_counts: readyScope.status_counts,
    knowledge_bases: [{ id: knowledgeBase.id, name: knowledgeBase.name }],
  },
  answer_run: {
    id: "run_trace",
    status: "succeeded",
    started_at: timestamp,
    finished_at: timestamp,
    total_latency_ms: 820,
    provider: "qwen",
    model: "qwen-plus",
    attempts: [],
    stage_results: null,
  },
  owner: adminUser,
  feedback: { positive: 0, negative: 0, comment_count: 0 },
  feedback_items: [],
};

function setAuth(user) {
  productApi.getAuthStatus.mockResolvedValue({
    setup_required: false,
    authenticated: true,
    user,
  });
}

function setCompactViewport(matches = false) {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: query === "(max-width: 820px)" ? matches : false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

beforeEach(() => {
  vi.clearAllMocks();
  workspaceQueryClient.clear();
  setCompactViewport();
  Element.prototype.scrollIntoView = vi.fn();
  window.history.pushState({}, "", "/");

  setAuth(memberUser);
  productApi.logoutWorkspace.mockResolvedValue({ logged_out: true });
  productApi.getQueryScope.mockResolvedValue(readyScope);
  productApi.listConversations.mockResolvedValue([]);
  productApi.getConversation.mockResolvedValue({
    id: "conv_trace",
    scope_mode: "auto",
    knowledge_base: null,
    messages: [],
    title: "历史对话",
    created_at: timestamp,
    updated_at: timestamp,
  });
  productApi.createConversation.mockResolvedValue({
    id: "conv_new",
    scope_mode: "auto",
    knowledge_base_id: null,
    knowledge_base: null,
    title: "新对话",
    created_at: timestamp,
    updated_at: timestamp,
  });
  productApi.streamMessage.mockResolvedValue({
    user_message: {
      id: "msg_user",
      role: "user",
      content: "公司的报销流程是什么？",
      status: "succeeded",
      answer_state: null,
      created_at: timestamp,
      citations: [],
    },
    assistant_message: message,
  });

  productApi.listAdminKnowledgeBases.mockResolvedValue([knowledgeBase]);
  productApi.listAdminKnowledgeBaseDocuments.mockResolvedValue([]);
  productApi.listAdminKnowledgeBasePermissions.mockResolvedValue({
    knowledge_base_id: knowledgeBase.id,
    items: [],
  });
  productApi.listWorkspaceMembers.mockResolvedValue([]);
  productApi.getAdminKnowledgeHealth.mockResolvedValue({ snapshot: null, current: null });
  productApi.listAdminRuns.mockResolvedValue({
    items: [adminRun],
    total: 1,
    page: 1,
    page_size: 20,
  });
  productApi.getAdminRun.mockResolvedValue(adminRun);
  productApi.getSystemDiagnostics.mockResolvedValue({
    version: 1,
    service: "deepsearcher-workspace",
    status: "ready",
    request_id: "req-diagnostics",
    services: {
      fastapi: { state: "ready" },
      milvus: { state: "ready" },
      llm: { state: "ready" },
      embedding: { state: "ready" },
      ingest_worker: { state: "ready" },
    },
    config: {
      llm_model: "qwen-plus",
      embedding_model: "text-embedding-v4",
      collection: "deepsearcher",
    },
  });
  productApi.getDashboardOverview.mockResolvedValue({
    updated_at: timestamp,
    kpis: {
      users: { value: 2, delta: 0, delta_pct: null },
      knowledge_bases: { value: 1, delta: 0, delta_pct: null },
      documents: { value: 2, ready: 2, failed: 0, delta: 0, delta_pct: null },
      answers_today: { value: 3, delta: null, delta_pct: null },
      conversations: { value: 1, delta: 0, delta_pct: null },
      messages: { value: 2, delta: 0, delta_pct: null },
      feedback: { value: 0, delta: 0, delta_pct: null },
      connector_syncs: { value: 0, delta: null, delta_pct: null },
      audit_logs: { value: 0, delta: null, delta_pct: null },
    },
    health_distribution: { healthy: 1, warning: 0, critical: 0, partial: 0, unknown: 0 },
    quality: {
      success_rate: { value: null, sample_count: 0, numerator: null, denominator: null },
      negative_feedback_rate: { value: null, sample_count: 0, numerator: null, denominator: null },
      feedback_coverage_rate: { value: null, sample_count: 0, numerator: null, denominator: null },
      uncited_answer_count: { value: null, sample_count: 0, numerator: null, denominator: null },
      average_latency_ms: { value: null, sample_count: 0, numerator: null, denominator: null },
      cancelled_count: 0,
    },
  });
  productApi.getDashboardTrends.mockResolvedValue({
    metric: "documents",
    window: "7d",
    granularity: "day",
    series: [],
  });
  productApi.listUsers.mockResolvedValue([adminUser, memberUser]);
  productApi.listAdminUsers.mockResolvedValue({ items: [adminUser, memberUser], total: 2, page: 1, page_size: 100 });
  productApi.listAdminDepartments.mockResolvedValue({ items: [], unassigned: { id: null, name: "未分部门", user_count: 0, knowledge_base_count: 0 } });
  productApi.listDepartmentUsers.mockResolvedValue({ items: [], department: undefined });
  productApi.getCompanyWideKnowledgeAccess.mockResolvedValue({ knowledge_base_ids: [] });
  productApi.getDepartmentKnowledgeAccess.mockResolvedValue({ department_id: "dep_1", knowledge_base_ids: [] });
  productApi.getUserKnowledgeAccess.mockResolvedValue({ user_id: memberUser.id, is_admin: false, inherited_access: [], personal_extra_access: [], department_access_count: 0, company_wide_access_count: 0, effective_access_count: 0 });
  productApi.setCompanyWideKnowledgeAccess.mockResolvedValue({ knowledge_base_ids: [] });
  productApi.setDepartmentKnowledgeAccess.mockResolvedValue({ department_id: "dep_1", knowledge_base_ids: [] });
  productApi.setUserKnowledgeAccess.mockResolvedValue({ user_id: memberUser.id, personal_extra_access: [] });
  productApi.updateUser.mockResolvedValue({ user: memberUser });
  productApi.listAuditLogs.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
  productApi.listKnowledgeBases.mockResolvedValue([knowledgeBase]);
  productApi.listWorkspaces.mockResolvedValue({ items: [] });
});

it("普通用户首页使用最终视觉文案，且不暴露后台知识概念", async () => {
  render(<App />);

  expect(
    await screen.findByRole("heading", { name: "从企业知识中找到可靠答案" }),
  ).toBeVisible();
  expect(screen.getByText("自动检索你可访问的知识，并提供可核验来源。")).toBeVisible();
  expect(screen.getByText("企业知识问答")).toBeVisible();
  expect(screen.getByText("自动检索可访问知识")).toBeVisible();
  expect(screen.getByPlaceholderText("输入您的问题，Shift + Enter 换行...")).toBeVisible();
  expect(screen.getByText("你可以这样问")).toBeVisible();
  expect(screen.getByText("总结资料")).toBeVisible();
  expect(screen.getByText("提取结论")).toBeVisible();
  expect(screen.getByText("行动建议")).toBeVisible();
  expect(screen.queryByText("自动检索 2 个知识域")).not.toBeInTheDocument();
  expect(screen.queryByText("问答范围")).not.toBeInTheDocument();
  expect(screen.queryByText("知识库")).not.toBeInTheDocument();
  expect(screen.queryByText("工作区")).not.toBeInTheDocument();
  expect(screen.queryByText("选择知识库")).not.toBeInTheDocument();
});

it("推荐卡片只填充问题，不会自动发送", async () => {
  const user = userEvent.setup();
  render(<App />);

  const composer = await screen.findByRole("textbox", { name: "输入问题" });
  await user.click(screen.getByText("总结资料"));

  expect(composer).toHaveValue("总结这些资料的核心内容");
  expect(productApi.createConversation).not.toHaveBeenCalled();
  expect(productApi.streamMessage).not.toHaveBeenCalled();
});

it("历史对话按最近七天分组，并支持本地过滤", async () => {
  const user = userEvent.setup();
  productApi.listConversations.mockResolvedValue([
    {
      id: "conv_recent",
      knowledge_base_id: null,
      knowledge_base_name: null,
      scope_mode: "auto",
      title: "近期材料摘要",
      created_at: "2026-08-20T10:00:00+08:00",
      updated_at: "2026-08-20T10:00:00+08:00",
    },
    {
      id: "conv_earlier",
      knowledge_base_id: null,
      knowledge_base_name: null,
      scope_mode: "auto",
      title: "旧政策查询",
      created_at: "2026-07-01T10:00:00+08:00",
      updated_at: "2026-07-01T10:00:00+08:00",
    },
  ]);
  render(<App />);

  expect(await screen.findByText("近期对话")).toBeVisible();
  expect(screen.getByText("更早以前")).toBeVisible();
  expect(screen.getByText("近期材料摘要")).toBeVisible();
  expect(screen.getByText("旧政策查询")).toBeVisible();

  await user.type(screen.getByRole("searchbox", { name: "搜索历史对话" }), "旧");
  expect(screen.queryByText("近期材料摘要")).not.toBeInTheDocument();
  expect(screen.getByText("旧政策查询")).toBeVisible();
});

it("普通用户没有知识访问权限时只看到统一无权限空态", async () => {
  productApi.getQueryScope.mockResolvedValue(noAccessScope);
  render(<App />);

  expect(await screen.findByRole("heading", { name: "暂无可访问的知识资料" })).toBeVisible();
  expect(screen.getByText("管理员为你配置知识访问后，即可开始提问。")).toBeVisible();
  expect(screen.getByText("自动检索可访问知识")).toBeVisible();
  expect(screen.queryByText("请联系管理员为你开通知识访问权限。")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(screen.queryByText("创建知识库")).not.toBeInTheDocument();
});

it("普通用户访问旧知识库或工作区地址会回到问答首页", async () => {
  window.history.pushState({}, "", "/workspaces");
  render(<App />);

  expect(
    await screen.findByRole("heading", { name: "从企业知识中找到可靠答案" }),
  ).toBeVisible();
  await waitFor(() => expect(window.location.pathname).toBe("/"));
});

it("普通用户直达管理员地址时显示 Forbidden 且不加载后台数据", async () => {
  window.history.pushState({}, "", "/admin/runs");
  render(<App />);

  expect(await screen.findByText("403 Forbidden")).toBeVisible();
  expect(screen.getByRole("heading", { name: "无权访问此页面" })).toBeVisible();
  expect(productApi.listAdminRuns).not.toHaveBeenCalled();
});

it("管理员后台使用产品化导航，不再把摄取任务作为一级页面", async () => {
  setAuth(adminUser);
  window.history.pushState({}, "", "/admin");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "概览" })).toBeVisible();
  expect(screen.getAllByText("知识库").length).toBeGreaterThan(0);
  expect(screen.getByText("用户管理")).toBeVisible();
  expect(screen.getByText("回答记录")).toBeVisible();
  expect(screen.getByText("系统状态")).toBeVisible();
  expect(screen.getByText("操作审计")).toBeVisible();
  expect(screen.queryByText("摄取任务")).not.toBeInTheDocument();
  expect(screen.getAllByText("回答质量").length).toBeGreaterThan(0);
  expect(screen.getAllByText("样本不足").length).toBeGreaterThan(0);
});

it("管理员旧摄取地址兼容跳转到知识库页面", async () => {
  setAuth(adminUser);
  window.history.pushState({}, "", "/admin/ingestion?status=failed");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "知识库" })).toBeVisible();
  await waitFor(() => expect(window.location.pathname).toBe("/admin/knowledge"));
  expect(screen.queryByRole("heading", { name: "摄取任务" })).not.toBeInTheDocument();
});

it("回答记录只显示真实 AnswerRun 阶段，缺失时标记未采集", async () => {
  setAuth(adminUser);
  window.history.pushState({}, "", "/admin/runs/msg_trace");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "公司的报销流程是什么？" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "未采集" })).toBeVisible();
  expect(screen.getByText("执行时知识范围")).toBeVisible();
  expect(screen.getByText("Collection：collection_v1")).toBeVisible();
  expect(screen.queryByText("Query Understanding")).not.toBeInTheDocument();
  expect(screen.getByText(/不代表用户当前仍拥有这些权限/)).toBeVisible();
});

it("管理员可以从回答记录看到自动范围而不是伪造单一知识库锚点", async () => {
  setAuth(adminUser);
  window.history.pushState({}, "", "/admin/runs");
  render(<App />);

  expect(await screen.findByText("自动（1 个知识域）")).toBeVisible();
  expect(screen.getByText("公司的报销流程是什么？")).toBeVisible();
});

it("新建问答使用 auto scope 创建对话，不向用户要求选择知识库", async () => {
  const user = userEvent.setup();
  render(<App />);

  const composer = await screen.findByRole("textbox", { name: "输入问题" });
  await user.type(composer, "公司的报销流程是什么？");
  await user.click(screen.getByRole("button", { name: "发送问题" }));

  await waitFor(() => expect(productApi.createConversation).toHaveBeenCalledWith());
  expect(productApi.streamMessage).toHaveBeenCalledWith(
    "conv_new",
    "公司的报销流程是什么？",
    expect.any(Function),
    expect.any(AbortSignal),
    false,
  );
});

it("管理员回答详情展示历史范围快照，但不把快照当作当前授权", async () => {
  setAuth(adminUser);
  window.history.pushState({}, "", "/admin/runs/msg_trace");
  render(<App />);

  expect(await screen.findByText(/执行时保存的历史事实/)).toBeVisible();
  expect(screen.getByText("Collection：collection_v1")).toBeVisible();
  expect(screen.getByText(/当前仍拥有这些权限/)).toBeVisible();
});

it("用户管理非法 tab 规范化为 users，并按当前 tab 加载数据", async () => {
  setAuth(adminUser);
  window.history.pushState({}, "", "/admin/users?tab=invalid");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "用户管理" })).toBeVisible();
  await waitFor(() => expect(window.location.search).toBe("?tab=users"));
  expect(productApi.listAdminUsers).toHaveBeenCalled();
  expect(productApi.listAdminKnowledgeBases).not.toHaveBeenCalled();
  expect(productApi.getCompanyWideKnowledgeAccess).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("link", { name: "全员知识" }));
  await waitFor(() => expect(window.location.search).toBe("?tab=company-wide"));
  expect(await screen.findByRole("heading", { name: "全员可访问知识" })).toBeVisible();
  expect(productApi.getCompanyWideKnowledgeAccess).toHaveBeenCalled();
});

it("用户详情把继承访问设为只读，只为个人额外访问渲染 checkbox", async () => {
  setAuth(adminUser);
  productApi.listAdminUsers.mockResolvedValue({ items: [memberUser], total: 1, page: 1, page_size: 100 });
  productApi.listAdminKnowledgeBases.mockResolvedValue([
    { ...knowledgeBase, id: "kb_inherited", name: "继承资料" },
    { ...knowledgeBase, id: "kb_extra", name: "个人资料" },
  ]);
  productApi.getUserKnowledgeAccess.mockResolvedValue({
    user_id: memberUser.id,
    is_admin: false,
    inherited_access: [{ knowledge_base_id: "kb_inherited", knowledge_base_name: "继承资料", sources: ["company_wide", "department"] }],
    personal_extra_access: [{ knowledge_base_id: "kb_extra", knowledge_base_name: "个人资料" }],
    department_access_count: 1,
    company_wide_access_count: 1,
    effective_access_count: 2,
  });
  window.history.pushState({}, "", `/admin/users/${memberUser.id}`);
  render(<App />);

  expect(await screen.findByRole("heading", { name: "个人额外知识" })).toBeVisible();
  expect(screen.getByText("继承资料")).toBeVisible();
  expect(screen.getByText("全员")).toBeVisible();
  expect(screen.getByText("部门")).toBeVisible();
  const checkboxes = screen.getAllByRole("checkbox");
  expect(checkboxes).toHaveLength(1);
  expect(checkboxes[0]).toBeChecked();
});

it("失败回答详情只显示可诊断事实，不渲染质量和引用卡片", async () => {
  setAuth(adminUser);
  const failedRun = {
    ...adminRun,
    message: { ...message, status: "failed", content: "向量检索服务暂时不可用。", answer_state: "failed" },
    answer_run: { ...adminRun.answer_run, status: "failed", request_id: "request-failed-1" },
  };
  productApi.getAdminRun.mockResolvedValue(failedRun);
  window.history.pushState({}, "", "/admin/runs/msg_trace");
  render(<App />);

  expect(await screen.findByText("向量检索服务暂时不可用。")).toBeVisible();
  expect(screen.getByText("request-failed-1")).toBeVisible();
  expect(screen.getByRole("link", { name: "查看系统状态" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "可信度 / 风险" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "反馈分析" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "声明" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "引用资料" })).not.toBeInTheDocument();
});

it("Dashboard 主 KPI 展示上海自然日今日问答", async () => {
  setAuth(adminUser);
  window.history.pushState({}, "", "/admin");
  render(<App />);

  expect(await screen.findByText("今日问答")).toBeVisible();
  expect(screen.getByText("上海自然日 · AnswerRun 创建尝试")).toBeVisible();
});

it("审计旧快照只回退到 ID，新权限快照展示名称", async () => {
  setAuth(adminUser);
  productApi.listAuditLogs.mockResolvedValue({
    items: [{
      id: "aud_1",
      biz_type: "knowledge_access",
      biz_id: "user_1",
      operation_type: "SET_USER_KB_ACCESS",
      action_desc: "修改用户额外知识访问",
      before_snapshot: [{ id: "kb_legacy" }],
      after_snapshot: [{ knowledge_base_id: "kb_new", knowledge_base_name: "新权限资料" }],
      change_diff: [],
      operator_id: "usr_admin",
      operator_name: "管理员",
      operator_role: "admin",
      success: true,
      error_message: null,
      request_id: null,
      ip: null,
      user_agent: null,
      created_at: timestamp,
    }],
    total: 1,
    page: 1,
    page_size: 20,
  });
  window.history.pushState({}, "", "/admin/audit");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "操作审计" })).toBeVisible();
  await userEvent.click(await screen.findByText("修改用户额外知识访问"));
  expect(screen.getByText("kb_legacy")).toBeVisible();
  expect(screen.getByText("新权限资料")).toBeVisible();
});
