import type { QueryScope, QueryScopeState } from "../../product-api";

export type QueryScopeChatState = QueryScopeState;

export const QUERY_SCOPE_COPY: Record<
  QueryScopeChatState,
  { title: string; description: string; actionLabel: string }
> = {
  ready: {
    title: "从企业知识中找到可靠答案",
    description: "自动检索你可访问的知识，并提供可核验来源。",
    actionLabel: "开始提问",
  },
  no_access: {
    title: "暂无可访问的知识资料",
    description: "管理员为你配置知识访问后，即可开始提问。",
    actionLabel: "联系管理员",
  },
  no_documents: {
    title: "知识资料还没有准备好",
    description: "当前可访问的知识范围还没有准备好，请联系管理员。",
    actionLabel: "联系管理员",
  },
  processing: {
    title: "知识资料正在准备中",
    description: "资料正在处理，完成后即可开始问答。",
    actionLabel: "刷新状态",
  },
  failed: {
    title: "知识资料暂时不可用",
    description: "资料处理出现问题，请联系管理员处理。",
    actionLabel: "联系管理员",
  },
  needs_rebuild: {
    title: "知识资料暂时不可用",
    description: "知识索引需要维护，请联系管理员处理。",
    actionLabel: "联系管理员",
  },
  partial: {
    title: "从企业知识中找到可靠答案",
    description: "自动检索你可访问的知识，并提供可核验来源。",
    actionLabel: "开始提问",
  },
  unavailable: {
    title: "知识资料暂时不可用",
    description: "当前知识范围存在多项处理问题，请联系管理员。",
    actionLabel: "联系管理员",
  },
  unknown: {
    title: "正在确认知识资料状态",
    description: "暂时无法确认当前知识范围，请稍后刷新或联系管理员。",
    actionLabel: "联系管理员",
  },
};

export function getQueryScopeChatState(scope: QueryScope | undefined): QueryScopeChatState {
  return scope?.state || "unknown";
}

export function isAskableQueryScope(scope: QueryScope | undefined): boolean {
  return Boolean(scope?.askable);
}
