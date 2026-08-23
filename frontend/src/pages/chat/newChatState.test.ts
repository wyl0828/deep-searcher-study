import { expect, it } from "vitest";

import type { QueryScope } from "../../product-api";
import {
  QUERY_SCOPE_COPY,
  getQueryScopeChatState,
  isAskableQueryScope,
} from "./newChatState";

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
} satisfies QueryScope;

it("使用普通用户首页最终 Hero 文案", () => {
  expect(QUERY_SCOPE_COPY.ready.title).toBe("从企业知识中找到可靠答案");
  expect(QUERY_SCOPE_COPY.ready.description).toBe(
    "自动检索你可访问的知识，并提供可核验来源。",
  );
  expect(QUERY_SCOPE_COPY.partial.title).toBe("从企业知识中找到可靠答案");
  expect(QUERY_SCOPE_COPY.partial.description).toBe(
    "自动检索你可访问的知识，并提供可核验来源。",
  );
});

it("按 Query Scope 状态决定首页可问答能力", () => {
  expect(getQueryScopeChatState(undefined)).toBe("unknown");
  expect(getQueryScopeChatState(readyScope)).toBe("ready");
  expect(isAskableQueryScope(readyScope)).toBe(true);
  expect(
    isAskableQueryScope({ ...readyScope, state: "no_access", askable: false }),
  ).toBe(false);
});
