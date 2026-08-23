import {
  ArrowRightStartOnRectangleIcon,
  ChatBubbleLeftEllipsisIcon,
  Cog6ToothIcon,
  CpuChipIcon,
  MagnifyingGlassIcon,
  RectangleGroupIcon,
  PlusIcon,
} from "@heroicons/react/24/outline";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import {
  CITATION_STATE_EVENT,
  TOGGLE_CITATIONS_EVENT,
  type CitationDrawerState,
} from "../components/evidence/citationEvents";
import { listConversations, type ConversationSummary, type ProductUser } from "../product-api";
import "../workspace.css";

const RECENT_CONVERSATION_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

function HistoryGroup({
  label,
  conversations,
  showCount = false,
}: {
  label: string;
  conversations: ConversationSummary[];
  showCount?: boolean;
}) {
  return (
    <section className="sidebar-history-group">
      <div className="sidebar-section-heading">
        <span>{label}</span>
        {showCount ? <span className="sidebar-section-count">{conversations.length}</span> : null}
      </div>
      <div className="sidebar-history-list">
        {conversations.map((conversation) => (
          <NavLink
            className={({ isActive }) => `history-item${isActive ? " active" : ""}`}
            key={conversation.id}
            to={`/chat/${conversation.id}`}
            title={conversation.title}
          >
            <ChatBubbleLeftEllipsisIcon aria-hidden="true" />
            <span>{conversation.title}</span>
          </NavLink>
        ))}
      </div>
    </section>
  );
}

export function UserLayout({
  user,
  onLogout,
}: {
  user: ProductUser;
  onLogout: () => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [historyQuery, setHistoryQuery] = useState("");
  const [citationDrawerState, setCitationDrawerState] =
    useState<CitationDrawerState>({ available: false, open: false });
  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
  });
  const isConversationRoute = location.pathname.startsWith("/chat/");
  const filteredConversations = useMemo(() => {
    const normalizedQuery = historyQuery.trim().toLocaleLowerCase();
    return (conversations.data ?? []).filter((conversation) => {
      return !normalizedQuery || conversation.title.toLocaleLowerCase().includes(normalizedQuery);
    });
  }, [conversations.data, historyQuery]);
  const historyGroups = useMemo(() => {
    const recentCutoff = Date.now() - RECENT_CONVERSATION_WINDOW_MS;
    return filteredConversations.reduce<{
      recent: ConversationSummary[];
      earlier: ConversationSummary[];
    }>(
      (groups, conversation) => {
        const updatedAt = Date.parse(conversation.updated_at);
        if (Number.isNaN(updatedAt) || updatedAt >= recentCutoff) {
          groups.recent.push(conversation);
        } else {
          groups.earlier.push(conversation);
        }
        return groups;
      },
      { recent: [], earlier: [] },
    );
  }, [filteredConversations]);

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
    <div className="product-shell user-shell">
      <a className="skip-link" href="#workspace-main">
        跳到主要内容
      </a>
      <aside className="product-sidebar user-sidebar">
        <Link className="brand-lockup" to="/" aria-label="DeepSearcher 首页">
          <span className="brand-mark" aria-hidden="true">
            <CpuChipIcon />
          </span>
          <span className="brand-copy">
            <strong className="brand-name">DeepSearcher</strong>
            <span className="user-brand-subtitle">企业级 AI 知识平台</span>
          </span>
        </Link>

        <button className="new-chat-button" type="button" onClick={() => navigate("/")}>
          <PlusIcon aria-hidden="true" />
          新建对话
        </button>

        <nav className="primary-navigation" aria-label="主导航">
          <button
            className="mobile-logout"
            type="button"
            aria-label="退出登录"
            onClick={onLogout}
          >
            <ArrowRightStartOnRectangleIcon aria-hidden="true" />
            <span>退出登录</span>
          </button>
        </nav>

        <div className="sidebar-history-search">
          <MagnifyingGlassIcon aria-hidden="true" />
          <label className="visually-hidden" htmlFor="history-search">
            搜索历史对话
          </label>
          <input
            id="history-search"
            type="search"
            value={historyQuery}
            onChange={(event) => setHistoryQuery(event.target.value)}
            placeholder="搜索历史对话..."
          />
        </div>

        <section className="sidebar-section sidebar-history">
          {historyGroups.recent.length ? (
            <HistoryGroup label="近期对话" conversations={historyGroups.recent} showCount />
          ) : null}
          {historyGroups.earlier.length ? (
            <HistoryGroup label="更早以前" conversations={historyGroups.earlier} />
          ) : null}
          {!filteredConversations.length ? (
            <span className="sidebar-muted">
              {historyQuery.trim() ? "没有找到匹配的对话" : "对话会自动保存在这里"}
            </span>
          ) : null}
        </section>

        <div className="sidebar-footer">
          <div className="sidebar-user-profile">
            <span className="sidebar-avatar" aria-hidden="true">
              {user.display_name.trim().slice(0, 1).toUpperCase() || "U"}
            </span>
            <span className="sidebar-user-copy">
              <strong title={user.username}>{user.display_name}</strong>
              <small>{user.role === "admin" ? "管理员" : "企业内部用户"}</small>
            </span>
          </div>
          {user.role === "admin" ? (
            <Link to="/admin" aria-label="打开管理后台">
              <Cog6ToothIcon aria-hidden="true" />
            </Link>
          ) : (
            <span className="sidebar-footer-placeholder" aria-hidden="true" />
          )}
          <button type="button" aria-label="退出登录" onClick={onLogout}>
            <ArrowRightStartOnRectangleIcon aria-hidden="true" />
          </button>
        </div>
      </aside>

      <main className="product-main user-main" id="workspace-main" tabIndex={-1}>
        <header className="product-topbar user-topbar">
          <div className="user-product-context">
            <RectangleGroupIcon aria-hidden="true" />
            <span>企业知识问答</span>
          </div>
          {isConversationRoute ? (
            <button
              className="citation-toggle"
              type="button"
              aria-label="切换引用来源"
              aria-expanded={citationDrawerState.open}
              aria-controls={citationDrawerState.available ? "citation-drawer" : undefined}
              disabled={!citationDrawerState.available}
              title={!citationDrawerState.available ? "当前对话没有引用来源" : undefined}
              onClick={() => window.dispatchEvent(new Event(TOGGLE_CITATIONS_EVENT))}
            >
              <RectangleGroupIcon aria-hidden="true" />
            </button>
          ) : null}
        </header>
        <Outlet />
      </main>
    </div>
  );
}
