export type CollectionIndexManifest = {
  schema_version: number;
  logical_collection: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_version: string;
  embedding_fingerprint: string;
  dimension: number;
  normalization: string;
  metric_type: string;
  chunk_size: number;
  chunk_overlap: number;
  chunk_algorithm?: string;
  chunk_config_version: string;
  document_version: string;
  data_version: string;
  created_at: string;
  manifest_fingerprint: string;
};

export type KnowledgeBase = {
  id: string;
  name: string;
  description: string;
  document_count: number;
  ready_document_count: number;
  conversation_count: number;
  is_current: boolean;
  index_status: "verified" | "not_indexed";
  index_manifest: CollectionIndexManifest | null;
  index_previous_collection: string | null;
  is_company_wide: boolean;
  created_at: string;
  updated_at: string;
};

export type AdminKnowledgeBase = KnowledgeBase & {
  health_status: "healthy" | "warning" | "critical" | "partial" | null;
  health_score: number | null;
  health_updated_at: string | null;
};

export type KnowledgeHealthComputed = {
  formula_version: string;
  status: "complete" | "partial";
  level: "healthy" | "warning" | "critical" | null;
  overall_score: number | null;
  data_score: number | null;
  retrieval_score: number | null;
  trust_score: number | null;
  metrics: {
    data: {
      total_documents: number;
      ready_documents: number;
      failed_documents: number;
      empty_documents: number;
      total_pages: number;
      index_verified: boolean;
      temporal_metadata_ratio: number;
      series_penalty: number;
    };
    retrieval: {
      message_sample_count: number;
      citation_coverage_rate: number;
      avg_citations_per_message: number;
      refusal_rate: number;
      insufficient_evidence_rate: number;
      web_source_rate: number;
      attribution: {
        unreferenced_documents: { id: string; display_name: string }[];
        referenced_ready_coverage: number | null;
        refusal_by_query_type: {
          query_type: string;
          sample_count: number;
          count: number;
          rate: number;
        }[];
        insufficient_by_query_type: {
          query_type: string;
          sample_count: number;
          count: number;
          rate: number;
        }[];
      };
    };
    trust: {
      claim_count: number;
      supported_claim_rate: number;
      conflicting_claim_rate: number;
      invalid_citation_rate: number;
      consistency_issue_rate: number;
      entailment_contradiction_rate: number;
    };
  };
  deductions: {
    code: string;
    reason: string;
    impact: string;
    document_ids?: string[];
    version_family?: string;
  }[];
  actions: {
    code: string;
    action: string;
    priority: "high" | "medium" | "low";
  }[];
};

export type KnowledgeHealthSnapshot = KnowledgeHealthComputed & {
  id: string;
  knowledge_base_id: string;
  created_at: string;
};

export type KnowledgeHealthView = {
  snapshot: KnowledgeHealthSnapshot | null;
  current: KnowledgeHealthComputed;
};

export type KnowledgeHealthSnapshotResult = {
  snapshot: KnowledgeHealthSnapshot;
  previous: KnowledgeHealthSnapshot | null;
  change: {
    direction: "new" | "changed";
    overall_delta: number | null;
    data_delta: number | null;
    retrieval_delta: number | null;
    trust_delta: number | null;
  };
};

export type HealthTrendItem = {
  created_at: string;
  overall: number | null;
  data: number | null;
  retrieval: number | null;
  trust: number | null;
  level: "healthy" | "warning" | "critical" | null;
};

export type HealthActionResult = {
  code: string;
  status: "succeeded" | "failed" | "requires_user_action";
  affected_count: number;
  message: string;
};

export type HealthActionsRunResult = {
  results: HealthActionResult[];
  snapshot: KnowledgeHealthSnapshot;
  delta: KnowledgeHealthSnapshotResult["change"];
};

export type ProductUser = {
  id: string;
  username: string;
  display_name: string;
  role: "admin" | "member";
  is_active: boolean;
  department_id?: string | null;
  department_name?: string | null;
  last_activity_at?: string | null;
  created_at: string;
  accessible_knowledge_base_count?: number;
};

export type Department = {
  id: string;
  name: string;
  user_count: number;
  knowledge_base_count: number;
  created_at?: string;
  updated_at?: string;
};

export type DepartmentList = {
  items: Department[];
  unassigned: {
    id: null;
    name: string;
    user_count: number;
    knowledge_base_count: number;
  };
};

export type AdminUsersPage = {
  items: ProductUser[];
  total: number;
  page: number;
  page_size: number;
};

export type KnowledgeAccessSummary = {
  is_company_wide: boolean;
  departments: Array<{ id: string; name: string }>;
  direct_users: Array<{ id: string; username: string; display_name: string }>;
  accessible_user_count: number;
};

export type InheritedKnowledgeAccess = {
  knowledge_base_id: string;
  knowledge_base_name: string;
  sources: Array<"company_wide" | "department">;
};

export type PersonalExtraKnowledgeAccess = {
  knowledge_base_id: string;
  knowledge_base_name: string;
};

export type UserKnowledgeAccess = {
  user_id: string;
  is_admin: boolean;
  inherited_access: InheritedKnowledgeAccess[];
  personal_extra_access: PersonalExtraKnowledgeAccess[];
  department_access_count: number;
  company_wide_access_count: number;
  effective_access_count: number;
};

export type AuthStatus = {
  setup_required: boolean;
  authenticated: boolean;
  user: ProductUser | null;
};

export type OperationAuditLog = {
  id: string;
  biz_type: string;
  biz_id: string;
  operation_type: string;
  action_desc: string;
  before_snapshot: unknown;
  after_snapshot: unknown;
  change_diff:
    | Array<{ field: string; before: unknown; after: unknown }>
    | null;
  operator_id: string;
  operator_name: string | null;
  operator_role: string | null;
  success: boolean;
  error_message: string | null;
  request_id: string | null;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
};

export type AuditLogPage = {
  items: OperationAuditLog[];
  total: number;
  page: number;
  page_size: number;
};

export type ProductDocument = {
  id: string;
  knowledge_base_id: string;
  display_name: string;
  size_bytes: number;
  page_count: number;
  status: "queued" | "processing" | "ready" | "failed";
  error: { code: string; message: string } | null;
  published_at: string | null;
  effective_at: string | null;
  superseded_at: string | null;
  temporal_metadata_source: "user_declared" | "connector" | "admin_verified" | null;
  version_family: string | null;
  version_family_source: "user_declared" | "connector" | "admin_verified" | null;
  created_at: string;
  updated_at: string;
  processing?: IngestJob | null;
};

export type DocumentGovernanceInput = {
  published_at: string | null;
  effective_at: string | null;
  superseded_at: string | null;
  version_family: string | null;
};

export type IngestJob = {
  id: string;
  document_id: string;
  status: "queued" | "processing" | "succeeded" | "dead_letter";
  attempt: number;
  retry_count: number;
  max_retries: number;
  available_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: { code: string; message: string } | null;
};

export type AdminIngestJob = IngestJob & {
  document: ProductDocument;
  knowledge_base: {
    id: string;
    name: string;
  };
};

export type AdminIngestJobPage = {
  items: AdminIngestJob[];
  total: number;
  page: number;
  page_size: number;
};

/**
 * The answer mode is optional on purpose: messages created before routing was
 * persisted do not have this field.  Callers must resolve a missing value
 * through resolveMessageAnswerMode instead of treating every historical
 * answer as knowledge-grounded.
 */
export type AnswerMode = "chat" | "knowledge" | "web";
export type NullableAnswerMode = AnswerMode | null;

export type AnswerRun = {
  id: string;
  status: "running" | "succeeded" | "failed" | "cancelled";
  answer_mode?: NullableAnswerMode;
  routing_decision?: Record<string, unknown> | null;
  current_stage?: string | null;
  failure_code?: string | null;
  effective_risk_level?: "low" | "medium" | "high" | null;
  effective_risk_factors?: string[] | null;
  request_id: string | null;
  started_at: string;
  finished_at: string | null;
  total_latency_ms: number | null;
  provider: string | null;
  model: string | null;
  attempts: Array<Record<string, unknown>> | null;
  stage_results: Array<Record<string, unknown>> | null;
};

export type AdminRunItem = {
  question: string | null;
  message: Message;
  conversation: {
    id: string;
    title: string;
    created_at: string;
    updated_at: string;
  };
  knowledge_base: {
    id: string;
    name: string;
  } | null;
  scope: QueryScopeSnapshot & {
    knowledge_bases: Array<{ id: string; name: string }>;
  };
  answer_run: AnswerRun | null;
  owner: {
    id: string;
    username: string;
    display_name: string;
  };
  feedback: {
    positive: number;
    negative: number;
    comment_count: number;
  };
  feedback_items: Array<{
    vote: 1 | -1 | null;
    reason: string | null;
    comment: string | null;
    created_at: string;
  }>;
};

export type AdminRunPage = {
  items: AdminRunItem[];
  total: number;
  page: number;
  page_size: number;
};

export type SystemHealthState = "ready" | "not_ready" | "unknown";

export type SystemHealthService = {
  state: SystemHealthState;
  code?: string;
  retryable?: boolean;
};

export type SystemDiagnostics = {
  version: number;
  service: string;
  status: "ready" | "degraded" | "not_ready";
  request_id: string | null;
  services: {
    fastapi: SystemHealthService;
    milvus: SystemHealthService;
    llm: SystemHealthService;
    embedding: SystemHealthService;
    ingest_worker: SystemHealthService;
  };
  config: {
    llm_model: string;
    embedding_model: string;
    collection: string;
  };
};

export type Citation = {
  id: string;
  index: number;
  document_id: string | null;
  display_name: string;
  page_number: number | null;
  chunk_index: number | null;
  section_title: string | null;
  section_path: string[] | null;
  char_start: number | null;
  char_end: number | null;
  bbox: number[] | null;
  location_id: string | null;
  source_locator: string | null;
  parser_version: string | null;
  extraction_method: string | null;
  source_type: "knowledge_base" | "web";
  source_url: string | null;
  source_domain: string | null;
  trusted: boolean;
  published_at: string | null;
  effective_at: string | null;
  superseded_at: string | null;
  temporal_metadata_source: string | null;
  version_family: string | null;
  version_family_source: string | null;
  text: string;
  supported: boolean;
};

export type CitationSpan = {
  citation_index: number;
  start: number | null;
  end: number | null;
  text: string;
  match_type: "normalized_exact" | "sentence_overlap" | "not_found";
  score: number;
};

export type TrustProvenance = {
  version: number;
  builder_version: string;
  execution_scope: "online" | "stream" | "library" | "evaluation";
  digest: string;
  runtime: {
    configuration_fingerprint: string;
    runtime_version: number | null;
    binding_revision: number | null;
    tenant_fingerprint: string | null;
    model_policy: string;
  };
  generation_model: {
    provider: string;
    model: string;
    version: string;
    fingerprint: string;
  };
  embedding: {
    provider: string;
    model: string;
    version: string;
    dimension: number | null;
    normalization: string;
    fingerprint: string;
  };
  index: {
    selection_mode: "explicit" | "dynamic";
    snapshot_status: "complete" | "partial" | "dynamic_unbound" | "unavailable";
    collection_count: number;
    manifests: Array<{
      status: "verified" | "legacy_unverified" | "unavailable";
      schema_version?: number;
      manifest_fingerprint?: string;
      data_version?: string;
      document_version?: string;
      embedding_fingerprint?: string;
      chunk_config_version?: string;
    }>;
  };
  evidence?: {
    snapshot_status: "unbound" | "complete" | "partial";
    evidence_count: number;
    knowledge_base_count: number;
    web_count: number;
    snapshot_fingerprint: string | null;
    items: Array<{
      position: number;
      source_type: "knowledge_base" | "web";
      content_fingerprint: string;
      source_fingerprint: string | null;
      locator_fingerprint: string | null;
      temporal_fingerprint?: string | null;
      publication_anchor_bound?: boolean;
      version_family_fingerprint?: string | null;
      version_family_bound?: boolean;
      trusted: boolean;
      provider?: string;
    }>;
  };
  temporal?: {
    version: 1;
    source: "request_clock";
    reference_date: string;
    timezone: string;
    fingerprint: string;
  };
  prompts: {
    grounding: { version: string; fingerprint: string };
    entailment: { version: string };
  };
  checkers: Record<string, Record<string, string | number | null>>;
  policy: {
    trust_contract_version: number;
    answer_policy_version: number;
  };
};

export type AnswerClaim = {
  id: string;
  index: number;
  text: string;
  support_status:
    | "supported"
    | "unsupported"
    | "invalid_citation"
    | "conflicting";
  structural_support_status:
    | "supported"
    | "unsupported"
    | "invalid_citation"
    | "conflicting";
  citation_indices: number[];
  citation_spans: CitationSpan[];
  citation_status: "valid" | "missing" | "invalid" | "conflicting";
  entailment_status: "not_checked" | "entailed" | "contradicted" | "unknown";
  consistency_status:
    | "not_checked"
    | "not_applicable"
    | "consistent"
    | "inconsistent"
    | "unknown";
  consistency_checks: Array<{
    kind:
      | "quantity"
      | "date"
      | "relative_time"
      | "negation"
      | "version"
      | "range"
      | "condition"
      | "freshness";
    status: "consistent" | "inconsistent" | "unknown";
    reason_code: string;
    claim_values?: string[];
    missing_values?: string[];
  }>;
  risk_status: "not_assessed" | "passed" | "rejected" | "conflict_disclosed";
  risk_checks: Array<{
    kind: "entailment" | "evidence_count" | "source_count";
    status: "passed" | "failed";
    reason_code: string;
    actual?: string | number;
    required?: string | number;
  }>;
  confidence: number | null;
  reason_codes: string[];
};

export type TrustClaimFinding = {
  index: number;
  text: string;
  support_status?: string;
  structural_support_status?: string;
  citation_status?: string;
  entailment_status?: "not_checked" | "entailed" | "contradicted" | "unknown";
  entailment_method?: "not_checked" | "exact_match" | "semantic_nli";
  entailment_checker?: string;
  entailment_checker_version?: string;
  consistency_status?: string;
  risk_status?: string;
  risk_checks?: Array<{
    kind: string;
    status: "passed" | "failed";
    reason_code: string;
    actual?: string | number;
    required?: string | number;
  }>;
  confidence?: number | null;
  reason_codes?: string[];
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: "pending" | "succeeded" | "failed";
  answer_state:
    | "grounded"
    | "fully_grounded"
    | "partially_grounded"
    | "conflicting_evidence"
    | "insufficient_evidence"
    | "failed"
    | null;
  /** Nullable/optional for historical messages returned by older APIs. */
  answer_mode?: NullableAnswerMode;
  trust_contract_version: number | null;
  trust_status:
    | "fully_grounded"
    | "partially_grounded"
    | "conflicting_evidence"
    | "insufficient_evidence"
    | "not_assessed"
    | null;
  safety_status: "not_evaluated" | "safe" | "unsafe" | null;
  policy_action:
    | "observe"
    | "allow"
    | "downgrade"
    | "disclose_conflict"
    | "refuse"
    | null;
  policy_profile: "standard" | "strict_high_risk" | null;
  policy_reason_codes: string[] | null;
  risk_level: "low" | "medium" | "high" | null;
  query_type: string | null;
  risk_factors: string[] | null;
  provenance_contract_version: number | null;
  provenance_digest: string | null;
  trust_details: {
    verification_level: string;
    evidence_snapshot_available: boolean;
    entailment: {
      version: number;
      checker?: string;
      checker_version?: string;
      status?: "disabled" | "skipped" | "completed" | "partial" | "failed";
      token_usage: number;
      eligible_claim_count: number;
      exact_match_count: number;
      checker_claim_count: number;
      entailed_count: number;
      contradicted_count: number;
      unknown_count: number;
      not_checked_count: number;
      error_code?: string;
    };
    risk: {
      version: number;
      classifier?: string;
      classifier_version?: string;
      risk_level?: "low" | "medium" | "high";
      query_type?: string;
      risk_factors: string[];
      requirements: {
        require_citation: boolean;
        require_decisive_entailment: boolean;
        minimum_evidence_count: number;
        minimum_distinct_source_count: number;
        allow_unknown_entailment: boolean;
      };
    };
    freshness?: {
      version: 1;
      classifier: "deterministic_freshness_intent";
      classifier_version: string;
      required: boolean;
      mode: "none" | "current" | "latest_effective" | "latest_published" | "recent";
      ordering_basis: "none" | "effective_at" | "published_at" | "undefined_window";
      reason_codes: string[];
    };
    temporal_context: {
      version: 1;
      source: "request_clock";
      reference_time: string;
      reference_date: string;
      timezone: string;
    };
    input: {
      trust_status: string | null;
      claim_count: number;
      supported_claim_count: number;
      claims: TrustClaimFinding[];
    };
    output: {
      trust_status: string | null;
      claim_count: number;
      supported_claim_count: number;
      claims: TrustClaimFinding[];
    };
    provenance?: TrustProvenance;
    limitations: string[];
  } | null;
  created_at: string;
  feedback?: { vote: 1 | -1; cancelled: boolean } | null;
  citations: Citation[];
  claims: AnswerClaim[];
};

export type QueryStageEvent =
  | {
      version: 1;
      request_id: string;
      sequence: number;
      event: "started";
      data: { stage: "query_started" | "chat_started" };
    }
  | {
      version: 1;
      request_id: string;
      sequence: number;
      event: "contextualization";
      data: {
        depends_on_history: boolean;
        history_turn_count: number;
        fallback_used: boolean;
        reason: string;
      };
    }
  | {
      version: 1;
      request_id: string;
      sequence: number;
      event: "routing";
      data: { agent: string; fallback_used: boolean };
    }
  | {
      version: 1;
      request_id: string;
      sequence: number;
      event: "iteration";
      data: { iteration: number };
    }
  | {
      version: 1;
      request_id: string;
      sequence: number;
      event: "retrieval";
      data: { iteration: number; retrieved_count: number };
    }
  | {
      version: 1;
      request_id: string;
      sequence: number;
      event: "web_search";
      data: {
        iteration: number;
        status: "disabled" | "completed" | "partial" | "degraded" | "empty";
        provider: string;
        query_count: number;
        result_count: number;
        error_code: string | null;
      };
    }
  | {
      version: 1;
      request_id: string;
      sequence: number;
      event: "support";
      data: { iteration: number; supported_count: number };
    }
  | {
      version: 1;
      request_id: string;
      sequence: number;
      event: "reflection";
      data: { iteration: number; has_enough_information: boolean };
    };

export type ConversationSummary = {
  id: string;
  knowledge_base_id: string | null;
  knowledge_base_name: string | null;
  scope_mode: "auto" | "fixed";
  title: string;
  created_at: string;
  updated_at: string;
};

export type ConversationDetail = {
  id: string;
  title: string;
  scope_mode: "auto" | "fixed";
  knowledge_base: KnowledgeBase | null;
  messages: Message[];
  created_at: string;
  updated_at: string;
};

export type QueryScopeState =
  | "ready"
  | "no_access"
  | "no_documents"
  | "processing"
  | "failed"
  | "needs_rebuild"
  | "partial"
  | "unavailable"
  | "unknown";

export type QueryScope = {
  state: QueryScopeState;
  askable: boolean;
  accessible_knowledge_base_count: number | null;
  usable_knowledge_base_count: number | null;
  status_counts: {
    usable: number | null;
    no_documents: number | null;
    processing: number | null;
    failed: number | null;
    needs_rebuild: number | null;
    unknown: number | null;
  };
  primary_action: "contact_admin" | "refresh" | null;
};

export type QueryScopeSnapshot = {
  schema_version: number;
  mode: "auto" | "fixed";
  knowledge_base_ids: string[];
  collection_names: string[];
  resolved_at: string | null;
  status_counts: QueryScope["status_counts"];
};

const GROUNDED_ANSWER_STATES = new Set([
  "grounded",
  "fully_grounded",
]);

/**
 * Resolve the UI mode without promoting every successful historical answer
 * to a knowledge answer.  Only explicit citations or a grounded state are a
 * safe compatibility signal for records where answer_mode was not persisted.
 */
export function resolveMessageAnswerMode(
  message: Pick<Message, "answer_mode" | "answer_state" | "citations">,
): AnswerMode {
  if (
    message.answer_mode === "chat" ||
    message.answer_mode === "knowledge" ||
    message.answer_mode === "web"
  ) {
    return message.answer_mode;
  }
  if (
    (Array.isArray(message.citations) && message.citations.length > 0) ||
    GROUNDED_ANSWER_STATES.has(message.answer_state || "")
  ) {
    return "knowledge";
  }
  return "chat";
}

type ProductErrorPayload = {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
    retryable?: boolean;
  };
  detail?: string;
};

export class ProductApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly requestId: string | null;

  constructor(
    message: string,
    code = "UNKNOWN_ERROR",
    retryable = false,
    requestId: string | null = null,
  ) {
    super(message);
    this.name = "ProductApiError";
    this.code = code;
    this.retryable = retryable;
    this.requestId = requestId;
  }
}

/**
 * Keep the UI safe when reading records written before the nullable answer
 * fields were introduced.  The server remains the source of truth; this only
 * supplies collection defaults needed by the renderer.
 */
export function normalizeMessage(
  value: Partial<Message> | null | undefined,
): Message {
  const message = (value || {}) as Message;
  return {
    ...message,
    answer_mode: message.answer_mode ?? null,
    citations: Array.isArray(message.citations) ? message.citations : [],
    claims: Array.isArray(message.claims) ? message.claims : [],
  };
}

function normalizeAdminRunItem(item: AdminRunItem): AdminRunItem {
  return {
    ...item,
    message: normalizeMessage(item.message),
    answer_run: item.answer_run
      ? {
          ...item.answer_run,
          answer_mode: item.answer_run.answer_mode ?? null,
          routing_decision: item.answer_run.routing_decision ?? null,
          current_stage: item.answer_run.current_stage ?? null,
          failure_code: item.answer_run.failure_code ?? null,
          effective_risk_level: item.answer_run.effective_risk_level ?? null,
          effective_risk_factors: item.answer_run.effective_risk_factors ?? null,
        }
      : null,
  };
}

async function readResponse<T>(response: Response): Promise<T> {
  let payload: ProductErrorPayload & T;
  try {
    payload = (await response.json()) as ProductErrorPayload & T;
  } catch {
    payload = {} as ProductErrorPayload & T;
  }
  if (!response.ok) {
    throw new ProductApiError(
      payload.error?.message || payload.detail || "操作没有完成，请重新尝试。",
      payload.error?.code,
      Boolean(payload.error?.retryable),
      payload.error?.request_id || response.headers.get("X-Request-ID"),
    );
  }
  return payload;
}

async function requestJson<T>(
  url: string,
  options: RequestInit = {},
  acceptedStatuses: number[] = [],
): Promise<T> {
  const response = await fetch(url, {
    ...options,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok && acceptedStatuses.includes(response.status)) {
    try {
      return (await response.json()) as T;
    } catch {
      throw new ProductApiError("服务返回了无法读取的诊断结果。", "INVALID_RESPONSE");
    }
  }
  return readResponse<T>(response);
}

export function getAuthStatus(): Promise<AuthStatus> {
  return requestJson("/api/auth/status");
}

export async function setupWorkspace(input: {
  username: string;
  password: string;
  display_name: string;
}): Promise<ProductUser> {
  const response = await requestJson<{ user: ProductUser }>("/api/auth/setup", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.user;
}

export async function loginWorkspace(input: {
  username: string;
  password: string;
}): Promise<ProductUser> {
  const response = await requestJson<{ user: ProductUser }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.user;
}

export function logoutWorkspace(): Promise<{ logged_out: boolean }> {
  return requestJson("/api/auth/logout", { method: "POST" });
}

export async function listAdminUsers(input: {
  q?: string;
  department_id?: string;
  role?: "admin" | "member";
  status?: "active" | "inactive";
  page?: number;
  page_size?: number;
} = {}): Promise<AdminUsersPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  return requestJson(`/api/admin/users${query ? `?${query}` : ""}`);
}

export async function createUser(input: {
  username: string;
  password: string;
  display_name: string;
  role: "admin" | "member";
  department_id?: string | null;
}): Promise<ProductUser> {
  const response = await requestJson<{ user: ProductUser }>("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.user;
}

export function updateUser(
  id: string,
  input: {
    display_name?: string;
    role?: "admin" | "member";
    department_id?: string | null;
    is_active?: boolean;
  },
): Promise<{ user: ProductUser }> {
  return requestJson(`/api/admin/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function listAdminDepartments(): Promise<DepartmentList> {
  return requestJson("/api/admin/departments");
}

export function createDepartment(name: string): Promise<{ department: Department }> {
  return requestJson("/api/admin/departments", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function updateDepartment(
  id: string,
  name: string,
): Promise<{ department: Department }> {
  return requestJson(`/api/admin/departments/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export function listDepartmentUsers(
  departmentId: string | null,
): Promise<{ department?: Department; items: ProductUser[] }> {
  return requestJson(
    departmentId
      ? `/api/admin/departments/${departmentId}/users`
      : "/api/admin/departments/unassigned/users",
  );
}

export function getCompanyWideKnowledgeAccess(): Promise<{ knowledge_base_ids: string[] }> {
  return requestJson("/api/admin/knowledge-access/company-wide");
}

export function setCompanyWideKnowledgeAccess(
  knowledgeBaseIds: string[],
): Promise<{ knowledge_base_ids: string[] }> {
  return requestJson("/api/admin/knowledge-access/company-wide", {
    method: "PUT",
    body: JSON.stringify({ knowledge_base_ids: knowledgeBaseIds }),
  });
}

export function getDepartmentKnowledgeAccess(
  departmentId: string,
): Promise<{ department_id: string; knowledge_base_ids: string[] }> {
  return requestJson(`/api/admin/departments/${departmentId}/knowledge-access`);
}

export function setDepartmentKnowledgeAccess(
  departmentId: string,
  knowledgeBaseIds: string[],
): Promise<{ department_id: string; knowledge_base_ids: string[] }> {
  return requestJson(`/api/admin/departments/${departmentId}/knowledge-access`, {
    method: "PUT",
    body: JSON.stringify({ knowledge_base_ids: knowledgeBaseIds }),
  });
}

export function getUserKnowledgeAccess(id: string): Promise<UserKnowledgeAccess> {
  return requestJson(`/api/admin/users/${id}/knowledge-access`);
}

export function setUserKnowledgeAccess(
  id: string,
  knowledgeBaseIds: string[],
): Promise<{
  user_id: string;
  personal_extra_access: PersonalExtraKnowledgeAccess[];
}> {
  return requestJson(`/api/admin/users/${id}/knowledge-access`, {
    method: "PUT",
    body: JSON.stringify({ knowledge_base_ids: knowledgeBaseIds }),
  });
}

export async function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  const response = await requestJson<{ items: KnowledgeBase[] }>(
    "/api/knowledge-bases",
  );
  return response.items;
}

export async function listAdminKnowledgeBases(): Promise<AdminKnowledgeBase[]> {
  const response = await requestJson<{ items: AdminKnowledgeBase[] }>(
    "/api/admin/knowledge-bases",
  );
  return response.items;
}

export async function listAdminKnowledgeBaseDocuments(
  id: string,
): Promise<ProductDocument[]> {
  const response = await requestJson<{ items: ProductDocument[] }>(
    `/api/admin/knowledge-bases/${id}/documents`,
  );
  return response.items;
}

export function getAdminKnowledgeBaseAccessSummary(
  id: string,
): Promise<KnowledgeAccessSummary> {
  return requestJson(`/api/admin/knowledge-bases/${id}/access-summary`);
}

export function getAdminKnowledgeHealth(id: string): Promise<KnowledgeHealthView> {
  return requestJson(`/api/admin/knowledge-bases/${id}/health`);
}

export function createKnowledgeBase(input: {
  name: string;
  description: string;
}): Promise<KnowledgeBase> {
  return requestJson("/api/knowledge-bases", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getKnowledgeBase(id: string): Promise<KnowledgeBase> {
  return requestJson(`/api/knowledge-bases/${id}`);
}

export function setCurrentKnowledgeBase(id: string): Promise<KnowledgeBase> {
  return requestJson(`/api/knowledge-bases/${id}/current`, { method: "PUT" });
}

export function reindexKnowledgeBase(id: string): Promise<KnowledgeBase> {
  return requestJson(`/api/knowledge-bases/${id}/reindex`, { method: "POST" });
}

export function getKnowledgeHealth(id: string): Promise<KnowledgeHealthView> {
  return requestJson(`/api/knowledge-bases/${id}/health`);
}

export function createKnowledgeHealthSnapshot(
  id: string,
): Promise<KnowledgeHealthSnapshotResult> {
  return requestJson(`/api/knowledge-bases/${id}/health/snapshot`, {
    method: "POST",
  });
}

export async function listKnowledgeHealthHistory(
  id: string,
): Promise<KnowledgeHealthSnapshot[]> {
  const response = await requestJson<{ items: KnowledgeHealthSnapshot[] }>(
    `/api/knowledge-bases/${id}/health/history`,
  );
  return response.items;
}

export function deleteKnowledgeBase(id: string): Promise<{
  deleted_id: string;
  current_knowledge_base_id: string | null;
}> {
  return requestJson(`/api/knowledge-bases/${id}`, { method: "DELETE" });
}

export async function listDocuments(id: string): Promise<ProductDocument[]> {
  const response = await requestJson<{ items: ProductDocument[] }>(
    `/api/knowledge-bases/${id}/documents`,
  );
  return response.items;
}

export async function uploadDocument(
  knowledgeBaseId: string,
  file: File,
  temporal: DocumentGovernanceInput,
): Promise<{ document: ProductDocument; job: IngestJob }> {
  const body = new FormData();
  body.append("file", file);
  for (const [field, value] of Object.entries(temporal)) {
    if (value) body.append(field, value);
  }
  const response = await fetch(
    `/api/knowledge-bases/${knowledgeBaseId}/documents`,
    {
      method: "POST",
      credentials: "same-origin",
      body,
    },
  );
  return readResponse(response);
}

export function updateDocumentGovernanceMetadata(
  documentId: string,
  temporal: DocumentGovernanceInput,
): Promise<{ document: ProductDocument; job: IngestJob | null }> {
  return requestJson(`/api/documents/${documentId}/governance-metadata`, {
    method: "PATCH",
    body: JSON.stringify(temporal),
  });
}

export function getIngestJob(id: string): Promise<IngestJob> {
  return requestJson(`/api/ingest-jobs/${id}`);
}

export function listAdminIngestJobs(input: {
  page?: number;
  page_size?: number;
  status?: string;
  knowledge_base_id?: string;
} = {}): Promise<AdminIngestJobPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  return requestJson(`/api/admin/ingest-jobs${query ? `?${query}` : ""}`);
}

export function retryAdminDocument(
  id: string,
): Promise<{ document: ProductDocument; job: IngestJob }> {
  return requestJson(`/api/admin/documents/${id}/retry`, { method: "POST" });
}

export async function listAdminRuns(input: {
  page?: number;
  page_size?: number;
  knowledge_base_id?: string;
  status?: string;
  trust_status?: string;
  risk_level?: string;
  feedback?: string;
} = {}): Promise<AdminRunPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  const response = await requestJson<AdminRunPage>(
    `/api/admin/runs${query ? `?${query}` : ""}`,
  );
  return {
    ...response,
    items: Array.isArray(response.items)
      ? response.items.map(normalizeAdminRunItem)
      : [],
  };
}

export function getAdminRun(messageId: string): Promise<AdminRunItem> {
  return requestJson<AdminRunItem>(`/api/admin/runs/${messageId}`).then(
    normalizeAdminRunItem,
  );
}

export function getSystemDiagnostics(): Promise<SystemDiagnostics> {
  return requestJson(
    "/api/health/diagnostics",
    { method: "POST" },
    [503],
  );
}

export function getQueryScope(): Promise<QueryScope> {
  return requestJson("/api/me/query-scope");
}

export function retryDocument(id: string) {
  return requestJson(`/api/documents/${id}/retry`, { method: "POST" });
}

export function deleteDocument(id: string): Promise<void> {
  return requestJson(`/api/documents/${id}`, { method: "DELETE" });
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await requestJson<{ items: ConversationSummary[] }>(
    "/api/conversations?limit=20",
  );
  return response.items;
}

export function createConversation(
  knowledgeBaseId?: string,
): Promise<ConversationSummary> {
  return requestJson("/api/conversations", {
    method: "POST",
    body: JSON.stringify(
      knowledgeBaseId
        ? { scope_mode: "fixed", knowledge_base_id: knowledgeBaseId }
        : { scope_mode: "auto" },
    ),
  });
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  const response = await requestJson<ConversationDetail>(
    `/api/conversations/${id}`,
  );
  return {
    ...response,
    messages: Array.isArray(response.messages)
      ? response.messages.map(normalizeMessage)
      : [],
  };
}

export function deleteConversation(id: string): Promise<void> {
  return requestJson(`/api/conversations/${id}`, { method: "DELETE" });
}

export async function sendMessage(
    conversationId: string,
    content: string,
    useWebSearch = false,
): Promise<{ user_message: Message; assistant_message: Message }> {
  const response = await requestJson<MessageResult>(
    `/api/conversations/${conversationId}/messages`,
    {
      method: "POST",
      body: JSON.stringify({ content, use_web_search: useWebSearch }),
    },
  );
  return {
    user_message: normalizeMessage(response.user_message),
    assistant_message: normalizeMessage(response.assistant_message),
  };
}

export function submitMessageFeedback(
  conversationId: string,
  messageId: string,
  input: { vote: 1 | -1; reason?: string; comment?: string },
): Promise<{ feedback: { vote: 1 | -1; cancelled: boolean } }> {
  return requestJson(
    `/api/conversations/${conversationId}/messages/${messageId}/feedback`,
    {
      method: "POST",
      body: JSON.stringify(input),
    },
  );
}

export function cancelMessageFeedback(
  conversationId: string,
  messageId: string,
): Promise<void> {
  return requestJson(
    `/api/conversations/${conversationId}/messages/${messageId}/feedback`,
    { method: "DELETE" },
  );
}

type MessageResult = {
  user_message: Message;
  assistant_message: Message;
};

type StreamEnvelope = {
  version?: number;
  request_id?: string;
  sequence?: number;
  event?: string;
  data?: Record<string, unknown>;
};

const QUERY_STAGE_EVENTS = new Set([
    "started",
    "contextualization",
    "routing",
  "iteration",
  "retrieval",
  "web_search",
  "support",
  "reflection",
]);

function parseSseFrame(frame: string): { event: string; payload: StreamEnvelope } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  try {
    const payload = JSON.parse(dataLines.join("\n")) as StreamEnvelope;
    return payload && typeof payload === "object" ? { event, payload } : null;
  } catch {
    return null;
  }
}

function stageEventFromEnvelope(
  event: string,
  envelope: StreamEnvelope,
): QueryStageEvent | null {
  if (!QUERY_STAGE_EVENTS.has(event) || envelope.event !== event) return null;
  if (!envelope.data || typeof envelope.data !== "object") return null;
  return envelope as QueryStageEvent;
}

function terminalError(envelope: StreamEnvelope): ProductApiError {
  const data = envelope.data || {};
  return new ProductApiError(
    typeof data.message === "string" ? data.message : "问答服务没有完成本次查询。",
    typeof data.code === "string" ? data.code : "QUERY_FAILED",
    Boolean(data.retryable),
    envelope.request_id || null,
  );
}

export async function streamMessage(
  conversationId: string,
  content: string,
  onStage: (event: QueryStageEvent) => void,
  signal?: AbortSignal,
  useWebSearch = false,
): Promise<MessageResult> {
  const response = await fetch(
    `/api/conversations/${conversationId}/messages/stream`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, use_web_search: useWebSearch }),
      signal,
    },
  );
  if (!response.ok) return readResponse<MessageResult>(response);
  if (!response.body) {
    throw new ProductApiError(
      "浏览器无法读取实时回答，请重新尝试。",
      "QUERY_STREAM_UNAVAILABLE",
      true,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer = (buffer + decoder.decode(value, { stream: !done })).replace(/\r\n/g, "\n");
      if (buffer.length > 1_000_000) {
        throw new ProductApiError(
          "实时回答数据异常，请重新尝试。",
          "QUERY_STREAM_INVALID",
          true,
        );
      }
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const parsed = parseSseFrame(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (parsed) {
          const stage = stageEventFromEnvelope(parsed.event, parsed.payload);
          if (stage) {
            onStage(stage);
          } else if (parsed.event === "completed") {
            const data = parsed.payload.data || {};
            if (data.user_message && data.assistant_message) {
              const result = data as MessageResult;
              return {
                user_message: normalizeMessage(result.user_message),
                assistant_message: normalizeMessage(result.assistant_message),
              };
            }
            throw new ProductApiError(
              "实时回答缺少完整结果，请重新尝试。",
              "QUERY_STREAM_INVALID",
              true,
            );
          } else if (parsed.event === "error") {
            throw terminalError(parsed.payload);
          } else if (parsed.event === "cancelled") {
            throw new ProductApiError(
              "本次回答已停止。",
              "QUERY_CANCELLED",
              true,
              parsed.payload.request_id || null,
            );
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
  throw new ProductApiError(
    "问答服务连接提前结束，请重新尝试。",
    "QUERY_STREAM_INCOMPLETE",
    true,
  );
}

export function getKnowledgeHealthTrend(
  id: string,
  limit = 30,
): Promise<{ items: HealthTrendItem[] }> {
  return requestJson(`/api/knowledge-bases/${id}/health/trend?limit=${limit}`);
}

export function runKnowledgeHealthActions(
  id: string,
  actions: string[],
): Promise<HealthActionsRunResult> {
  return requestJson(`/api/knowledge-bases/${id}/health/actions/run`, {
    method: "POST",
    body: JSON.stringify({ actions }),
  });
}

export type DashboardKpi = {
  value: number | null;
  delta: number | null;
  delta_pct: number | null;
};

export type MetricStat = {
  value: number | null;
  sample_count: number;
  numerator: number | null;
  denominator: number | null;
};

export type DashboardOverview = {
  updated_at: string;
  kpis: {
    users: DashboardKpi;
    knowledge_bases: DashboardKpi;
    documents: DashboardKpi & { ready?: number | null; failed?: number | null };
    answers_today: DashboardKpi;
    conversations: DashboardKpi;
    messages: DashboardKpi;
    feedback: DashboardKpi;
    connector_syncs: DashboardKpi;
    audit_logs: DashboardKpi;
  };
  health_distribution: {
    healthy: number;
    warning: number;
    critical: number;
    partial: number;
    unknown: number;
  };
  quality: {
    success_rate: MetricStat;
    negative_feedback_rate: MetricStat;
    feedback_coverage_rate: MetricStat;
    uncited_answer_count: MetricStat;
    average_latency_ms: MetricStat;
    cancelled_count: number;
  };
};

export type DashboardTrends = {
  metric: string;
  window: string;
  granularity: string;
  series: { name: string; data: { ts: string; value: number }[] }[];
};

export function getDashboardOverview(): Promise<DashboardOverview> {
  return requestJson("/api/admin/dashboard/overview");
}

export function getDashboardTrends(days: number): Promise<DashboardTrends> {
  return requestJson("/api/admin/dashboard/trends?days=" + days);
}

export async function listAuditLogs(input: {
  page?: number;
  page_size?: number;
  biz_type?: string;
  biz_id?: string;
  operation_type?: string;
  operator_id?: string;
  operator_name?: string;
  success?: boolean;
  begin_time?: string;
  end_time?: string;
} = {}): Promise<AuditLogPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  return requestJson(`/api/admin/audit-logs${query ? `?${query}` : ""}`);
}
