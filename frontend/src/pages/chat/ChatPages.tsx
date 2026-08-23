import {
  ArrowPathIcon,
  ArrowTopRightOnSquareIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ChevronRightIcon,
  CircleStackIcon,
  ClipboardDocumentIcon,
  CommandLineIcon,
  DocumentTextIcon,
  ExclamationTriangleIcon,
  GlobeAltIcon,
  HandThumbDownIcon,
  HandThumbUpIcon,
  ListBulletIcon,
  MagnifyingGlassIcon,
  PaperAirplaneIcon,
  ShieldCheckIcon,
  TrashIcon,
  UserCircleIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Fragment,
  type ReactNode,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import { useNavigate, useParams } from "react-router-dom";

import { DangerConfirmDialog } from "../../DangerConfirmDialog";
import { QueryFailureState } from "../../QueryFailureState";
import { useMediaQuery } from "../../useMediaQuery";
import { useModalFocus } from "../../useModalFocus";
import {
  CITATION_STATE_EVENT,
  TOGGLE_CITATIONS_EVENT,
  type CitationDrawerState,
} from "../../components/evidence/citationEvents";
import {
  type Citation,
  type CitationSpan,
  type KnowledgeBase,
  type Message,
  type QueryScope,
  type QueryStageEvent,
  cancelMessageFeedback,
  createConversation,
  deleteConversation,
  getConversation,
  getQueryScope,
  streamMessage,
  submitMessageFeedback,
} from "../../product-api";
import "../../workspace.css";

import { ErrorState, LoadingState } from "../../components/states";
import {
  QUERY_SCOPE_COPY,
  getQueryScopeChatState,
  isAskableQueryScope,
} from "./newChatState";


import {
  cancelledQueryError,
  displayAnswerContent,
  displayHealthValue,
  QueryProgress,
  SUGGESTED_QUESTIONS,
} from "../user/userPageCommon";

function QuestionComposer({
  knowledgeBase,
  scope,
  value,
  onChange,
  onSubmit,
  pending,
  useWebSearch,
  onUseWebSearchChange,
  compact = false,
  homepage = false,
  askable,
}: {
  knowledgeBase?: KnowledgeBase;
  scope?: QueryScope;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  pending: boolean;
  useWebSearch: boolean;
  onUseWebSearchChange: (enabled: boolean) => void;
  compact?: boolean;
  homepage?: boolean;
  askable?: boolean;
}) {
  const hasReadyDocuments = Boolean(knowledgeBase?.ready_document_count);
  const indexUnavailable =
    hasReadyDocuments && knowledgeBase?.index_status !== "verified";
  const disabled = askable === undefined
    ? scope
      ? !scope.askable
      : !knowledgeBase || !hasReadyDocuments || indexUnavailable
    : !askable;
  const scopeUnavailable = scope && !scope.askable;
  return (
    <form
      className={`question-composer ${compact ? "question-composer--compact" : ""} ${homepage ? "question-composer--homepage" : ""}`}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <textarea
        aria-label="输入问题"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (!disabled && value.trim() && !pending) onSubmit();
          }
        }}
        placeholder={
          scopeUnavailable
            ? scope?.primary_action === "refresh"
              ? "资料正在准备中，刷新状态后即可提问"
              : "暂无可用知识资料"
                : indexUnavailable
                  ? "当前知识索引暂不可用，请联系管理员"
                  : disabled
                    ? "当前没有可用知识资料"
                    : "输入您的问题，Shift + Enter 换行..."
        }
        disabled={disabled || pending}
      />
      <div className="composer-toolbar">
        <span className="composer-scope-note">
          <CircleStackIcon aria-hidden="true" />
          自动检索可访问知识
        </span>
        <span className="composer-divider" />
        <button
          className={`composer-web-search ${useWebSearch ? "active" : ""}`}
          type="button"
          aria-pressed={useWebSearch}
          disabled={disabled || pending}
          title="为当前问题补充联网搜索"
          onClick={() => onUseWebSearchChange(!useWebSearch)}
        >
          <GlobeAltIcon aria-hidden="true" />
          联网搜索
        </button>
        <button
          className="send-button"
          type="submit"
          aria-label="发送问题"
          disabled={disabled || !value.trim() || pending}
        >
          {pending ? (
            <ArrowPathIcon className="spin" aria-hidden="true" />
          ) : (
            <PaperAirplaneIcon aria-hidden="true" />
          )}
        </button>
      </div>
    </form>
  );
}

function renderNewChatTitle(title: string) {
  if (title !== "从企业知识中找到可靠答案") return title;
  return (
    <>
      从企业知识中找到<span className="hero-title-accent">可靠答案</span>
    </>
  );
}

function SuggestedQuestionIcon({ variant }: { variant: string }) {
  const Icon =
    variant === "insights"
      ? MagnifyingGlassIcon
      : variant === "actions"
        ? ListBulletIcon
        : DocumentTextIcon;
  return (
    <span className={`suggested-question-icon suggested-question-icon--${variant}`}>
      <Icon aria-hidden="true" />
    </span>
  );
}

export function NewChatPage() {
  const navigate = useNavigate();
  const queryCache = useQueryClient();
  const [question, setQuestion] = useState("");
  const [error, setError] = useState("");
  const [streamStages, setStreamStages] = useState<QueryStageEvent[]>([]);
  const [useWebSearch, setUseWebSearch] = useState(false);
  const streamControllerRef = useRef<AbortController | null>(null);
  const queryScope = useQuery({
    queryKey: ["query-scope"],
    queryFn: getQueryScope,
    staleTime: 15_000,
  });
  const availabilityState = getQueryScopeChatState(queryScope.data);
  const stateCopy = QUERY_SCOPE_COPY[availabilityState];
  const canAsk = isAskableQueryScope(queryScope.data);
  const mutation = useMutation({
    mutationFn: async () => {
      if (!queryScope.data?.askable) throw new Error(stateCopy.description);
      const conversation = await createConversation();
      const controller = new AbortController();
      streamControllerRef.current = controller;
      setStreamStages([]);
      try {
        await streamMessage(
          conversation.id,
          question,
          (stage) => setStreamStages((current) => [...current, stage].slice(-8)),
          controller.signal,
          useWebSearch,
        );
      } catch (requestError) {
        if (controller.signal.aborted) throw cancelledQueryError();
        throw requestError;
      }
      return conversation;
    },
    onSuccess: async (conversation) => {
      await queryCache.invalidateQueries({ queryKey: ["conversations"] });
      navigate(`/chat/${conversation.id}`);
    },
    onError: (requestError) => setError(requestError.message),
    onSettled: async () => {
      streamControllerRef.current = null;
      await queryCache.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  useEffect(
    () => () => {
      streamControllerRef.current?.abort();
    },
    [],
  );

  if (queryScope.isLoading) return <LoadingState label="正在确认可访问资料…" />;
  if (queryScope.error) return <ErrorState message={queryScope.error.message} />;

  return (
    <div className="new-chat-page">
      <div className="new-chat-content">
        <h1>{renderNewChatTitle(stateCopy.title)}</h1>
        <p>{stateCopy.description}</p>

        <QuestionComposer
          homepage
          scope={queryScope.data}
          value={question}
          onChange={setQuestion}
          onSubmit={() => {
            setError("");
            mutation.mutate();
          }}
          pending={mutation.isPending}
          useWebSearch={useWebSearch}
          onUseWebSearchChange={setUseWebSearch}
          askable={canAsk}
        />
        <div className="composer-hints" aria-label="输入快捷键">
          <CommandLineIcon aria-hidden="true" />
          <span>Enter 发送</span>
          <span aria-hidden="true">·</span>
          <span>Shift + Enter 换行</span>
        </div>
        {mutation.isPending ? (
          <QueryProgress
            stages={streamStages}
            onStop={() => streamControllerRef.current?.abort()}
          />
        ) : null}
        {error ? <ErrorState message={error} /> : null}

        {!canAsk && queryScope.data?.primary_action === "refresh" ? (
          <div className="query-scope-recovery">
            <span>{stateCopy.description}</span>
            <button className="secondary-button" type="button" onClick={() => void queryScope.refetch()}>
              <ArrowPathIcon aria-hidden="true" />
              {stateCopy.actionLabel}
            </button>
          </div>
        ) : (
          <div className="suggested-questions">
            <div className="suggested-questions-heading">
              <span aria-hidden="true" />
              <strong>你可以这样问</strong>
              <span aria-hidden="true" />
            </div>
            <div className="suggested-questions-grid">
              {SUGGESTED_QUESTIONS.map((suggestion) => (
                <button
                  className="suggested-question-card"
                  type="button"
                  key={suggestion.prompt}
                  onClick={() => setQuestion(suggestion.prompt)}
                >
                  <SuggestedQuestionIcon variant={suggestion.icon} />
                  <span className="suggested-question-title">
                    <strong>{suggestion.title}</strong>
                    <ChevronRightIcon aria-hidden="true" />
                  </span>
                  <span className="suggested-question-description">{suggestion.description}</span>
                  <span className="suggested-question-prompt">
                    <strong>推荐问法：</strong>
                    {suggestion.prompt}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function CitationDrawer({
  citations,
  selectedId,
  selectedSpan,
  focusSelected,
  modal,
  onSelect,
  onClose,
  onOpenSource,
}: {
  citations: Citation[];
  selectedId: string | null;
  selectedSpan: CitationSpan | null;
  focusSelected: boolean;
  modal: boolean;
  onSelect: (citation: Citation) => void;
  onClose: () => void;
  onOpenSource: (citation: Citation) => void;
}) {
  const selectedButtonRef = useRef<HTMLButtonElement | null>(null);
  const headingId = useId();
  const drawerRef = useModalFocus({ open: modal, onDismiss: onClose });
  const [showAll, setShowAll] = useState(false);
  const visibleCitations = showAll
    ? citations
    : citations.slice(0, DEFAULT_CITATION_LIMIT);

  useEffect(() => {
    if (
      selectedId &&
      !citations
        .slice(0, DEFAULT_CITATION_LIMIT)
        .some((citation) => citation.id === selectedId)
    ) {
      setShowAll(true);
    }
  }, [citations, selectedId]);

  useEffect(() => {
    if (focusSelected) selectedButtonRef.current?.focus();
  }, [focusSelected, selectedId]);

  useEffect(() => {
    if (!modal) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [modal]);

  return (
    <aside
      ref={drawerRef}
      id="citation-drawer"
      className="citation-drawer"
      role={modal ? "dialog" : undefined}
      tabIndex={modal ? -1 : undefined}
      aria-modal={modal ? "true" : undefined}
      aria-labelledby={headingId}
    >
      <div className="citation-heading">
        <div>
          <h2 id={headingId}>引用来源</h2>
          <span aria-label={`${citations.length} 个来源`}>{citations.length}</span>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="关闭引用来源">
          <XMarkIcon aria-hidden="true" />
        </button>
      </div>
      <p className="citation-intro">
        回答中的引用来自以下文档或网页。默认展示前 {DEFAULT_CITATION_LIMIT} 条，点击引用可核对片段，原文在预览层打开。
      </p>
      <div className="citation-list">
        {visibleCitations.map((citation) => (
          <article
            className={`citation-card ${
              citation.id === selectedId ? "selected" : ""
            }`}
            key={citation.id}
          >
            <button
              className="citation-select"
              type="button"
              aria-label={`选择引用 ${citation.index}：${citation.display_name}${
                citation.page_number ? `，第 ${citation.page_number} 页` : ""
              }`}
              ref={citation.id === selectedId ? selectedButtonRef : null}
              onClick={() => onSelect(citation)}
            >
              <span className="citation-index">[{citation.index}]</span>
              <div className="citation-source">
                <strong>
                  {citation.source_type === "web" ? (
                    <GlobeAltIcon aria-hidden="true" />
                  ) : (
                    <DocumentTextIcon aria-hidden="true" />
                  )}
                  {citation.display_name}
                </strong>
                <div className="citation-location">
                  {citation.page_number ? (
                    <span>第 {citation.page_number} 页</span>
                  ) : null}
                  {citation.section_title ? (
                    <span>{citation.section_title}</span>
                  ) : null}
                  {citation.char_start != null &&
                  citation.char_end != null ? (
                    <span>
                      字符 {citation.char_start}–{citation.char_end}
                    </span>
                  ) : null}
                  {citation.extraction_method === "ocr" ? (
                    <span>OCR 识别</span>
                  ) : null}
                  {citation.source_type === "web" && citation.source_domain ? (
                    <span>{citation.source_domain}</span>
                  ) : null}
                  {citation.source_type === "web" && citation.trusted ? (
                    <span>域名白名单</span>
                  ) : null}
                </div>
                <p>
                  {citation.text &&
                  citation.id === selectedId &&
                  selectedSpan?.citation_index === citation.index &&
                  selectedSpan.start != null &&
                  selectedSpan.end != null &&
                  selectedSpan.start >= 0 &&
                  selectedSpan.end > selectedSpan.start &&
                  selectedSpan.end <= citation.text.length ? (
                    <>
                      {citation.text.slice(0, selectedSpan.start)}
                      <mark
                        className={`citation-span citation-span--${selectedSpan.match_type}`}
                        title={
                          selectedSpan.match_type === "normalized_exact"
                            ? "声明文字在证据中的精确位置"
                            : "与声明最相关的证据句，仅用于定位"
                        }
                      >
                        {citation.text.slice(selectedSpan.start, selectedSpan.end)}
                      </mark>
                      {citation.text.slice(selectedSpan.end)}
                    </>
                  ) : (
                    citation.text || "该来源暂时无法预览。"
                  )}
                </p>
              </div>
            </button>
            {citation.source_type === "web" &&
            citation.source_url &&
            /^https?:\/\//.test(citation.source_url) ? (
              <a
                className="citation-open-source"
                href={citation.source_url}
                target="_blank"
                rel="noreferrer"
              >
                打开网页来源
                <ArrowTopRightOnSquareIcon aria-hidden="true" />
              </a>
            ) : citation.document_id ? (
              <button
                className="citation-open-source"
                type="button"
                onClick={() => onOpenSource(citation)}
                aria-label={`查看 ${citation.display_name}${
                  citation.page_number ? ` 第 ${citation.page_number} 页` : ""
                }的原文`}
              >
                查看原文
                {citation.page_number ? `第 ${citation.page_number} 页` : ""}
                <ArrowTopRightOnSquareIcon aria-hidden="true" />
              </button>
            ) : (
              <span className="citation-source-unavailable">
                {citation.source_type === "web" ? "网页地址不可用" : "原文已从知识库删除"}
              </span>
            )}
          </article>
        ))}
      </div>
      {citations.length > DEFAULT_CITATION_LIMIT ? (
        <button
          className="citation-more-button"
          type="button"
          onClick={() => setShowAll((current) => !current)}
        >
          {showAll
            ? "收起其余引用"
            : `查看更多（还有 ${citations.length - DEFAULT_CITATION_LIMIT} 条）`}
          {showAll ? <ChevronUpIcon aria-hidden="true" /> : <ChevronDownIcon aria-hidden="true" />}
        </button>
      ) : null}
    </aside>
  );
}

const DEFAULT_CITATION_LIMIT = 5;

type ImagePreviewTarget = {
  kind: "image";
  src: string;
  alt: string;
};

type CitationPreviewTarget = {
  kind: "citation";
  citation: Citation;
};

type SourcePreviewTarget = ImagePreviewTarget | CitationPreviewTarget;

function citationDocumentHref(citation: Citation) {
  if (!citation.document_id) return null;
  return `/api/documents/${encodeURIComponent(citation.document_id)}/content${
    citation.page_number != null ? `#page=${citation.page_number}` : ""
  }`;
}

function citationAriaLabel(citation: Citation) {
  return `查看引用 ${citation.index}：${citation.display_name}${
    citation.page_number ? `，第 ${citation.page_number} 页` : ""
  }`;
}

function citationPillLabel(citation: Citation) {
  return `[${citation.index}] ${citation.display_name}${
    citation.page_number ? ` · 第 ${citation.page_number} 页` : ""
  }`;
}

function flattenMarkdownText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenMarkdownText).join("");
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props?: { children?: ReactNode } }).props;
    return props?.children ? flattenMarkdownText(props.children) : "";
  }
  return "";
}

function normalizeForMatch(value: string) {
  return value
    .replace(/\s+/g, "")
    .replace(/[。！？!?.,，、:：；;]/g, "")
    .toLowerCase();
}

function CitationTag({
  citation,
  span,
  onCitation,
}: {
  citation: Citation;
  span?: CitationSpan;
  onCitation: (
    citation: Citation,
    trigger: HTMLButtonElement,
    span?: CitationSpan,
  ) => void;
}) {
  return (
    <button
      className="citation-tag"
      type="button"
      data-citation-trigger={citation.id}
      aria-label={citationAriaLabel(citation)}
      onClick={(event) => onCitation(citation, event.currentTarget, span)}
    >
      {citationPillLabel(citation)}
    </button>
  );
}

function MarkdownImagePreview({
  src,
  alt,
  onOpen,
}: {
  src?: string;
  alt?: string;
  onOpen: (image: ImagePreviewTarget) => void;
}) {
  if (!src) return null;
  const accessibleAlt = alt?.trim() || "回答中的图片";

  return (
    <span className="markdown-image-preview">
      <img src={src} alt={accessibleAlt} loading="lazy" decoding="async" />
      <button
        className="markdown-image-preview-action"
        type="button"
        onClick={() => onOpen({ kind: "image", src, alt: accessibleAlt })}
      >
        查看图片
      </button>
    </span>
  );
}

function AnswerContent({
  content,
  message,
  onCitation,
  onImageOpen,
}: {
  content: string;
  message: Message;
  onCitation: (
    citation: Citation,
    trigger: HTMLButtonElement,
    span?: CitationSpan,
  ) => void;
  onImageOpen: (image: ImagePreviewTarget) => void;
}) {
  const claims = message.claims || [];

  return (
    <div className="markdown-answer">
      <ReactMarkdown
        components={{
          p: ({ children }) => {
            const paragraphText = normalizeForMatch(flattenMarkdownText(children));
            const matchingClaims = claims.filter((claim) => {
              const claimText = normalizeForMatch(displayAnswerContent(claim.text));
              return (
                Boolean(claimText) &&
                Boolean(paragraphText) &&
                (paragraphText.includes(claimText) || claimText.includes(paragraphText))
              );
            });
            const matchingCitations = matchingClaims.flatMap((claim) =>
              claim.citation_indices
                .map((index) => ({
                  citation: message.citations.find((item) => item.index === index),
                  span: claim.citation_spans?.find(
                    (item) => item.citation_index === index && item.match_type !== "not_found",
                  ),
                }))
                .filter(
                  (
                    item,
                  ): item is { citation: Citation; span: CitationSpan | undefined } =>
                    Boolean(item.citation),
                ),
            ).filter((item, index, items) =>
              items.findIndex(({ citation }) => citation.id === item.citation.id) === index,
            );

            return (
              <p>
                {children}
                {matchingCitations.length ? (
                  <span className="inline-answer-citations" aria-label="相关引用">
                    {matchingCitations.map(({ citation, span }) => (
                      <CitationTag
                        key={citation.id}
                        citation={citation}
                        span={span}
                        onCitation={onCitation}
                      />
                    ))}
                  </span>
                ) : null}
              </p>
            );
          },
          img: ({ src, alt }) => (
            <MarkdownImagePreview src={src} alt={alt} onOpen={onImageOpen} />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function SourceReference({
  citation,
  onOpenSource,
}: {
  citation: Citation;
  onOpenSource: (citation: Citation) => void;
}) {
  const href =
    citation.source_type === "web" &&
    citation.source_url &&
    /^https?:\/\//.test(citation.source_url)
      ? citation.source_url
      : citationDocumentHref(citation);

  if (!href) {
    return (
      <span className="evidence-source-link evidence-source-link--unavailable">
        原文暂不可用
      </span>
    );
  }

  if (citation.source_type === "knowledge_base") {
    return (
      <button
        className="evidence-source-link"
        type="button"
        onClick={() => onOpenSource(citation)}
      >
        查看原文
        <ArrowTopRightOnSquareIcon aria-hidden="true" />
      </button>
    );
  }

  return (
    <a
      className="evidence-source-link"
      href={href}
      target="_blank"
      rel="noreferrer"
    >
      查看原文
      <ArrowTopRightOnSquareIcon aria-hidden="true" />
    </a>
  );
}

function renderEvidenceText(citation: Citation, span?: CitationSpan) {
  if (
    span?.start != null &&
    span.end != null &&
    span.start >= 0 &&
    span.end > span.start &&
    span.end <= citation.text.length
  ) {
    return (
      <>
        {citation.text.slice(0, span.start)}
        <mark
          className={`evidence-highlight evidence-highlight--${span.match_type}`}
          title={
            span.match_type === "normalized_exact"
              ? "回答文字在证据中的对应位置"
              : "与回答最相关的证据句"
          }
        >
          {citation.text.slice(span.start, span.end)}
        </mark>
        {citation.text.slice(span.end)}
      </>
    );
  }
  return citation.text || "该来源暂时无法预览。";
}

function EvidenceCard({
  citation,
  supportingClaim,
  span,
  expanded,
  onToggle,
  onOpenSource,
}: {
  citation: Citation;
  supportingClaim?: string;
  span?: CitationSpan;
  expanded: boolean;
  onToggle: () => void;
  onOpenSource: (citation: Citation) => void;
}) {
  const hasLongText = citation.text.length > 280;
  return (
    <article className="evidence-card">
      <header className="evidence-card-header">
        <span className={`evidence-file-icon evidence-file-icon--${citation.source_type}`}>
          {citation.source_type === "web" ? (
            <GlobeAltIcon aria-hidden="true" />
          ) : (
            <DocumentTextIcon aria-hidden="true" />
          )}
        </span>
        <div className="evidence-file-meta">
          <strong>{citation.display_name}</strong>
          <span>
            {citation.page_number ? `第 ${citation.page_number} 页` : "来源片段"}
            {citation.section_title ? ` · ${citation.section_title}` : ""}
          </span>
        </div>
        <span className={`evidence-support-badge ${citation.supported ? "is-supported" : "is-review"}`}>
          {citation.supported ? "已用于回答" : "待核验"}
        </span>
        <SourceReference citation={citation} onOpenSource={onOpenSource} />
      </header>
      <div className="evidence-card-body">
        {supportingClaim ? (
          <p className="evidence-support-copy">
            <strong>支持：</strong>
            {supportingClaim}
          </p>
        ) : null}
        <div className={`evidence-quote ${expanded ? "is-expanded" : ""}`}>
          <span className="evidence-quote-label">原文片段</span>
          <p>“{renderEvidenceText(citation, span)}”</p>
        </div>
        {hasLongText ? (
          <button
            className="evidence-expand-button"
            type="button"
            aria-expanded={expanded}
            onClick={onToggle}
          >
            {expanded ? "收起片段" : "展开完整片段"}
            {expanded ? <ChevronUpIcon aria-hidden="true" /> : <ChevronDownIcon aria-hidden="true" />}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function AssistantMessage({
  conversationId,
  message,
  isAdmin,
  onCitation,
  onOpenSource,
  onImageOpen,
  onRegenerate,
  queryPending,
  regenerating,
  queryDisabled,
}: {
  conversationId: string;
  message: Message;
  isAdmin: boolean;
  onCitation: (
    citation: Citation,
    trigger: HTMLButtonElement,
    span?: CitationSpan,
  ) => void;
  onOpenSource: (citation: Citation) => void;
  onImageOpen: (image: ImagePreviewTarget) => void;
  onRegenerate: () => void;
  queryPending: boolean;
  regenerating: boolean;
  queryDisabled: boolean;
}) {
  const queryCache = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [showAllCitations, setShowAllCitations] = useState(false);
  const [expandedEvidence, setExpandedEvidence] = useState<Record<string, boolean>>({});
  const [feedback, setFeedback] = useState<"helpful" | "unhelpful" | null>(
    message.feedback && !message.feedback.cancelled
      ? message.feedback.vote === 1
        ? "helpful"
        : "unhelpful"
      : null,
  );
  const [feedbackPending, setFeedbackPending] = useState(false);

  // Keep the selected vote in sync with the server state: message.feedback is
  // refreshed by the conversation query after every submit/cancel, and useState
  // only reads its initial value once.
  useEffect(() => {
    setFeedback(
      message.feedback && !message.feedback.cancelled
        ? message.feedback.vote === 1
          ? "helpful"
          : "unhelpful"
        : null,
    );
  }, [message.id, message.feedback?.vote, message.feedback?.cancelled]);

  const applyFeedback = async (vote: "helpful" | "unhelpful") => {
    if (feedbackPending) return;
    setFeedbackPending(true);
    try {
      if (feedback === vote) {
        await cancelMessageFeedback(conversationId, message.id);
        setFeedback(null);
      } else {
        await submitMessageFeedback(conversationId, message.id, {
          vote: vote === "helpful" ? 1 : -1,
        });
        setFeedback(vote);
      }
      await queryCache.invalidateQueries({
        queryKey: ["conversation", conversationId],
      });
    } catch {
      // Silent failure: keep the previous selection instead of blocking.
    } finally {
      setFeedbackPending(false);
    }
  };
  const displayedContent = displayAnswerContent(message.content);
  const visibleCitations = showAllCitations
    ? message.citations
    : message.citations.slice(0, DEFAULT_CITATION_LIMIT);
  const hiddenCitationCount = Math.max(
    message.citations.length - DEFAULT_CITATION_LIMIT,
    0,
  );
  const trustInputClaims = message.trust_details?.input.claims || [];
  const contradictedClaimCount = trustInputClaims.filter(
    (claim) => claim.entailment_status === "contradicted",
  ).length;
  const unknownEntailmentCount = trustInputClaims.filter(
    (claim) => claim.entailment_status === "unknown",
  ).length;
  const rejectedRiskClaimCount = trustInputClaims.filter(
    (claim) => claim.risk_status === "rejected",
  ).length;
  const highRiskQuantitative = Boolean(
    message.risk_level === "high" &&
      message.risk_factors?.includes("QUANTITATIVE_DECISION"),
  );
  const provenance = message.trust_details?.provenance;
  const provenanceDigest = provenance?.digest.replace("sha256:", "") || "";
  const evidenceProvenance = provenance?.evidence;
  const temporalContext = message.trust_details?.temporal_context;
  const freshness = message.trust_details?.freshness;
  const hasEvidence = Boolean(
    message.citations.length || message.claims.length || evidenceProvenance,
  );
  const evidenceDocuments = new Set(message.citations.map((citation) => citation.display_name));
  const supportedEvidenceCount = message.citations.filter((citation) => citation.supported).length;
  const evidenceStatus =
    message.answer_state === "conflicting_evidence"
      ? { label: "存在来源冲突", tone: "conflict" }
      : message.answer_state === "partially_grounded"
        ? { label: "部分完成核验", tone: "partial" }
        : message.answer_state === "insufficient_evidence"
          ? { label: "依据不足", tone: "warning" }
          : message.answer_state === "failed"
            ? { label: "未完成检索", tone: "warning" }
            : { label: "已完成核验", tone: "success" };
  const supportingClaimByCitation = new Map<number, { text: string; span?: CitationSpan }>();
  message.claims.forEach((claim) => {
    if (claim.support_status !== "supported") return;
    claim.citation_indices.forEach((index) => {
      if (!supportingClaimByCitation.has(index)) {
        supportingClaimByCitation.set(index, {
          text: claim.text,
          span: claim.citation_spans?.find(
            (span) => span.citation_index === index && span.match_type !== "not_found",
          ),
        });
      }
    });
  });
  const formatConsistencyValue = (kind: string, value: string) => {
    const parts = value.split("|");
    if (kind === "range" && parts.length === 3) {
      const operator =
        ({ lte: "不超过", gte: "不少于", lt: "小于", gt: "大于" } as const)[
          parts[0] as "lte" | "gte" | "lt" | "gt"
        ] || parts[0];
      return `${operator} ${parts[1]}${parts[2] ? ` ${parts[2]}` : ""}`;
    }
    if (kind === "quantity" && parts.length === 2) {
      return `${parts[0]}${parts[1] ? ` ${parts[1]}` : ""}`;
    }
    if (kind === "condition") {
      const relation = value.match(/^(all|any)\((.*)\)$/);
      if (relation) {
        return `${relation[1] === "all" ? "需同时满足" : "满足任一"}：${relation[2]
          .split(",")
          .join("、")}`;
      }
      const negated = value.match(/^negated\((.*)\)$/);
      if (negated) {
        return `条件被否定：${negated[1]}`;
      }
    }
    if (kind === "relative_time") {
      const [period, rawValue] = value.split(":", 2);
      if (period === "date") return rawValue;
      if (period === "week") return `${rawValue} 周`;
      if (period === "month") return `${rawValue} 月`;
      if (period === "quarter") return `${rawValue.replace("-Q", " 年第 ")} 季度`;
      if (period === "year") return `${rawValue} 年`;
      const relativeLabels: Record<string, string> = {
        "day:-2": "前天",
        "day:-1": "昨天",
        "day:0": "今天",
        "day:1": "明天",
        "day:2": "后天",
        "week:-1": "上周",
        "week:0": "本周",
        "week:1": "下周",
        "month:-1": "上月",
        "month:0": "本月",
        "month:1": "下月",
        "quarter:-1": "上季度",
        "quarter:0": "本季度",
        "quarter:1": "下季度",
        "year:-1": "去年",
        "year:0": "今年",
        "year:1": "明年",
      };
      return relativeLabels[value] || value;
    }
    if (kind === "freshness") {
      const labels: Record<string, string> = {
        current: "当前有效版本",
        latest_effective: "最新生效版本",
        latest_published: "最新发布版本",
        recent: "近期",
        effective_at: "生效日期",
        published_at: "发布日期",
        request_clock: "请求时间基准",
        recency_window: "明确的近期时间窗口",
        active_version: "当前有效版本",
        eligible_publication: "可用发布日期",
        "effective_at<=reference_date": "生效日期不晚于查询日期",
        "reference_date<superseded_at": "查询日期早于失效日期",
        "published_at<=reference_date": "发布日期不晚于查询日期",
      };
      return labels[value] || value;
    }
    return value;
  };
  return (
    <article className="assistant-message">
      <div className="assistant-avatar">
        <img src="/deepsearcher-badge.png" alt="" />
      </div>
      <div className="assistant-body">
        {message.answer_state === "insufficient_evidence" ? (
          <div className="insufficient-evidence">
            当前资料中没有找到足够依据。你可以换一种问法，或上传更相关的资料。
          </div>
        ) : null}
        {message.answer_state === "partially_grounded" ? (
          <div className="grounding-notice grounding-notice--partial">
            回答中只有部分声明找到了可核对依据，未支持内容已单独标记。
          </div>
        ) : null}
        {message.answer_state === "conflicting_evidence" ? (
          <div className="grounding-notice grounding-notice--conflict">
            检索到的来源存在冲突，请结合对应引用判断。
          </div>
        ) : null}
        {message.policy_action === "downgrade" ? (
          <div className="grounding-notice grounding-notice--partial">
            可信回答策略已移除未获得当前证据支持的声明，仅保留可核对内容。
          </div>
        ) : null}
        {message.risk_level === "high" ? (
          <div className="grounding-notice grounding-notice--high-risk">
            本问题已按高风险策略核验：必须有明确语义结论
            {highRiskQuantitative ? "，且额度、期限或比例需要至少两份独立来源" : ""}。
          </div>
        ) : null}
        {freshness?.required ? (
          <div className="grounding-notice grounding-notice--freshness">
            本问题要求核验
            {freshness.mode === "current"
              ? "当前有效版本"
              : freshness.mode === "latest_published"
                ? "最新发布版本"
                : freshness.mode === "latest_effective"
                  ? "最新生效版本"
                  : "近期资料"}
            ；系统只使用文档声明的业务日期，不使用上传时间推断。
          </div>
        ) : null}
        {rejectedRiskClaimCount ? (
          <div className="grounding-notice grounding-notice--risk-rejected">
            {rejectedRiskClaimCount} 条声明未满足高风险证据门槛，已被删除或拒答。
          </div>
        ) : null}
        {contradictedClaimCount ? (
          <div className="grounding-notice grounding-notice--entailment-conflict">
            语义核验发现 {contradictedClaimCount} 条声明与引用证据矛盾，风险内容已由可信策略移除。
          </div>
        ) : null}
        {unknownEntailmentCount ? (
          <div className="grounding-notice grounding-notice--entailment-unknown">
            {unknownEntailmentCount} 条声明未得到高置信语义结论；
            {message.risk_level === "high"
              ? "高风险策略不会保留这些内容。"
              : "当前标准策略保留内容并明确标记。"}
          </div>
        ) : null}
        {message.answer_state === "failed" ? (
          <div className="query-failure-inline">
            系统没有完成本次检索，这不代表知识库中没有相关资料。
          </div>
        ) : null}
        <section className="assistant-answer-card" aria-label="AI 回答">
          <header className="assistant-answer-header">
            <div className="assistant-identity">
              <span className="assistant-identity-mark" aria-hidden="true">
                <img src="/deepsearcher-badge.png" alt="" />
              </span>
              <div>
                <strong>DeepSearcher AI</strong>
                <span>企业知识问答助手</span>
              </div>
            </div>
            {message.answer_state === "failed" ? (
              <span className="answer-status-badge answer-status-badge--warning">
                <ExclamationTriangleIcon aria-hidden="true" />
                检索未完成
              </span>
            ) : (
              <span className="answer-status-badge">
                <CheckCircleIcon aria-hidden="true" />
                已基于企业知识库回答
              </span>
            )}
          </header>
          <AnswerContent
            content={displayedContent}
            message={message}
            onCitation={onCitation}
            onImageOpen={onImageOpen}
          />
        </section>
        {message.citations.length ? (
          <section className="answer-sources" aria-label="检索引用来源">
            <div className="answer-sources-heading">
              <DocumentTextIcon aria-hidden="true" />
              <strong>检索引用来源</strong>
              <span>{message.citations.length}</span>
            </div>
            <div className="answer-source-list">
              {visibleCitations.map((citation) => (
                <button
                  className="answer-source-pill"
                  type="button"
                  key={citation.id}
                  aria-label={`在引用来源中查看 ${citation.index}：${citation.display_name}${
                    citation.page_number ? `，第 ${citation.page_number} 页` : ""
                  }`}
                  onClick={(event) => onCitation(citation, event.currentTarget)}
                >
                  {citation.source_type === "web" ? (
                    <GlobeAltIcon aria-hidden="true" />
                  ) : (
                    <DocumentTextIcon aria-hidden="true" />
                  )}
                  <span>{citationPillLabel(citation)}</span>
                </button>
              ))}
              {hiddenCitationCount ? (
                <button
                  className="answer-source-more"
                  type="button"
                  onClick={() => setShowAllCitations((current) => !current)}
                >
                  {showAllCitations
                    ? "收起其余引用"
                    : `查看更多（还有 ${hiddenCitationCount} 条）`}
                  {showAllCitations ? (
                    <ChevronUpIcon aria-hidden="true" />
                  ) : (
                    <ChevronDownIcon aria-hidden="true" />
                  )}
                </button>
              ) : null}
            </div>
          </section>
        ) : null}
        {hasEvidence ? (
          <>
            <button
              className={`evidence-toggle ${evidenceOpen ? "is-open" : ""}`}
              type="button"
              aria-expanded={evidenceOpen}
              aria-controls={`evidence-panel-${message.id}`}
              onClick={() => setEvidenceOpen((current) => !current)}
            >
              <ShieldCheckIcon aria-hidden="true" />
              <span>{evidenceOpen ? "收起回答依据" : "查看回答依据"}</span>
              {evidenceOpen ? (
                <ChevronUpIcon aria-hidden="true" />
              ) : (
                <ChevronDownIcon aria-hidden="true" />
              )}
            </button>
            <section
              id={`evidence-panel-${message.id}`}
              className={`evidence-panel ${evidenceOpen ? "is-open" : ""}`}
            >
              <div className="evidence-panel-inner">
                <div className="evidence-panel-heading">
                  <div>
                    <span className="evidence-panel-kicker">回答依据与支持材料</span>
                    <strong>Supporting Evidence</strong>
                  </div>
                  <span className="evidence-panel-count">
                    {message.citations.length} 段证据
                  </span>
                </div>
                <div className="evidence-summary">
                  <div className="evidence-summary-status">
                    <span className="evidence-summary-icon" aria-hidden="true">
                      <ShieldCheckIcon />
                    </span>
                    <div>
                      <span>回答依据状态</span>
                      <strong className={`evidence-status-text evidence-status-text--${evidenceStatus.tone}`}>
                        {evidenceStatus.label}
                      </strong>
                      <p>
                        已引用 {evidenceDocuments.size} 份文档，找到 {supportedEvidenceCount} 段可支撑回答的证据。
                      </p>
                    </div>
                  </div>
                  <div className="evidence-summary-metrics" aria-label="证据摘要">
                    <div>
                      <strong>{evidenceDocuments.size}</strong>
                      <span>已引用文档</span>
                    </div>
                    <div>
                      <strong>{supportedEvidenceCount}</strong>
                      <span>支撑证据</span>
                    </div>
                    <div>
                      <strong>{message.claims.length || message.citations.length}</strong>
                      <span>核验项</span>
                    </div>
                  </div>
                </div>
                <div className="evidence-list" aria-label="支持证据片段">
                  {visibleCitations.map((citation) => {
                    const supportingClaim = supportingClaimByCitation.get(citation.index);
                    return (
                      <EvidenceCard
                        key={citation.id}
                        citation={citation}
                        supportingClaim={supportingClaim?.text}
                        span={supportingClaim?.span}
                        expanded={Boolean(expandedEvidence[citation.id])}
                        onToggle={() =>
                          setExpandedEvidence((current) => ({
                            ...current,
                            [citation.id]: !current[citation.id],
                          }))
                        }
                        onOpenSource={onOpenSource}
                      />
                    );
                  })}
                </div>
                {hiddenCitationCount ? (
                  <button
                    className="evidence-more-button"
                    type="button"
                    onClick={() => setShowAllCitations((current) => !current)}
                  >
                    {showAllCitations
                      ? "收起其余证据"
                      : `查看更多证据（还有 ${hiddenCitationCount} 条）`}
                    {showAllCitations ? (
                      <ChevronUpIcon aria-hidden="true" />
                    ) : (
                      <ChevronDownIcon aria-hidden="true" />
                    )}
                  </button>
                ) : null}
        {message.claims?.length ? (
          <details className="claim-grounding">
            <summary>逐条证据核验（{message.claims.length} 条）</summary>
            <div className="claim-grounding-list" aria-label="回答声明与证据">
              {message.claims.map((claim) => {
                const claimCitations = claim.citation_indices
                  .map((index) =>
                    message.citations.find((citation) => citation.index === index),
                  )
                  .filter((citation): citation is Citation => Boolean(citation));
                const attentionChecks = (claim.consistency_checks || []).filter(
                  (check) => check.status !== "consistent",
                );
                const hasFreshnessAttention = attentionChecks.some(
                  (check) => check.kind === "freshness",
                );
                const statusLabel =
                  claim.risk_status === "rejected"
                    ? "高风险门槛未通过"
                    : claim.entailment_status === "contradicted"
                    ? "证据语义矛盾"
                    : claim.entailment_status === "unknown"
                      ? "语义待确认"
                      : claim.consistency_status === "unknown"
                        ? hasFreshnessAttention
                          ? "时效依据待确认"
                          : "时间基准待确认"
                        : claim.consistency_status === "inconsistent"
                          ? "证据内容不一致"
                    : claim.support_status === "supported"
                    ? "已有依据"
                    : claim.support_status === "conflicting"
                      ? "来源冲突"
                      : claim.support_status === "invalid_citation"
                        ? "引用无效"
                        : "缺少依据";
                return (
                  <div
                    key={claim.id}
                    className={`answer-claim answer-claim--${claim.support_status}`}
                  >
                    <div className="markdown-answer">
                      <ReactMarkdown
                        components={{
                          img: ({ src, alt }) => (
                            <MarkdownImagePreview
                              src={src}
                              alt={alt}
                              onOpen={onImageOpen}
                            />
                          ),
                        }}
                      >
                        {claim.text}
                      </ReactMarkdown>
                    </div>
                    <div className="claim-evidence">
                      <span>{statusLabel}</span>
                      {claimCitations.map((citation) => (
                        <button
                          type="button"
                          key={citation.id}
                          aria-label={`查看声明 ${claim.index} 的引用 ${citation.index}：${citation.display_name}${
                            citation.page_number ? `，第 ${citation.page_number} 页` : ""
                          }`}
                          onClick={(event) =>
                            onCitation(
                              citation,
                              event.currentTarget,
                              claim.citation_spans?.find(
                                (span) =>
                                  span.citation_index === citation.index &&
                                  span.match_type !== "not_found",
                              ),
                            )
                          }
                        >
                          [{citation.index}]
                        </button>
                      ))}
                    </div>
                    {attentionChecks.length ? (
                      <div className="claim-consistency" role="note">
                        {attentionChecks.map((check) => (
                          <span key={`${claim.id}-${check.kind}`}>
                            {check.kind === "freshness"
                              ? check.reason_code === "FRESHNESS_EVIDENCE_SUPERSEDED"
                                ? "引用资料在查询日期前已经失效或被替代"
                                : check.reason_code ===
                                    "FRESHNESS_EVIDENCE_NOT_YET_EFFECTIVE"
                                  ? "引用资料在查询日期尚未生效"
                                  : check.reason_code ===
                                      "FRESHNESS_NEWER_EVIDENCE_AVAILABLE"
                                    ? "本次证据快照中存在更新的有效资料"
                                    : check.reason_code ===
                                        "FRESHNESS_RECENCY_WINDOW_UNDEFINED"
                                      ? "“近期”没有明确时间窗口，系统不会自行猜测"
                                      : check.reason_code ===
                                          "FRESHNESS_REFERENCE_MISSING"
                                        ? "本次请求缺少可信日期基准"
                                        : check.reason_code ===
                                            "FRESHNESS_PUBLICATION_IN_FUTURE"
                                          ? "引用资料的发布日期晚于查询日期"
                                          : check.reason_code ===
                                              "FRESHNESS_NO_CURRENT_VERSION"
                                            ? "本次证据中没有当前有效版本"
                                            : "文档缺少完成时效判断所需的业务日期"
                              : check.kind === "quantity"
                              ? check.reason_code === "QUANTITY_ENTITY_MISMATCH"
                                ? "数字虽然出现，但对应对象与证据不一致"
                                : "数字或单位与证据不一致"
                              : check.kind === "date"
                                ? "日期与证据不一致"
                                : check.kind === "relative_time"
                                  ? check.reason_code === "RELATIVE_TIME_REFERENCE_MISSING"
                                    ? "回答包含相对时间，但本次请求缺少可信时间基准"
                                    : check.reason_code ===
                                        "RELATIVE_TIME_EVIDENCE_ANCHOR_MISSING"
                                      ? "证据也使用了相对时间，但缺少可信文档时间锚点"
                                      : check.reason_code ===
                                          "RELATIVE_TIME_ENTITY_MISMATCH"
                                        ? "时间虽然匹配，但对应对象与证据不一致"
                                      : "相对时间换算结果与证据不一致"
                                : check.kind === "version"
                                  ? "版本与证据不一致"
                                  : check.kind === "range"
                                    ? check.reason_code === "RANGE_ENTITY_MISMATCH"
                                      ? "范围数值虽然出现，但对应对象与证据不一致"
                                      : "范围约束与证据不一致"
                                    : check.kind === "condition"
                                      ? check.reason_code === "CONDITION_RELATION_MISMATCH"
                                        ? "前置条件的“同时满足/满足任一”关系与证据不一致"
                                        : check.reason_code === "CONDITION_NEGATED"
                                          ? "回答否定了证据要求的前置条件"
                                          : "权限、例外或前置条件与证据不一致"
                                      : "陈述的肯定/否定方向与证据不一致"}
                            {check.missing_values?.length
                              ? `：${check.missing_values
                                  .map((value) =>
                                    formatConsistencyValue(check.kind, value),
                                  )
                                  .join("、")}`
                              : ""}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {claim.entailment_status === "unknown" ? (
                      <div className="claim-entailment" role="note">
                        语义核验未达到高置信阈值
                        {claim.confidence != null
                          ? `（置信度 ${Math.round(claim.confidence * 100)}%）`
                          : ""}
                      </div>
                    ) : null}
                    {(claim.risk_checks || []).some(
                      (check) => check.status === "failed",
                    ) ? (
                      <div className="claim-risk" role="note">
                        {(claim.risk_checks || [])
                          .filter((check) => check.status === "failed")
                          .map((check) => (
                            <span key={`${claim.id}-risk-${check.kind}`}>
                              {check.kind === "entailment"
                                ? "需要明确的语义核验结论"
                                : check.kind === "source_count"
                                  ? `独立来源不足（${displayHealthValue(check.actual)}/${displayHealthValue(check.required)}）`
                                  : `证据数量不足（${displayHealthValue(check.actual)}/${displayHealthValue(check.required)}）`}
                            </span>
                          ))}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </details>
        ) : null}
        {isAdmin && provenance ? (
          <details className="trust-provenance">
            <summary>本次可信判断谱系</summary>
            <div className="trust-provenance-grid" aria-label="可信判断谱系">
              <div>
                <span>生成模型</span>
                <strong>
                  {provenance.generation_model.provider} / {provenance.generation_model.model}
                </strong>
              </div>
              <div>
                <span>向量模型</span>
                <strong>
                  {provenance.embedding.provider} / {provenance.embedding.model}
                </strong>
              </div>
              <div>
                <span>索引快照</span>
                <strong>
                  {provenance.index.snapshot_status === "complete"
                    ? provenance.index.selection_mode === "dynamic"
                      ? `动态路由，已绑定 ${provenance.index.collection_count} 个实际知识库版本`
                      : `已绑定 ${provenance.index.collection_count} 个知识库版本`
                    : provenance.index.snapshot_status === "dynamic_unbound"
                      ? "动态路由，未绑定固定快照"
                      : "部分版本信息不可用"}
                </strong>
              </div>
              <div>
                <span>运行时</span>
                <strong>
                  {provenance.runtime.runtime_version
                    ? `v${provenance.runtime.runtime_version} · 绑定修订 ${provenance.runtime.binding_revision}`
                    : "本地库调用"}
                </strong>
              </div>
              <div>
                <span>最终证据快照</span>
                <strong>
                  {evidenceProvenance?.snapshot_status === "complete"
                    ? `${evidenceProvenance.evidence_count} 段已绑定${
                        evidenceProvenance.web_count
                          ? `，其中 ${evidenceProvenance.web_count} 段来自网页 snippet`
                          : ""
                      }${
                        (evidenceProvenance.items || []).some(
                          (item) => item.publication_anchor_bound,
                        )
                          ? `，${
                              (evidenceProvenance.items || []).filter(
                                (item) =>
                                  item.source_type === "knowledge_base" &&
                                  item.publication_anchor_bound,
                              ).length
                            } 段绑定发布日期`
                          : ""
                      }${
                        (evidenceProvenance.items || []).some(
                          (item) => item.version_family_bound,
                        )
                          ? `，${
                              (evidenceProvenance.items || []).filter(
                                (item) =>
                                  item.source_type === "knowledge_base" &&
                                  item.version_family_bound,
                              ).length
                            } 段绑定文档系列`
                          : ""
                      }`
                    : evidenceProvenance?.snapshot_status === "partial"
                      ? "部分证据来源版本不可确认"
                      : "未绑定模型最终看到的证据快照"}
                </strong>
              </div>
              <div>
                <span>可信策略</span>
                <strong>
                  可信度策略 v{provenance.policy.trust_contract_version} · 回答策略 v
                  {provenance.policy.answer_policy_version}
                </strong>
              </div>
              {temporalContext ? (
                <div>
                  <span>相对时间基准</span>
                  <strong>
                    {temporalContext.reference_date} · {temporalContext.timezone}
                  </strong>
                </div>
              ) : null}
              {freshness?.required ? (
                <div>
                  <span>时效意图</span>
                  <strong>
                    {freshness.mode === "current"
                      ? "当前有效"
                      : freshness.mode === "latest_published"
                        ? "最新发布"
                        : freshness.mode === "latest_effective"
                          ? "最新生效"
                          : "近期（窗口未定义）"}
                    {` · Classifier v${freshness.classifier_version}`}
                  </strong>
                </div>
              ) : null}
              <div>
                <span>判定摘要</span>
                <code title={provenance.digest}>{provenanceDigest.slice(0, 16)}</code>
              </div>
           </div>
           <p>该摘要只包含版本与指纹，不保存原始问题、完整提示词或访问密钥。</p>
          </details>
        ) : null}
              </div>
            </section>
          </>
        ) : null}
        <div className="answer-actions">
          {message.answer_state !== "failed" ? (
            <button
              type="button"
              onClick={async () => {
                await navigator.clipboard.writeText(displayedContent);
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1200);
              }}
            >
              <ClipboardDocumentIcon aria-hidden="true" />
              {copied ? "已复制" : "复制"}
            </button>
          ) : null}
          <button
            type="button"
            onClick={onRegenerate}
            disabled={queryPending || queryDisabled}
            title={queryDisabled ? "当前知识资料暂不可用" : undefined}
          >
            <ArrowPathIcon aria-hidden="true" />
            {regenerating ? "正在生成…" : "重新生成"}
          </button>
          {message.answer_state !== "failed" ? (
            <>
              <button
                type="button"
                className={feedback === "helpful" ? "selected" : ""}
                aria-pressed={feedback === "helpful"}
                disabled={feedbackPending}
                onClick={() => applyFeedback("helpful")}
              >
                <HandThumbUpIcon aria-hidden="true" />
                有帮助
              </button>
              <button
                type="button"
                className={feedback === "unhelpful" ? "selected" : ""}
                aria-pressed={feedback === "unhelpful"}
                disabled={feedbackPending}
                onClick={() => applyFeedback("unhelpful")}
              >
                <HandThumbDownIcon aria-hidden="true" />
                没帮助
              </button>
            </>
          ) : null}
        </div>
      </div>
    </article>
  );
}

function SourcePreviewDialog({
  target,
  onClose,
}: {
  target: SourcePreviewTarget | null;
  onClose: () => void;
}) {
  const open = Boolean(target);
  const headingId = useId();
  const dialogRef = useModalFocus({ open, onDismiss: onClose });

  useEffect(() => {
    if (!open) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (!target) return null;

  const isImage = target.kind === "image";
  const title = isImage
    ? target.alt
    : `${target.citation.display_name}${
        target.citation.page_number ? ` · 第 ${target.citation.page_number} 页` : ""
      }`;
  const documentHref = isImage ? null : citationDocumentHref(target.citation);

  return (
    <>
      <div
        className="source-preview-backdrop"
        role="presentation"
        onMouseDown={onClose}
      />
      <section
        ref={dialogRef}
        className={`source-preview-dialog ${isImage ? "is-image" : "is-document"}`}
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={headingId}
      >
        <header className="source-preview-heading">
          <div>
            <span>原文预览</span>
            <h2 id={headingId}>{title}</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            data-dialog-initial-focus
            onClick={onClose}
            aria-label="关闭原文预览"
          >
            <XMarkIcon aria-hidden="true" />
          </button>
        </header>
        <div className="source-preview-content">
          {isImage ? (
            <img
              className="source-preview-image"
              src={target.src}
              alt={target.alt}
            />
          ) : documentHref ? (
            <iframe
              className="source-preview-frame"
              src={documentHref}
              title={`预览 ${title}`}
            />
          ) : (
            <p className="source-preview-unavailable">原文暂不可用。</p>
          )}
        </div>
        <p className="source-preview-hint">
          预览在独立层中打开，不会把原始图片或 PDF 页面插入聊天流。
        </p>
      </section>
    </>
  );
}

export function ChatPage({ isAdmin = false }: { isAdmin?: boolean }) {
  const { conversationId = "" } = useParams();
  const navigate = useNavigate();
  const queryCache = useQueryClient();
  const compactViewport = useMediaQuery("(max-width: 820px)");
  const [question, setQuestion] = useState("");
  const [showDeleteConversation, setShowDeleteConversation] = useState(false);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const [selectedCitationSpan, setSelectedCitationSpan] =
    useState<CitationSpan | null>(null);
  const [previewTarget, setPreviewTarget] = useState<SourcePreviewTarget | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [focusDrawerSelection, setFocusDrawerSelection] = useState(false);
  const [streamStages, setStreamStages] = useState<QueryStageEvent[]>([]);
  const [useWebSearch, setUseWebSearch] = useState(false);
  const streamControllerRef = useRef<AbortController | null>(null);
  const lastCitationTriggerRef = useRef<HTMLButtonElement | null>(null);
  const conversationEndRef = useRef<HTMLDivElement | null>(null);
  const conversation = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => getConversation(conversationId),
    enabled: Boolean(conversationId),
  });
  const queryScope = useQuery({
    queryKey: ["query-scope"],
    queryFn: getQueryScope,
    staleTime: 15_000,
  });
  const mutation = useMutation({
    mutationFn: async (content: string) => {
      const controller = new AbortController();
      streamControllerRef.current = controller;
      setStreamStages([]);
      try {
        return await streamMessage(
          conversationId,
          content,
          (stage) => setStreamStages((current) => [...current, stage].slice(-8)),
          controller.signal,
          useWebSearch,
        );
      } catch (requestError) {
        if (controller.signal.aborted) throw cancelledQueryError();
        throw requestError;
      }
    },
    onSuccess: async () => {
      setQuestion("");
      await Promise.all([
        queryCache.invalidateQueries({ queryKey: ["conversation", conversationId] }),
        queryCache.invalidateQueries({ queryKey: ["conversations"] }),
      ]);
    },
    onSettled: async () => {
      streamControllerRef.current = null;
      await queryCache.invalidateQueries({
        queryKey: ["conversation", conversationId],
      });
    },
  });
  const removeConversation = useMutation({
    mutationFn: () => deleteConversation(conversationId),
    onSuccess: async () => {
      queryCache.removeQueries({
        queryKey: ["conversation", conversationId],
        exact: true,
      });
      await queryCache.invalidateQueries({ queryKey: ["conversations"] });
      navigate("/");
    },
  });
  const citations = useMemo(
    () =>
      conversation.data?.messages.flatMap((message) => message.citations || []) || [],
    [conversation.data],
  );
  const messageCount = conversation.data?.messages.length || 0;
  const hasConversation = Boolean(conversation.data);

  useEffect(() => {
    if (!selectedCitation && citations.length) {
      setSelectedCitation(citations[0]);
      setSelectedCitationSpan(null);
    }
  }, [citations, selectedCitation]);

  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent<CitationDrawerState>(CITATION_STATE_EVENT, {
        detail: {
          available: citations.length > 0,
          open: drawerOpen && citations.length > 0,
        },
      }),
    );
  }, [citations.length, drawerOpen]);

  useEffect(
    () => () => {
      window.dispatchEvent(
        new CustomEvent<CitationDrawerState>(CITATION_STATE_EVENT, {
          detail: { available: false, open: false },
        }),
      );
    },
    [],
  );

  useEffect(() => {
    setDrawerOpen(false);
    setFocusDrawerSelection(false);
    setSelectedCitationSpan(null);
    setPreviewTarget(null);
  }, [compactViewport, conversationId]);

  useEffect(() => {
    if (drawerOpen) return undefined;
    const timeout = window.setTimeout(() => {
      const trigger =
        lastCitationTriggerRef.current?.isConnected
          ? lastCitationTriggerRef.current
          : selectedCitation
            ? document.querySelector<HTMLButtonElement>(
                `[data-citation-trigger="${selectedCitation.id}"]`,
              )
            : null;
      if (trigger?.isConnected) trigger.focus();
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [drawerOpen, selectedCitation]);

  useEffect(() => {
    const toggleDrawer = () => {
      if (citations.length) {
        setFocusDrawerSelection(false);
        setDrawerOpen((current) => !current);
      }
    };
    window.addEventListener(TOGGLE_CITATIONS_EVENT, toggleDrawer);
    return () => window.removeEventListener(TOGGLE_CITATIONS_EVENT, toggleDrawer);
  }, [citations.length]);

  useEffect(
    () => () => {
      streamControllerRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    if (!hasConversation) return undefined;
    const frame = window.requestAnimationFrame(() => {
      conversationEndRef.current?.scrollIntoView?.({
        behavior: mutation.isPending ? "smooth" : "auto",
        block: "end",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [hasConversation, messageCount, mutation.isPending, streamStages.length]);

  const closeCitationDrawer = () => {
    setDrawerOpen(false);
    setFocusDrawerSelection(false);
    window.setTimeout(() => {
      const trigger =
        lastCitationTriggerRef.current?.isConnected
          ? lastCitationTriggerRef.current
          : selectedCitation
            ? document.querySelector<HTMLButtonElement>(
                `[data-citation-trigger="${selectedCitation.id}"]`,
              )
            : null;
      if (trigger?.isConnected) trigger.focus();
    }, 0);
  };

  if (conversation.isLoading) return <LoadingState label="正在恢复对话…" />;
  if (conversation.error) return <ErrorState message={conversation.error.message} />;
  if (!conversation.data) return null;
  const queryDisabled = !queryScope.data?.askable;

  return (
    <div className={`chat-page ${drawerOpen && citations.length ? "chat-page--drawer" : ""}`}>
      <section className="conversation-column">
        <div className="conversation-toolbar">
          <div>
            <span>当前对话</span>
            <strong>{conversation.data.title}</strong>
          </div>
          <button
            className="danger-outline-button"
            type="button"
            disabled={mutation.isPending}
            title={mutation.isPending ? "回答生成完成后才能删除" : "删除当前对话"}
            onClick={() => {
              removeConversation.reset();
              setShowDeleteConversation(true);
            }}
          >
            <TrashIcon aria-hidden="true" />
            删除对话
          </button>
        </div>
        <div className="conversation-scroll">
          {conversation.data.messages.map((message, index) =>
            message.role === "user" ? (
              <article className="user-message" key={message.id}>
                <div>
                  <p>{message.content}</p>
                  <time>
                    {new Date(message.created_at).toLocaleTimeString("zh-CN", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </div>
                <UserCircleIcon aria-hidden="true" />
              </article>
            ) : (
              <AssistantMessage
                key={message.id}
                conversationId={conversationId}
                message={message}
                isAdmin={isAdmin}
                onOpenSource={(citation) =>
                  setPreviewTarget({ kind: "citation", citation })
                }
                onImageOpen={(image) => setPreviewTarget(image)}
                queryPending={mutation.isPending}
                regenerating={
                  mutation.isPending &&
                  mutation.variables ===
                    conversation.data.messages
                      .slice(0, index)
                      .reverse()
                      .find((candidate) => candidate.role === "user")?.content
                }
                queryDisabled={queryDisabled}
                onRegenerate={() => {
                  const previousUserMessage = conversation.data.messages
                    .slice(0, index)
                    .reverse()
                    .find((candidate) => candidate.role === "user");
                  if (previousUserMessage) mutation.mutate(previousUserMessage.content);
                }}
                onCitation={(citation, trigger, span) => {
                  lastCitationTriggerRef.current = trigger;
                  setSelectedCitation(citation);
                  setSelectedCitationSpan(span || null);
                  setFocusDrawerSelection(true);
                  setDrawerOpen(true);
                }}
              />
            ),
          )}
          {mutation.isPending ? (
            <QueryProgress
              stages={streamStages}
              onStop={() => streamControllerRef.current?.abort()}
            />
          ) : null}
          {mutation.error ? (
            <QueryFailureState
              error={mutation.error}
              pending={mutation.isPending}
              onRetry={() => {
                const retryQuestion = (mutation.variables || question).trim();
                if (retryQuestion) mutation.mutate(retryQuestion);
              }}
              onOpenKnowledgeBase={() =>
                void queryScope.refetch()
              }
            />
          ) : null}
          <div
            ref={conversationEndRef}
            className="conversation-end-anchor"
            aria-hidden="true"
          />
        </div>
        <div className="sticky-composer">
          <QuestionComposer
            compact
            knowledgeBase={conversation.data.knowledge_base || undefined}
            scope={queryScope.data}
            value={question}
            onChange={setQuestion}
            onSubmit={() => mutation.mutate(question)}
            pending={mutation.isPending}
            useWebSearch={useWebSearch}
            onUseWebSearchChange={setUseWebSearch}
          />
        </div>
      </section>
      {drawerOpen && citations.length ? (
        <>
          {compactViewport ? (
            <div
              className="citation-backdrop"
              role="presentation"
              onMouseDown={closeCitationDrawer}
            />
          ) : null}
          <CitationDrawer
            citations={citations}
            selectedId={selectedCitation?.id || null}
            selectedSpan={selectedCitationSpan}
            focusSelected={focusDrawerSelection}
            modal={compactViewport}
            onSelect={(citation) => {
              setFocusDrawerSelection(false);
              setSelectedCitation(citation);
              setSelectedCitationSpan(null);
            }}
            onOpenSource={(citation) =>
              setPreviewTarget({ kind: "citation", citation })
            }
            onClose={closeCitationDrawer}
          />
        </>
      ) : null}
      <SourcePreviewDialog
        target={previewTarget}
        onClose={() => setPreviewTarget(null)}
      />
      <DangerConfirmDialog
        open={showDeleteConversation}
        title="删除这段对话？"
        description={
          <p>
            将删除“{conversation.data.title}”中的
            {conversation.data.messages.length} 条消息及其引用记录。知识库、文档和向量数据不会受到影响。
          </p>
        }
        pending={removeConversation.isPending}
        error={removeConversation.error}
        onClose={() => {
          if (removeConversation.isPending) return;
          removeConversation.reset();
          setShowDeleteConversation(false);
        }}
        onConfirm={() => removeConversation.mutate()}
      />
    </div>
  );
}
