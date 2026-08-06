import {
  ArrowPathIcon,
  ArrowTopRightOnSquareIcon,
  BookOpenIcon,
  ChatBubbleLeftEllipsisIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleStackIcon,
  ClipboardDocumentIcon,
  Cog6ToothIcon,
  DocumentTextIcon,
  GlobeAltIcon,
  HandThumbDownIcon,
  HandThumbUpIcon,
  MagnifyingGlassIcon,
  PaperAirplaneIcon,
  PaperClipIcon,
  PlusIcon,
  RectangleGroupIcon,
  TrashIcon,
  UserCircleIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import {
  BrowserRouter,
  Link,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
  useMatch,
  useNavigate,
  useParams,
} from "react-router-dom";

import { ConsoleApp } from "./ConsoleApp";
import { DangerConfirmDialog } from "./DangerConfirmDialog";
import { QueryFailureState } from "./QueryFailureState";
import { useMediaQuery } from "./useMediaQuery";
import { useModalFocus } from "./useModalFocus";
import {
  type Citation,
  type KnowledgeBase,
  type Message,
  type ProductDocument,
  type QueryStageEvent,
  ProductApiError,
  createConversation,
  createKnowledgeBase,
  deleteConversation,
  deleteDocument,
  deleteKnowledgeBase,
  getConversation,
  getKnowledgeBase,
  listConversations,
  listDocuments,
  listKnowledgeBases,
  reindexKnowledgeBase,
  retryDocument,
  setCurrentKnowledgeBase,
  streamMessage,
  uploadDocument,
} from "./product-api";
import "./workspace.css";

export const workspaceQueryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
    },
  },
});

const SUGGESTED_QUESTIONS = [
  "总结当前知识库中的核心内容",
  "这些资料中最重要的三个结论是什么？",
  "帮我找出文档中可以执行的下一步",
];

const TOGGLE_CITATIONS_EVENT = "deepsearcher:toggle-citations";
const CITATION_STATE_EVENT = "deepsearcher:citation-state";

type CitationDrawerState = {
  available: boolean;
  open: boolean;
};

function formatRelativeDate(value: string) {
  const date = new Date(value);
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? "今天"
    : date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function formatFileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function LoadingState({ label = "正在加载…" }: { label?: string }) {
  return (
    <div className="product-state product-state--loading" role="status">
      <ArrowPathIcon aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="product-state product-state--error" role="alert">
      <span>{message}</span>
    </div>
  );
}

function stageLabel(stage: QueryStageEvent) {
  switch (stage.event) {
    case "started":
      return "已开始处理问题";
    case "routing":
      return `已选择 ${stage.data.agent} 检索流程`;
    case "iteration":
      return `正在进行第 ${stage.data.iteration} 轮检索`;
    case "retrieval":
      return `已找到 ${stage.data.retrieved_count} 个候选片段`;
    case "web_search":
      if (stage.data.status === "disabled") return "联网搜索未配置，继续使用知识库";
      if (stage.data.status === "degraded") return "联网搜索暂时不可用，已降级为知识库检索";
      if (stage.data.status === "partial") {
        return `联网搜索部分完成，获得 ${stage.data.result_count} 个网页片段`;
      }
      if (stage.data.status === "empty") return "联网搜索完成，未找到可用网页片段";
      return `联网搜索完成，获得 ${stage.data.result_count} 个网页片段`;
    case "support":
      return `其中 ${stage.data.supported_count} 个片段通过证据核验`;
    case "reflection":
      return stage.data.has_enough_information
        ? "证据检查完成，正在组织回答"
        : "已完成本轮证据检查";
  }
}

function QueryProgress({
  stages,
  onStop,
}: {
  stages: QueryStageEvent[];
  onStop: () => void;
}) {
  const visibleStages = stages.slice(-5);
  return (
    <section className="query-progress" role="status" aria-live="polite">
      <div className="query-progress-heading">
        <span>
          <ArrowPathIcon className="spin" aria-hidden="true" />
          正在检索并生成回答
        </span>
        <button type="button" onClick={onStop}>
          <XMarkIcon aria-hidden="true" />
          停止生成
        </button>
      </div>
      {visibleStages.length ? (
        <ol>
          {visibleStages.map((stage) => (
            <li key={`${stage.request_id}-${stage.sequence}`}>
              <CheckCircleIcon aria-hidden="true" />
              {stageLabel(stage)}
            </li>
          ))}
        </ol>
      ) : (
        <p>正在连接问答服务…</p>
      )}
      <small>这里展示的是系统执行阶段，不是模型的思维链。</small>
    </section>
  );
}

function cancelledQueryError() {
  return new ProductApiError("本次回答已停止。", "QUERY_CANCELLED", true);
}

function CreateKnowledgeBaseDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (knowledgeBase: KnowledgeBase) => void;
}) {
  const queryCache = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const mutation = useMutation({
    mutationFn: createKnowledgeBase,
    onSuccess: async (knowledgeBase) => {
      await queryCache.invalidateQueries({ queryKey: ["knowledge-bases"] });
      setName("");
      setDescription("");
      onCreated(knowledgeBase);
    },
  });
  const dialogRef = useModalFocus({
    open,
    onDismiss: onClose,
    dismissBlocked: mutation.isPending,
  });

  if (!open) return null;
  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={() => {
        if (!mutation.isPending) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="dialog-card"
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby="create-knowledge-base-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">新建资料空间</span>
            <h2 id="create-knowledge-base-title">创建知识库</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label="关闭"
            disabled={mutation.isPending}
          >
            <XMarkIcon aria-hidden="true" />
          </button>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate({ name: name.trim(), description: description.trim() });
          }}
        >
          <label className="form-field">
            <span>知识库名称</span>
            <input
              data-dialog-initial-focus
              value={name}
              maxLength={40}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：AI 全栈学习资料"
            />
          </label>
          <label className="form-field">
            <span>描述（可选）</span>
            <textarea
              value={description}
              maxLength={200}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="说明这个知识库包含哪些资料"
            />
          </label>
          {mutation.error ? <ErrorState message={mutation.error.message} /> : null}
          <div className="dialog-actions">
            <button
              className="secondary-button"
              type="button"
              onClick={onClose}
              disabled={mutation.isPending}
            >
              取消
            </button>
            <button
              className="product-primary-button"
              type="submit"
              disabled={!name.trim() || mutation.isPending}
            >
              {mutation.isPending ? "正在创建…" : "创建知识库"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function WorkspaceLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [citationDrawerState, setCitationDrawerState] =
    useState<CitationDrawerState>({ available: false, open: false });
  const knowledgeBases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: listKnowledgeBases,
  });
  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
  });
  const conversationMatch = useMatch("/chat/:conversationId");
  const knowledgeBaseMatch = useMatch("/knowledge/:knowledgeBaseId");
  const conversationId = conversationMatch?.params.conversationId || "";
  const activeConversation = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => getConversation(conversationId),
    enabled: Boolean(conversationId),
  });
  const currentKnowledgeBase = knowledgeBases.data?.find((item) => item.is_current);
  const conversationSummary = conversations.data?.find(
    (item) => item.id === conversationId,
  );
  const routeKnowledgeBase = knowledgeBases.data?.find(
    (item) => item.id === knowledgeBaseMatch?.params.knowledgeBaseId,
  );
  const contextualKnowledgeBase =
    activeConversation.data?.knowledge_base || routeKnowledgeBase || currentKnowledgeBase;
  const contextualKnowledgeBaseId =
    contextualKnowledgeBase?.id || conversationSummary?.knowledge_base_id;
  const contextualKnowledgeBaseName =
    contextualKnowledgeBase?.name || conversationSummary?.knowledge_base_name;
  const isConversationRoute = location.pathname.startsWith("/chat/");

  useEffect(() => {
    const handleCitationState = (event: Event) => {
      const detail = (event as CustomEvent<CitationDrawerState>).detail;
      if (detail) setCitationDrawerState(detail);
    };
    window.addEventListener(CITATION_STATE_EVENT, handleCitationState);
    return () => window.removeEventListener(CITATION_STATE_EVENT, handleCitationState);
  }, []);

  useEffect(() => {
    if (!isConversationRoute) {
      setCitationDrawerState({ available: false, open: false });
    }
  }, [isConversationRoute]);

  return (
    <div className="product-shell">
      <a className="skip-link" href="#workspace-main">
        跳到主要内容
      </a>
      <aside className="product-sidebar">
        <Link className="brand-lockup" to="/" aria-label="DeepSearcher 首页">
          <img src="/deepsearcher-logo.png" alt="DeepSearcher" />
        </Link>

        <button className="new-chat-button" type="button" onClick={() => navigate("/")}>
          <PlusIcon aria-hidden="true" />
          新建对话
        </button>

        <nav className="primary-navigation" aria-label="主导航">
          <NavLink to="/" end>
            <ChatBubbleLeftEllipsisIcon aria-hidden="true" />
            对话
          </NavLink>
          <NavLink to="/knowledge">
            <CircleStackIcon aria-hidden="true" />
            知识库
          </NavLink>
        </nav>

        <section className="sidebar-section">
          <div className="sidebar-section-heading">
            <span>我的知识空间</span>
            <button type="button" onClick={() => setShowCreateDialog(true)} aria-label="创建知识库">
              <PlusIcon aria-hidden="true" />
            </button>
          </div>
          {knowledgeBases.isLoading ? (
            <span className="sidebar-muted">正在读取…</span>
          ) : currentKnowledgeBase ? (
            <Link
              className="current-knowledge-card"
              to={`/knowledge/${currentKnowledgeBase.id}`}
            >
              <CircleStackIcon aria-hidden="true" />
              <span>{currentKnowledgeBase.name}</span>
              <i aria-label="当前知识库" />
            </Link>
          ) : (
            <button
              className="sidebar-empty-action"
              type="button"
              onClick={() => setShowCreateDialog(true)}
            >
              创建第一个知识库
            </button>
          )}
        </section>

        <section className="sidebar-section sidebar-history">
          <div className="sidebar-section-heading">
            <span>历史对话</span>
          </div>
          {conversations.data?.length ? (
            <>
              <span className="history-group-label">最近</span>
              {conversations.data.slice(0, 7).map((conversation) => (
                <NavLink
                  className="history-item"
                  key={conversation.id}
                  to={`/chat/${conversation.id}`}
                  title={conversation.title}
                >
                  <ChatBubbleLeftEllipsisIcon aria-hidden="true" />
                  <span>{conversation.title}</span>
                </NavLink>
              ))}
            </>
          ) : (
            <span className="sidebar-muted">对话会自动保存在这里</span>
          )}
        </section>

        <div className="sidebar-footer">
          <UserCircleIcon aria-hidden="true" />
          <span>本地用户</span>
          <Link to="/console" aria-label="打开学习控制台">
            <Cog6ToothIcon aria-hidden="true" />
          </Link>
        </div>
      </aside>

      <main className="product-main" id="workspace-main" tabIndex={-1}>
        <header className="product-topbar">
          <button
            className="knowledge-switcher"
            type="button"
            aria-label={
              contextualKnowledgeBaseName
                ? `打开知识库：${contextualKnowledgeBaseName}`
                : "选择知识库"
            }
            onClick={() =>
              navigate(
                contextualKnowledgeBaseId
                  ? `/knowledge/${contextualKnowledgeBaseId}`
                  : "/knowledge",
              )
            }
          >
            <CircleStackIcon aria-hidden="true" />
            <span>{contextualKnowledgeBaseName || "选择知识库"}</span>
            <ChevronDownIcon aria-hidden="true" />
          </button>
          <button className="search-placeholder" type="button" disabled>
            <MagnifyingGlassIcon aria-hidden="true" />
            <span>搜索知识库内容</span>
            <kbd>⌘K</kbd>
          </button>
          <button
            className="citation-toggle"
            type="button"
            aria-label="切换引用来源"
            aria-expanded={isConversationRoute && citationDrawerState.open}
            aria-controls={citationDrawerState.available ? "citation-drawer" : undefined}
            disabled={!isConversationRoute || !citationDrawerState.available}
            title={
              !isConversationRoute
                ? "仅在对话中查看引用"
                : !citationDrawerState.available
                  ? "当前对话没有引用来源"
                  : undefined
            }
            onClick={() => window.dispatchEvent(new Event(TOGGLE_CITATIONS_EVENT))}
          >
            <RectangleGroupIcon aria-hidden="true" />
          </button>
        </header>
        <Outlet />
      </main>

      <CreateKnowledgeBaseDialog
        open={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        onCreated={(knowledgeBase) => {
          setShowCreateDialog(false);
          navigate(`/knowledge/${knowledgeBase.id}`);
        }}
      />
    </div>
  );
}

function QuestionComposer({
  knowledgeBase,
  value,
  onChange,
  onSubmit,
  pending,
  useWebSearch,
  onUseWebSearchChange,
  compact = false,
}: {
  knowledgeBase?: KnowledgeBase;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  pending: boolean;
  useWebSearch: boolean;
  onUseWebSearchChange: (enabled: boolean) => void;
  compact?: boolean;
}) {
  const hasReadyDocuments = Boolean(knowledgeBase?.ready_document_count);
  const indexUnavailable =
    hasReadyDocuments && knowledgeBase?.index_status !== "verified";
  const disabled = !knowledgeBase || !hasReadyDocuments || indexUnavailable;
  return (
    <form
      className={`question-composer ${compact ? "question-composer--compact" : ""}`}
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
          indexUnavailable
            ? "请先在知识库页面重建并验证索引"
            : disabled
              ? "知识库中有可用文档后即可提问"
              : "输入你的问题，Shift + Enter 换行"
        }
        disabled={disabled || pending}
      />
      <div className="composer-toolbar">
        <Link
          className="composer-tool"
          to={knowledgeBase ? `/knowledge/${knowledgeBase.id}` : "/knowledge"}
        >
          {indexUnavailable ? (
            <ArrowPathIcon aria-hidden="true" />
          ) : (
            <PaperClipIcon aria-hidden="true" />
          )}
          {indexUnavailable ? "前往重建" : "上传文件"}
        </Link>
        <span className="composer-divider" />
        <button
          className={`composer-web-search ${useWebSearch ? "active" : ""}`}
          type="button"
          aria-pressed={useWebSearch}
          disabled={disabled || pending}
          title="启用后，生成的搜索词会发送给已配置的 Web Search Provider"
          onClick={() => onUseWebSearchChange(!useWebSearch)}
        >
          <GlobeAltIcon aria-hidden="true" />
          联网搜索
        </button>
        <span className="composer-divider" />
        <span className="composer-knowledge">
          <CircleStackIcon aria-hidden="true" />
          知识库：{knowledgeBase?.name || "未选择"}
        </span>
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

function NewChatPage() {
  const navigate = useNavigate();
  const queryCache = useQueryClient();
  const [question, setQuestion] = useState("");
  const [error, setError] = useState("");
  const [streamStages, setStreamStages] = useState<QueryStageEvent[]>([]);
  const [useWebSearch, setUseWebSearch] = useState(false);
  const streamControllerRef = useRef<AbortController | null>(null);
  const knowledgeBases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: listKnowledgeBases,
  });
  const currentKnowledgeBase = knowledgeBases.data?.find((item) => item.is_current);
  const mutation = useMutation({
    mutationFn: async () => {
      if (!currentKnowledgeBase) throw new Error("请先创建一个知识库。");
      const conversation = await createConversation(currentKnowledgeBase.id);
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

  if (knowledgeBases.isLoading) return <LoadingState />;
  const hasKnowledgeBase = Boolean(currentKnowledgeBase);
  const hasReadyDocuments = Boolean(currentKnowledgeBase?.ready_document_count);
  const indexUnavailable =
    hasReadyDocuments && currentKnowledgeBase?.index_status !== "verified";
  const canAsk = hasReadyDocuments && !indexUnavailable;

  return (
    <div className="new-chat-page">
      <div className="new-chat-content">
        <div className="new-chat-mark">
          {canAsk ? (
            <BookOpenIcon aria-hidden="true" />
          ) : (
            <CircleStackIcon aria-hidden="true" />
          )}
        </div>
        <h1>
          {!hasKnowledgeBase
            ? "先创建一个知识库"
            : canAsk
              ? "向你的资料提问"
              : indexUnavailable
                ? "先验证这个知识库的索引"
                : "为这个知识库添加资料"}
        </h1>
        <p>
          {!hasKnowledgeBase
            ? "上传 PDF 后，就可以基于自己的资料提问。"
            : canAsk
              ? "答案会依据当前知识库，并附上可以核对的来源。"
              : indexUnavailable
                ? "这是升级前建立的索引，完整重建后即可继续可信问答。"
                : "上传并处理至少一份 PDF 后，就可以开始可信问答。"}
        </p>

        <QuestionComposer
          knowledgeBase={currentKnowledgeBase}
          value={question}
          onChange={setQuestion}
          onSubmit={() => {
            setError("");
            mutation.mutate();
          }}
          pending={mutation.isPending}
          useWebSearch={useWebSearch}
          onUseWebSearchChange={setUseWebSearch}
        />
        {mutation.isPending ? (
          <QueryProgress
            stages={streamStages}
            onStop={() => streamControllerRef.current?.abort()}
          />
        ) : null}
        {error ? <ErrorState message={error} /> : null}

        {!canAsk ? (
          <Link
            className="product-primary-button empty-primary-action"
            to={currentKnowledgeBase ? `/knowledge/${currentKnowledgeBase.id}` : "/knowledge"}
          >
            {!hasKnowledgeBase
              ? "创建知识库"
              : indexUnavailable
                ? "前往重建索引"
                : "上传 PDF"}
          </Link>
        ) : (
          <div className="suggested-questions">
            <span>你可以这样问</span>
            <div>
              {SUGGESTED_QUESTIONS.map((suggestion) => (
                <button type="button" key={suggestion} onClick={() => setQuestion(suggestion)}>
                  {suggestion}
                  <ChevronRightIcon aria-hidden="true" />
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
  focusSelected,
  modal,
  onSelect,
  onClose,
}: {
  citations: Citation[];
  selectedId: string | null;
  focusSelected: boolean;
  modal: boolean;
  onSelect: (citation: Citation) => void;
  onClose: () => void;
}) {
  const selectedButtonRef = useRef<HTMLButtonElement | null>(null);
  const headingId = useId();
  const drawerRef = useModalFocus({ open: modal, onDismiss: onClose });

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
        回答中的引用来自以下文档或网页，点击可以核对原文片段。
      </p>
      <div className="citation-list">
        {citations.map((citation) => (
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
                <p>{citation.text || "该来源暂时无法预览。"}</p>
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
              <a
                className="citation-open-source"
                href={`/api/documents/${encodeURIComponent(
                  citation.document_id,
                )}/content${
                  citation.page_number ? `#page=${citation.page_number}` : ""
                }`}
                target="_blank"
                rel="noreferrer"
              >
                打开原文
                {citation.page_number ? `第 ${citation.page_number} 页` : ""}
                <ArrowTopRightOnSquareIcon aria-hidden="true" />
              </a>
            ) : (
              <span className="citation-source-unavailable">
                {citation.source_type === "web" ? "网页地址不可用" : "原文已从知识库删除"}
              </span>
            )}
          </article>
        ))}
      </div>
    </aside>
  );
}

function AssistantMessage({
  message,
  onCitation,
  onRegenerate,
  queryPending,
  regenerating,
  queryDisabled,
}: {
  message: Message;
  onCitation: (citation: Citation, trigger: HTMLButtonElement) => void;
  onRegenerate: () => void;
  queryPending: boolean;
  regenerating: boolean;
  queryDisabled: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<"helpful" | "unhelpful" | null>(null);
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
        {message.answer_state === "failed" ? (
          <div className="query-failure-inline">
            系统没有完成本次检索，这不代表知识库中没有相关资料。
          </div>
        ) : null}
        <div className="markdown-answer">
          <ReactMarkdown>{message.content}</ReactMarkdown>
        </div>
        {message.citations.length ? (
          <div className="inline-citations" aria-label="回答引用">
            <span>参考来源</span>
            {message.citations.map((citation) => (
              <button
                type="button"
                key={citation.id}
                aria-label={`查看引用 ${citation.index}：${citation.display_name}${
                  citation.page_number ? `，第 ${citation.page_number} 页` : ""
                }`}
                onClick={(event) => onCitation(citation, event.currentTarget)}
              >
                [{citation.index}]
              </button>
            ))}
          </div>
        ) : null}
        <div className="answer-actions">
          {message.answer_state !== "failed" ? (
            <button
              type="button"
              onClick={async () => {
                await navigator.clipboard.writeText(message.content);
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
            title={queryDisabled ? "请先在知识库页面重建并验证索引" : undefined}
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
                onClick={() =>
                  setFeedback((current) =>
                    current === "helpful" ? null : "helpful",
                  )
                }
              >
                <HandThumbUpIcon aria-hidden="true" />
                有帮助
              </button>
              <button
                type="button"
                className={feedback === "unhelpful" ? "selected" : ""}
                aria-pressed={feedback === "unhelpful"}
                onClick={() =>
                  setFeedback((current) =>
                    current === "unhelpful" ? null : "unhelpful",
                  )
                }
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

function ChatPage() {
  const { conversationId = "" } = useParams();
  const navigate = useNavigate();
  const queryCache = useQueryClient();
  const compactViewport = useMediaQuery("(max-width: 820px)");
  const [question, setQuestion] = useState("");
  const [showDeleteConversation, setShowDeleteConversation] = useState(false);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(() => !compactViewport);
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
    if (!selectedCitation && citations.length) setSelectedCitation(citations[0]);
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
    setDrawerOpen(!compactViewport);
    setFocusDrawerSelection(false);
  }, [compactViewport, conversationId]);

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
    if (!compactViewport) {
      window.requestAnimationFrame(() => lastCitationTriggerRef.current?.focus());
    }
  };

  if (conversation.isLoading) return <LoadingState label="正在恢复对话…" />;
  if (conversation.error) return <ErrorState message={conversation.error.message} />;
  if (!conversation.data) return null;
  const queryDisabled =
    conversation.data.knowledge_base.index_status !== "verified";

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
                message={message}
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
                onCitation={(citation, trigger) => {
                  lastCitationTriggerRef.current = trigger;
                  setSelectedCitation(citation);
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
                navigate(`/knowledge/${conversation.data.knowledge_base.id}`)
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
            knowledgeBase={conversation.data.knowledge_base}
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
            focusSelected={focusDrawerSelection}
            modal={compactViewport}
            onSelect={(citation) => {
              setFocusDrawerSelection(false);
              setSelectedCitation(citation);
            }}
            onClose={closeCitationDrawer}
          />
        </>
      ) : null}
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

function KnowledgeListPage() {
  const navigate = useNavigate();
  const [showCreate, setShowCreate] = useState(false);
  const knowledgeBases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: listKnowledgeBases,
  });
  if (knowledgeBases.isLoading) return <LoadingState />;
  return (
    <div className="knowledge-page page-frame">
      <div className="page-heading">
        <div>
          <span className="eyebrow">资料空间</span>
          <h1>知识库</h1>
          <p>创建独立的资料范围，回答只会检索你选择的知识库。</p>
        </div>
        <button className="product-primary-button" type="button" onClick={() => setShowCreate(true)}>
          <PlusIcon aria-hidden="true" />
          创建知识库
        </button>
      </div>
      {knowledgeBases.data?.length ? (
        <div className="knowledge-grid">
          {knowledgeBases.data.map((knowledgeBase) => (
            <Link className="knowledge-card" to={`/knowledge/${knowledgeBase.id}`} key={knowledgeBase.id}>
              <div className="knowledge-card-icon">
                <CircleStackIcon aria-hidden="true" />
              </div>
              <div>
                <div className="knowledge-card-title">
                  <h2>{knowledgeBase.name}</h2>
                  {knowledgeBase.is_current ? <span>当前</span> : null}
                  {knowledgeBase.ready_document_count &&
                  knowledgeBase.index_status !== "verified" ? (
                    <span className="knowledge-index-warning">需重建</span>
                  ) : null}
                </div>
                <p>{knowledgeBase.description || "暂未添加描述"}</p>
                <div className="knowledge-card-meta">
                  <span>{knowledgeBase.document_count} 份文档</span>
                  <span>{knowledgeBase.ready_document_count} 份可用</span>
                  <span>{formatRelativeDate(knowledgeBase.updated_at)}更新</span>
                </div>
              </div>
              <ChevronRightIcon aria-hidden="true" />
            </Link>
          ))}
        </div>
      ) : (
        <div className="large-empty-state">
          <CircleStackIcon aria-hidden="true" />
          <h2>还没有知识库</h2>
          <p>创建一个知识库，上传 PDF 后开始提问。</p>
          <button className="product-primary-button" type="button" onClick={() => setShowCreate(true)}>
            创建知识库
          </button>
        </div>
      )}
      <CreateKnowledgeBaseDialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={(knowledgeBase) => navigate(`/knowledge/${knowledgeBase.id}`)}
      />
    </div>
  );
}

function DocumentStatusBadge({ status }: { status: string }) {
  const copy: Record<string, string> = {
    queued: "等待处理",
    processing: "正在处理",
    ready: "可用于问答",
    failed: "处理失败",
  };
  const label = copy[status] || status;
  return (
    <span
      className={`document-status document-status--${status}`}
      role="cell"
      aria-label={`文档状态：${label}`}
      aria-live="polite"
      aria-atomic="true"
    >
      {label}
    </span>
  );
}

function KnowledgeDetailPage() {
  const { knowledgeBaseId = "" } = useParams();
  const navigate = useNavigate();
  const queryCache = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [documentToDelete, setDocumentToDelete] = useState<ProductDocument | null>(null);
  const [showDeleteKnowledgeBase, setShowDeleteKnowledgeBase] = useState(false);
  const knowledgeBase = useQuery({
    queryKey: ["knowledge-base", knowledgeBaseId],
    queryFn: () => getKnowledgeBase(knowledgeBaseId),
  });
  const documents = useQuery({
    queryKey: ["documents", knowledgeBaseId],
    queryFn: () => listDocuments(knowledgeBaseId),
    refetchInterval: (query) =>
      query.state.data?.some((document) =>
        ["queued", "processing"].includes(document.status),
      )
        ? 2000
        : false,
  });
  const documentStatusSignature = (documents.data || [])
    .map((document) => `${document.id}:${document.status}`)
    .join("|");
  useEffect(() => {
    if (!documents.data) return;
    void Promise.all([
      queryCache.invalidateQueries({ queryKey: ["knowledge-base", knowledgeBaseId] }),
      queryCache.invalidateQueries({ queryKey: ["knowledge-bases"] }),
    ]);
  }, [documentStatusSignature, documents.data, knowledgeBaseId, queryCache]);
  const upload = useMutation({
    mutationFn: (file: File) => uploadDocument(knowledgeBaseId, file),
    onSuccess: async () => {
      await Promise.all([
        queryCache.invalidateQueries({ queryKey: ["documents", knowledgeBaseId] }),
        queryCache.invalidateQueries({ queryKey: ["knowledge-base", knowledgeBaseId] }),
        queryCache.invalidateQueries({ queryKey: ["knowledge-bases"] }),
      ]);
    },
  });
  const select = useMutation({
    mutationFn: () => setCurrentKnowledgeBase(knowledgeBaseId),
    onSuccess: async () => {
      await Promise.all([
        queryCache.invalidateQueries({ queryKey: ["knowledge-bases"] }),
        queryCache.invalidateQueries({ queryKey: ["knowledge-base", knowledgeBaseId] }),
      ]);
    },
  });
  const retry = useMutation({
    mutationFn: (documentId: string) => retryDocument(documentId),
    onSuccess: () =>
      queryCache.invalidateQueries({ queryKey: ["documents", knowledgeBaseId] }),
  });
  const remove = useMutation({
    mutationFn: (documentId: string) => deleteDocument(documentId),
    onSuccess: async () => {
      await Promise.all([
        queryCache.invalidateQueries({ queryKey: ["documents", knowledgeBaseId] }),
        queryCache.invalidateQueries({ queryKey: ["knowledge-base", knowledgeBaseId] }),
        queryCache.invalidateQueries({ queryKey: ["knowledge-bases"] }),
      ]);
      setDocumentToDelete(null);
    },
  });
  const removeKnowledgeBase = useMutation({
    mutationFn: () => deleteKnowledgeBase(knowledgeBaseId),
    onSuccess: async (result) => {
      queryCache.removeQueries({
        queryKey: ["knowledge-base", knowledgeBaseId],
        exact: true,
      });
      queryCache.removeQueries({
        queryKey: ["documents", knowledgeBaseId],
        exact: true,
      });
      await Promise.all([
        queryCache.invalidateQueries({ queryKey: ["knowledge-bases"] }),
        queryCache.invalidateQueries({ queryKey: ["conversations"] }),
      ]);
      navigate(
        result.current_knowledge_base_id
          ? `/knowledge/${result.current_knowledge_base_id}`
          : "/knowledge",
      );
    },
  });
  const reindex = useMutation({
    mutationFn: () => reindexKnowledgeBase(knowledgeBaseId),
    onSuccess: async (updatedKnowledgeBase) => {
      queryCache.setQueryData(
        ["knowledge-base", knowledgeBaseId],
        updatedKnowledgeBase,
      );
      await queryCache.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
  });

  if (knowledgeBase.isLoading || documents.isLoading) return <LoadingState />;
  if (knowledgeBase.error) return <ErrorState message={knowledgeBase.error.message} />;
  if (!knowledgeBase.data) return null;
  const indexManifest = knowledgeBase.data.index_manifest;
  const indexVerified =
    knowledgeBase.data.index_status === "verified" && indexManifest !== null;
  const documentsBusy = documents.data?.some((document) =>
    ["queued", "processing"].includes(document.status),
  );

  return (
    <div className="knowledge-detail page-frame">
      <div className="knowledge-detail-heading">
        <div className="knowledge-detail-icon">
          <CircleStackIcon aria-hidden="true" />
        </div>
        <div>
          <div className="title-with-status">
            <h1>{knowledgeBase.data.name}</h1>
            {knowledgeBase.data.is_current ? (
              <span role="status" aria-live="polite">
                当前知识库
              </span>
            ) : null}
          </div>
          <p>{knowledgeBase.data.description || "这个知识库还没有描述。"}</p>
        </div>
        <div className="knowledge-detail-actions">
          <button
            className="danger-outline-button"
            type="button"
            title={
              documents.data?.some((document) =>
                ["queued", "processing"].includes(document.status),
              )
                ? "所有文档处理完成后才能删除知识库"
                : "删除知识库"
            }
            disabled={documents.data?.some((document) =>
              ["queued", "processing"].includes(document.status),
            )}
            onClick={() => {
              removeKnowledgeBase.reset();
              setShowDeleteKnowledgeBase(true);
            }}
          >
            <TrashIcon aria-hidden="true" />
            删除知识库
          </button>
          {!knowledgeBase.data.is_current ? (
            <button
              className="secondary-button"
              type="button"
              disabled={select.isPending}
              onClick={() => select.mutate()}
            >
              {select.isPending ? "正在切换…" : "设为当前"}
            </button>
          ) : null}
          <button
            className="product-primary-button"
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={upload.isPending}
          >
            <PaperClipIcon aria-hidden="true" />
            {upload.isPending ? "正在上传…" : "上传 PDF"}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) upload.mutate(file);
              event.target.value = "";
            }}
          />
        </div>
      </div>

      <div className="knowledge-summary">
        <div>
          <strong>{knowledgeBase.data.document_count}</strong>
          <span>文档总数</span>
        </div>
        <div>
          <strong>{knowledgeBase.data.ready_document_count}</strong>
          <span>可用于问答</span>
        </div>
        <div>
          <strong>{knowledgeBase.data.conversation_count}</strong>
          <span>关联对话</span>
        </div>
      </div>

      <section
        className={`index-status-card ${
          indexVerified
            ? "index-status-card--verified"
            : "index-status-card--attention"
        }`}
        aria-labelledby="index-status-title"
        aria-busy={reindex.isPending}
      >
        <div className="index-status-icon">
          {indexVerified ? (
            <CheckCircleIcon aria-hidden="true" />
          ) : (
            <ArrowPathIcon aria-hidden="true" />
          )}
        </div>
        <div className="index-status-content">
          <div className="index-status-heading">
            <div>
              <span className="eyebrow">检索索引</span>
              <h2 id="index-status-title" aria-live="polite" aria-atomic="true">
                {indexVerified
                  ? "索引版本已验证"
                  : knowledgeBase.data.document_count
                    ? "需要重建索引"
                    : "等待文档建立索引"}
              </h2>
            </div>
            <span className="index-status-badge">
              {indexVerified ? "可安全问答" : "尚未验证"}
            </span>
          </div>
          <p>
            {indexVerified
              ? "文档向量与当前嵌入模型、分块配置已经绑定，查询前会自动核对版本。"
              : knowledgeBase.data.document_count
                ? "这是升级前创建的索引。为避免不同模型的向量被混用，问答会暂时阻止检索，请完整重建一次。"
                : "上传第一份 PDF 后，系统会记录嵌入模型与分块版本。"}
          </p>
          {indexManifest ? (
            <dl className="index-profile">
              <div>
                <dt>嵌入模型</dt>
                <dd>{indexManifest.embedding_model}</dd>
              </div>
              <div>
                <dt>模型版本</dt>
                <dd>{indexManifest.embedding_version}</dd>
              </div>
              <div>
                <dt>向量维度</dt>
                <dd>{indexManifest.dimension}</dd>
              </div>
              <div>
                <dt>分块配置</dt>
                <dd>
                  {indexManifest.chunk_size} / {indexManifest.chunk_overlap}
                </dd>
              </div>
            </dl>
          ) : null}
        </div>
        {knowledgeBase.data.document_count ? (
          <button
            className="secondary-button index-rebuild-button"
            type="button"
            onClick={() => reindex.mutate()}
            disabled={reindex.isPending || documentsBusy}
            title={
              documentsBusy
                ? "所有文档处理完成后才能重建索引"
                : "先创建并验证新索引，成功后再切换"
            }
          >
            <ArrowPathIcon aria-hidden="true" />
            {reindex.isPending
              ? "正在重建…"
              : indexVerified
                ? "重新构建"
                : "立即重建"}
          </button>
        ) : null}
      </section>

      {upload.error ? <ErrorState message={upload.error.message} /> : null}
      {reindex.error ? <ErrorState message={reindex.error.message} /> : null}
      <section className="document-section">
        <div className="section-title-row">
          <div>
            <h2>文档</h2>
            <p>只有处理完成的 PDF 才会用于回答。</p>
          </div>
          {knowledgeBase.data.ready_document_count && indexVerified ? (
            <button className="text-button" type="button" onClick={() => navigate("/")}>
              开始提问
              <ChevronRightIcon aria-hidden="true" />
            </button>
          ) : null}
        </div>
        {documents.data?.length ? (
          <div
            className="document-table"
            role="table"
            aria-label="知识库文档"
            aria-busy={documents.isFetching}
          >
            <div role="rowgroup">
              <div className="document-table-head" role="row">
                <span role="columnheader">文件名</span>
                <span role="columnheader">大小</span>
                <span role="columnheader">添加时间</span>
                <span role="columnheader">状态</span>
                <span role="columnheader">操作</span>
              </div>
            </div>
            <div role="rowgroup">
              {documents.data.map((document) => (
                <div className="document-row" key={document.id} role="row">
                  <span className="document-name" role="cell">
                    <DocumentTextIcon aria-hidden="true" />
                    <span>
                      <strong>{document.display_name}</strong>
                      {document.error ? <small>{document.error.message}</small> : null}
                    </span>
                  </span>
                  <span role="cell">
                    {formatFileSize(document.size_bytes)}
                    {document.page_count > 0 ? ` · ${document.page_count} 页` : ""}
                  </span>
                  <span role="cell">{formatRelativeDate(document.created_at)}</span>
                  <DocumentStatusBadge status={document.status} />
                  <span className="document-actions" role="cell">
                    {document.status === "failed" ? (
                      <button className="text-button" type="button" onClick={() => retry.mutate(document.id)}>
                        重试
                      </button>
                    ) : null}
                    <button
                      className="document-delete-button"
                      type="button"
                      aria-label={`删除 ${document.display_name}`}
                      title={
                        ["queued", "processing"].includes(document.status)
                          ? "文档处理完成后才能删除"
                          : "删除文档"
                      }
                      disabled={["queued", "processing"].includes(document.status)}
                      onClick={() => {
                        remove.reset();
                        setDocumentToDelete(document);
                      }}
                    >
                      <TrashIcon aria-hidden="true" />
                      删除
                    </button>
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="large-empty-state large-empty-state--compact">
            <DocumentTextIcon aria-hidden="true" />
            <h2>还没有文档</h2>
            <p>上传第一份 PDF，处理完成后即可开始提问。</p>
            <button className="product-primary-button" type="button" onClick={() => fileInput.current?.click()}>
              上传 PDF
            </button>
          </div>
        )}
      </section>
      <DangerConfirmDialog
        open={Boolean(documentToDelete)}
        title="删除文档？"
        description={
          <p>
            将删除“{documentToDelete?.display_name}”及其向量数据。历史回答中的引用文字会保留，
            但这份文档不会再用于后续问答。
          </p>
        }
        pending={remove.isPending}
        error={remove.error}
        onClose={() => {
          if (remove.isPending) return;
          remove.reset();
          setDocumentToDelete(null);
        }}
        onConfirm={() => {
          if (documentToDelete) remove.mutate(documentToDelete.id);
        }}
      />
      <DangerConfirmDialog
        open={showDeleteKnowledgeBase}
        title="删除整个知识库？"
        description={
          <p>
            将永久删除“{knowledgeBase.data.name}”、{knowledgeBase.data.document_count} 份文档、
            {knowledgeBase.data.conversation_count} 段对话，以及全部文件和向量数据。
          </p>
        }
        confirmLabel="删除知识库"
        pendingLabel="正在删除知识库…"
        pending={removeKnowledgeBase.isPending}
        error={removeKnowledgeBase.error}
        onClose={() => {
          if (removeKnowledgeBase.isPending) return;
          removeKnowledgeBase.reset();
          setShowDeleteKnowledgeBase(false);
        }}
        onConfirm={() => removeKnowledgeBase.mutate()}
      />
    </div>
  );
}

function KnowledgeDetailRoute() {
  const { knowledgeBaseId = "" } = useParams();
  return <KnowledgeDetailPage key={knowledgeBaseId} />;
}

function ConsoleRoute() {
  return <ConsoleApp />;
}

function ProductRouter() {
  return (
    <Routes>
      <Route element={<WorkspaceLayout />}>
        <Route index element={<NewChatPage />} />
        <Route path="chat/:conversationId" element={<ChatPage />} />
        <Route path="knowledge" element={<KnowledgeListPage />} />
        <Route path="knowledge/:knowledgeBaseId" element={<KnowledgeDetailRoute />} />
      </Route>
      <Route path="console" element={<ConsoleRoute />} />
    </Routes>
  );
}

export function App() {
  return (
    <QueryClientProvider client={workspaceQueryClient}>
      <BrowserRouter>
        <ProductRouter />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export function TestProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={workspaceQueryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
}
