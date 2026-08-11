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
  created_at: string;
  updated_at: string;
};

export type ProductUser = {
  id: string;
  username: string;
  display_name: string;
  role: "admin" | "member";
  is_active: boolean;
  created_at: string;
};

export type AuthStatus = {
  setup_required: boolean;
  authenticated: boolean;
  user: ProductUser | null;
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
  citations: Citation[];
  claims: AnswerClaim[];
};

export type QueryStageEvent =
  | {
      version: 1;
      request_id: string;
      sequence: number;
      event: "started";
      data: { stage: "query_started" };
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
  knowledge_base_id: string;
  knowledge_base_name: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type ConversationDetail = {
  id: string;
  title: string;
  knowledge_base: KnowledgeBase;
  messages: Message[];
  created_at: string;
  updated_at: string;
};

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

async function requestJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...options,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
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

export async function listUsers(): Promise<ProductUser[]> {
  const response = await requestJson<{ items: ProductUser[] }>("/api/admin/users");
  return response.items;
}

export async function createUser(input: {
  username: string;
  password: string;
  display_name: string;
  role: "admin" | "member";
}): Promise<ProductUser> {
  const response = await requestJson<{ user: ProductUser }>("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.user;
}

export async function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  const response = await requestJson<{ items: KnowledgeBase[] }>(
    "/api/knowledge-bases",
  );
  return response.items;
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
  knowledgeBaseId: string,
): Promise<ConversationSummary> {
  return requestJson("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ knowledge_base_id: knowledgeBaseId }),
  });
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return requestJson(`/api/conversations/${id}`);
}

export function deleteConversation(id: string): Promise<void> {
  return requestJson(`/api/conversations/${id}`, { method: "DELETE" });
}

export function sendMessage(
    conversationId: string,
    content: string,
    useWebSearch = false,
): Promise<{ user_message: Message; assistant_message: Message }> {
  return requestJson(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content, use_web_search: useWebSearch }),
  });
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
              return data as MessageResult;
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
