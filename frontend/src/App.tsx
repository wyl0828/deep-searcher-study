import {
  ArrowPathIcon,
  ArrowRightStartOnRectangleIcon,
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
  ShieldCheckIcon,
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
  Fragment,
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
  type CitationSpan,
  type DocumentGovernanceInput,
  type GroupMemberItem,
  type KnowledgeBase,
  type KnowledgeBaseMember,
  type MemberGroup,
  type Message,
  type OperationAuditLog,
  type ProductDocument,
  type ProductUser,
  type QueryStageEvent,
  type Workspace,
  type WorkspaceMember,
  ProductApiError,
  addKnowledgeBaseMember,
  addWorkspaceGroupMember,
  addWorkspaceMember,
  createConversation,
  createKnowledgeBase,
  createKnowledgeHealthSnapshot,
  createUser,
  createWorkspaceGroup,
  createWorkspace,
  deleteConversation,
  deleteDocument,
  deleteKnowledgeBase,
  deleteWorkspaceGroup,
  getConversation,
  getAuthStatus,
  getKnowledgeBase,
  getKnowledgeHealth,
  getKnowledgeHealthTrend,
  getWorkspaceGroup,
  listAuditLogs,
  listConversations,
  listDocuments,
  listKnowledgeBases,
  listKnowledgeBaseMembers,
  listKnowledgeHealthHistory,
  listWorkspaceGroups,
  listWorkspaceMembers,
  listWorkspaces,
  listUsers,
  loginWorkspace,
  logoutWorkspace,
  reindexKnowledgeBase,
  retryDocument,
  removeWorkspaceMember,
  removeKnowledgeBaseMember,
  removeWorkspaceGroupMember,
  runKnowledgeHealthActions,
  setCurrentKnowledgeBase,
  setupWorkspace,
  streamMessage,
  uploadDocument,
  updateDocumentGovernanceMetadata,
  updateKnowledgeBaseMemberRole,
  updateWorkspaceGroupRole,
  updateWorkspaceMemberRole,
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

function displayAnswerContent(content: string) {
  let insideCodeFence = false;
  return content
    .split("\n")
    .map((line) => {
      if (line.trimStart().startsWith("```")) {
        insideCodeFence = !insideCodeFence;
        return line;
      }
      if (insideCodeFence) return line;
      return line
        .replace(
          /\[\s*(?:E[1-9]\d{0,2}|CONFLICT\s*:\s*E[1-9]\d{0,2}(?:\s*,\s*E[1-9]\d{0,2})+)\s*\]/gi,
          "",
        )
        .replace(/[ \t]+([。！？!?.,])/g, "$1");
    })
    .join("\n");
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
    case "contextualization":
      if (stage.data.fallback_used) return "上下文理解失败，已直接检索当前问题";
      if (stage.data.depends_on_history) {
        return `已结合 ${stage.data.history_turn_count} 条历史消息理解追问`;
      }
      return "当前问题可独立检索";
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
  const [workspaceId, setWorkspaceId] = useState("");
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => listWorkspaces(),
    enabled: open,
  });
  const workspaceItems = workspaces.data?.items || [];
  const mutation = useMutation({
    mutationFn: (input: { name: string; description: string }) =>
      createKnowledgeBase({
        ...input,
        workspace_id: workspaceId || undefined,
      }),
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
          <label className="form-field">
            <span>所属工作区</span>
            <select
              value={workspaceId}
              onChange={(event) => setWorkspaceId(event.target.value)}
              disabled={mutation.isPending}
            >
              <option value="">默认个人工作区</option>
              {workspaceItems.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}（
                  {workspace.role === "owner"
                    ? "所有者"
                    : workspace.role === "editor"
                      ? "可编辑"
                      : "只读"}
                  ）
                </option>
              ))}
            </select>
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


function CreateWorkspaceDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const mutation = useMutation({
    mutationFn: (input: { name: string; description: string }) =>
      createWorkspace(input),
    onSuccess: onCreated,
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
        aria-labelledby="create-workspace-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">团队与共享</span>
            <h2 id="create-workspace-title">新建工作区</h2>
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
            <span>工作区名称</span>
            <input
              data-dialog-initial-focus
              value={name}
              maxLength={40}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：研发团队"
            />
          </label>
          <label className="form-field">
            <span>描述（可选）</span>
            <textarea
              value={description}
              maxLength={200}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="说明这个工作区的用途"
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
              {mutation.isPending ? "正在创建…" : "创建工作区"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}


function WorkspaceMemberRow({
  workspace,
  member,
  canManage,
}: {
  workspace: Workspace;
  member: WorkspaceMember;
  canManage: boolean;
}) {
  const queryCache = useQueryClient();
  const [role, setRole] = useState(member.role);
  const invalidate = async () => {
    await Promise.all([
      queryCache.invalidateQueries({ queryKey: ["workspace-members", workspace.id] }),
      queryCache.invalidateQueries({ queryKey: ["workspaces"] }),
    ]);
  };
  const changeRole = useMutation({
    mutationFn: (nextRole: "editor" | "viewer") =>
      updateWorkspaceMemberRole(workspace.id, member.user_id, nextRole),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => removeWorkspaceMember(workspace.id, member.user_id),
    onSuccess: invalidate,
  });
  const isOwner = member.role === "owner";
  return (
    <li className="workspace-member-row">
      <div className="workspace-member-id">
        <strong>{member.display_name}</strong>
        <span>@{member.username}</span>
      </div>
      <span className="workspace-role-badge">
        {isOwner ? "所有者" : member.role === "editor" ? "可编辑" : "只读"}
      </span>
      {canManage && !isOwner ? (
        <div className="workspace-member-actions">
          <select
            value={role}
            disabled={changeRole.isPending}
            onChange={(event) => {
              const next = event.target.value as "editor" | "viewer";
              setRole(next);
              changeRole.mutate(next);
            }}
          >
            <option value="editor">可编辑</option>
            <option value="viewer">只读</option>
          </select>
          <button
            className="secondary-button workspace-remove-member"
            type="button"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            移除
          </button>
        </div>
      ) : null}
    </li>
  );
}


function WorkspaceCard({ workspace }: { workspace: Workspace }) {
  const queryCache = useQueryClient();
  const [showMembers, setShowMembers] = useState(false);
  const [username, setUsername] = useState("");
  const [newRole, setNewRole] = useState<"editor" | "viewer">("viewer");
  const canManage = workspace.role === "owner";
  const members = useQuery({
    queryKey: ["workspace-members", workspace.id],
    queryFn: () => listWorkspaceMembers(workspace.id),
    enabled: showMembers,
  });
  const addMember = useMutation({
    mutationFn: (input: { username: string; role: "editor" | "viewer" }) =>
      addWorkspaceMember(workspace.id, input),
    onSuccess: async () => {
      setUsername("");
      await Promise.all([
        queryCache.invalidateQueries({ queryKey: ["workspace-members", workspace.id] }),
        queryCache.invalidateQueries({ queryKey: ["workspaces"] }),
      ]);
    },
  });
  return (
    <section className="workspace-card">
      <div className="workspace-card-head">
        <div>
          <h2>{workspace.name}</h2>
          <p>{workspace.description || "暂无描述"}</p>
        </div>
        <span className="workspace-role-badge">
          {workspace.role === "owner"
            ? "所有者"
            : workspace.role === "editor"
              ? "可编辑"
              : "只读"}
        </span>
      </div>
      <div className="workspace-stats">
        <span>{workspace.member_count} 位成员</span>
        <span>{workspace.knowledge_base_count} 个知识库</span>
      </div>
      <button
        className="secondary-button"
        type="button"
        onClick={() => setShowMembers((value) => !value)}
      >
        {showMembers ? "收起成员" : "查看成员"}
      </button>
      {showMembers ? (
        <div className="workspace-members">
          {canManage ? (
            <form
              className="workspace-add-member"
              onSubmit={(event) => {
                event.preventDefault();
                if (username.trim()) addMember.mutate({ username: username.trim(), role: newRole });
              }}
            >
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="输入用户名"
                maxLength={32}
              />
              <select
                value={newRole}
                onChange={(event) =>
                  setNewRole(event.target.value as "editor" | "viewer")
                }
              >
                <option value="editor">可编辑</option>
                <option value="viewer">只读</option>
              </select>
              <button
                className="product-primary-button"
                type="submit"
                disabled={!username.trim() || addMember.isPending}
              >
                添加成员
              </button>
            </form>
          ) : null}
          {members.isLoading ? <LoadingState label="正在加载成员…" /> : null}
          {members.error ? <ErrorState message={members.error.message} /> : null}
          <ul className="workspace-member-list">
            {(members.data?.items || []).map((member) => (
              <WorkspaceMemberRow
                key={member.user_id}
                workspace={workspace}
                member={member}
                canManage={canManage}
              />
            ))}
          </ul>
          {canManage ? <WorkspaceGroupsPanel workspace={workspace} /> : null}
        </div>
      ) : null}
    </section>
  );
}


function WorkspacesPage() {
  const queryCache = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => listWorkspaces(),
  });
  return (
    <div className="page-container">
      <div className="page-heading">
        <div>
          <span className="eyebrow">团队与共享</span>
          <h1>工作区</h1>
        </div>
        <button
          className="product-primary-button"
          type="button"
          onClick={() => setShowCreate(true)}
        >
          新建工作区
        </button>
      </div>
      {workspaces.isLoading ? <LoadingState label="正在加载工作区…" /> : null}
      {workspaces.error ? <ErrorState message={workspaces.error.message} /> : null}
      <div className="workspace-grid">
        {(workspaces.data?.items || []).map((workspace) => (
          <WorkspaceCard key={workspace.id} workspace={workspace} />
        ))}
      </div>
      <CreateWorkspaceDialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={() => {
          setShowCreate(false);
          void queryCache.invalidateQueries({ queryKey: ["workspaces"] });
        }}
      />
    </div>
  );
}


function KnowledgeBaseMembersPanel({
  knowledgeBaseId,
}: {
  knowledgeBaseId: string;
}) {
  const queryCache = useQueryClient();
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<"editor" | "viewer">("viewer");
  const members = useQuery({
    queryKey: ["kb-members", knowledgeBaseId],
    queryFn: () => listKnowledgeBaseMembers(knowledgeBaseId),
  });
  const invalidate = async () => {
    await queryCache.invalidateQueries({
      queryKey: ["kb-members", knowledgeBaseId],
    });
  };
  const add = useMutation({
    mutationFn: (input: { username: string; role: "editor" | "viewer" }) =>
      addKnowledgeBaseMember(knowledgeBaseId, input),
    onSuccess: async () => {
      setUsername("");
      await invalidate();
    },
  });
  const changeRole = useMutation({
    mutationFn: ({
      userId,
      nextRole,
    }: {
      userId: string;
      nextRole: "editor" | "viewer";
    }) => updateKnowledgeBaseMemberRole(knowledgeBaseId, userId, nextRole),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (userId: string) =>
      removeKnowledgeBaseMember(knowledgeBaseId, userId),
    onSuccess: invalidate,
  });
  return (
    <section className="health-list-block kb-members-panel">
      <h3>成员覆盖</h3>
      <p className="kb-member-hint">
        仅影响该知识库的读写权限；工作区 owner 始终拥有全部权限。
      </p>
      <form
        className="workspace-add-member"
        onSubmit={(event) => {
          event.preventDefault();
          if (username.trim()) {
            add.mutate({ username: username.trim(), role });
          }
        }}
      >
        <input
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          placeholder="输入用户名"
          maxLength={32}
        />
        <select
          value={role}
          onChange={(event) =>
            setRole(event.target.value as "editor" | "viewer")
          }
        >
          <option value="editor">可编辑</option>
          <option value="viewer">只读</option>
        </select>
        <button
          className="product-primary-button"
          type="submit"
          disabled={!username.trim() || add.isPending}
        >
          添加覆盖
        </button>
      </form>
      {add.error ? <ErrorState message={add.error.message} /> : null}
      {members.isLoading ? <LoadingState label="正在加载成员…" /> : null}
      {members.error ? <ErrorState message={members.error.message} /> : null}
      <ul className="workspace-member-list">
        {(members.data?.items || []).map((member) => (
          <li key={member.user_id} className="workspace-member-row">
            <div className="workspace-member-id">
              <strong>{member.display_name}</strong>
              <span>@{member.username}</span>
            </div>
            <span className="workspace-role-badge">
              {member.role === "editor" ? "可编辑" : "只读"}
            </span>
            <div className="workspace-member-actions">
              <select
                value={member.role}
                disabled={changeRole.isPending}
                onChange={(event) =>
                  changeRole.mutate({
                    userId: member.user_id,
                    nextRole: event.target.value as "editor" | "viewer",
                  })
                }
              >
                <option value="editor">可编辑</option>
                <option value="viewer">只读</option>
              </select>
              <button
                className="secondary-button workspace-remove-member"
                type="button"
                disabled={remove.isPending}
                onClick={() => remove.mutate(member.user_id)}
              >
                移除
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}


function WorkspaceGroupsPanel({ workspace }: { workspace: Workspace }) {
  const queryCache = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState<"editor" | "viewer">("viewer");
  const [expanded, setExpanded] = useState<string | null>(null);
  const groups = useQuery({
    queryKey: ["workspace-groups", workspace.id],
    queryFn: () => listWorkspaceGroups(workspace.id),
  });
  const invalidate = async () => {
    await Promise.all([
      queryCache.invalidateQueries({
        queryKey: ["workspace-groups", workspace.id],
      }),
      queryCache.invalidateQueries({ queryKey: ["workspaces"] }),
    ]);
  };
  const create = useMutation({
    mutationFn: (input: { name: string; role: "editor" | "viewer" }) =>
      createWorkspaceGroup(workspace.id, input),
    onSuccess: async () => {
      setName("");
      await invalidate();
    },
  });
  const changeRole = useMutation({
    mutationFn: ({
      groupId,
      nextRole,
    }: {
      groupId: string;
      nextRole: "editor" | "viewer";
    }) => updateWorkspaceGroupRole(workspace.id, groupId, nextRole),
    onSuccess: invalidate,
  });
  const removeGroup = useMutation({
    mutationFn: (groupId: string) => deleteWorkspaceGroup(workspace.id, groupId),
    onSuccess: invalidate,
  });
  const detail = useQuery({
    queryKey: ["workspace-group", workspace.id, expanded],
    queryFn: () => getWorkspaceGroup(workspace.id, expanded as string),
    enabled: Boolean(expanded),
  });
  const addMember = useMutation({
    mutationFn: (input: { groupId: string; username: string }) =>
      addWorkspaceGroupMember(workspace.id, input.groupId, {
        username: input.username,
      }),
    onSuccess: async () => {
      await queryCache.invalidateQueries({
        queryKey: ["workspace-group", workspace.id, expanded],
      });
    },
  });
  const removeMember = useMutation({
    mutationFn: ({ groupId, userId }: { groupId: string; userId: string }) =>
      removeWorkspaceGroupMember(workspace.id, groupId, userId),
    onSuccess: async () => {
      await queryCache.invalidateQueries({
        queryKey: ["workspace-group", workspace.id, expanded],
      });
    },
  });
  return (
    <div className="workspace-groups">
      <h4>成员组</h4>
      <form
        className="workspace-add-member"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) create.mutate({ name: name.trim(), role });
        }}
      >
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="组名称"
          maxLength={40}
        />
        <select
          value={role}
          onChange={(event) =>
            setRole(event.target.value as "editor" | "viewer")
          }
        >
          <option value="editor">可编辑</option>
          <option value="viewer">只读</option>
        </select>
        <button
          className="product-primary-button"
          type="submit"
          disabled={!name.trim() || create.isPending}
        >
          创建组
        </button>
      </form>
      {create.error ? <ErrorState message={create.error.message} /> : null}
      <ul className="workspace-member-list">
        {(groups.data?.items || []).map((group) => (
          <li key={group.id} className="workspace-group-row">
            <div className="workspace-group-head">
              <strong>{group.name}</strong>
              <span className="workspace-role-badge">
                {group.role === "editor" ? "可编辑" : "只读"} · {group.member_count} 人
              </span>
              <div className="workspace-member-actions">
                <select
                  value={group.role}
                  disabled={changeRole.isPending}
                  onChange={(event) =>
                    changeRole.mutate({
                      groupId: group.id,
                      nextRole: event.target.value as "editor" | "viewer",
                    })
                  }
                >
                  <option value="editor">可编辑</option>
                  <option value="viewer">只读</option>
                </select>
                <button
                  className="secondary-button workspace-remove-member"
                  type="button"
                  disabled={removeGroup.isPending}
                  onClick={() => removeGroup.mutate(group.id)}
                >
                  删除
                </button>
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setExpanded(expanded === group.id ? null : group.id)}
                >
                  {expanded === group.id ? "收起" : "成员"}
                </button>
              </div>
            </div>
            {expanded === group.id ? (
              <div className="workspace-group-members">
                <form
                  className="workspace-add-member"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const input = form.querySelector("input") as HTMLInputElement;
                    if (input.value.trim()) {
                      addMember.mutate({ groupId: group.id, username: input.value.trim() });
                      input.value = "";
                    }
                  }}
                >
                  <input placeholder="输入用户名" maxLength={32} />
                  <button
                    className="product-primary-button"
                    type="submit"
                    disabled={addMember.isPending}
                  >
                    添加成员
                  </button>
                </form>
                {addMember.error ? (
                  <ErrorState message={addMember.error.message} />
                ) : null}
                <ul className="workspace-member-list">
                  {(detail.data?.members || []).map((member) => (
                    <li key={member.user_id} className="workspace-member-row">
                      <div className="workspace-member-id">
                        <strong>{member.username}</strong>
                        <span>@{member.username}</span>
                      </div>
                      <button
                        className="secondary-button workspace-remove-member"
                        type="button"
                        disabled={removeMember.isPending}
                        onClick={() =>
                          removeMember.mutate({
                            groupId: group.id,
                            userId: member.user_id,
                          })
                        }
                      >
                        移除
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}


function AuthScreen({ setupRequired }: { setupRequired: boolean }) {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      setupRequired
        ? setupWorkspace({
            username,
            password,
            display_name: displayName,
          })
        : loginWorkspace({ username, password }),
    onSuccess: (user) => {
      queryClient.setQueryData(["auth-status"], {
        setup_required: false,
        authenticated: true,
        user,
      });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <img src="/deepsearcher-logo.png" alt="DeepSearcher" />
        <div className="auth-heading">
          <span>{setupRequired ? "首次使用" : "欢迎回来"}</span>
          <h1 id="auth-title">
            {setupRequired ? "创建工作台管理员" : "登录学习工作台"}
          </h1>
          <p>
            {setupRequired
              ? "首位用户将成为管理员，并接管升级前已有的知识库与对话。"
              : "登录后只会看到属于你的知识库、文档与对话。"}
          </p>
        </div>
        <form onSubmit={submit}>
          {setupRequired ? (
            <label>
              <span>显示名称</span>
              <input
                autoComplete="name"
                maxLength={50}
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="例如：小林"
                required
              />
            </label>
          ) : null}
          <label>
            <span>用户名</span>
            <input
              autoCapitalize="none"
              autoComplete="username"
              maxLength={32}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="3–32 位字母、数字或 ._-"
              required
            />
          </label>
          <label>
            <span>密码</span>
            <input
              autoComplete={setupRequired ? "new-password" : "current-password"}
              minLength={setupRequired ? 10 : 1}
              maxLength={128}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={setupRequired ? "至少 10 个字符" : "输入密码"}
              required
            />
          </label>
          {mutation.error ? <ErrorState message={mutation.error.message} /> : null}
          <button
            className="product-primary-button auth-submit"
            type="submit"
            disabled={
              mutation.isPending ||
              !username.trim() ||
              !password ||
              (setupRequired && !displayName.trim())
            }
          >
            {mutation.isPending
              ? "正在提交…"
              : setupRequired
                ? "创建管理员并进入"
                : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}

function WorkspaceLayout({
  user,
  onLogout,
}: {
  user: ProductUser;
  onLogout: () => void;
}) {
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
          <NavLink to="/workspaces">
            <RectangleGroupIcon aria-hidden="true" />
            工作区
          </NavLink>
          {user.role === "admin" ? (
            <NavLink className="admin-users-link" to="/users">
              <ShieldCheckIcon aria-hidden="true" />
              用户管理
            </NavLink>
          ) : null}
          {user.role === "admin" ? (
            <NavLink className="admin-users-link" to="/admin/audit">
              <ClipboardDocumentIcon aria-hidden="true" />
              操作审计
            </NavLink>
          ) : null}
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
          <span title={user.username}>{user.display_name}</span>
          {user.role === "admin" ? (
            <Link to="/console" aria-label="打开学习控制台">
              <Cog6ToothIcon aria-hidden="true" />
            </Link>
          ) : (
            <span aria-hidden="true" />
          )}
          <button type="button" aria-label="退出登录" onClick={onLogout}>
            <ArrowRightStartOnRectangleIcon aria-hidden="true" />
          </button>
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
  selectedSpan,
  focusSelected,
  modal,
  onSelect,
  onClose,
}: {
  citations: Citation[];
  selectedId: string | null;
  selectedSpan: CitationSpan | null;
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
  onCitation: (
    citation: Citation,
    trigger: HTMLButtonElement,
    span?: CitationSpan,
  ) => void;
  onRegenerate: () => void;
  queryPending: boolean;
  regenerating: boolean;
  queryDisabled: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<"helpful" | "unhelpful" | null>(null);
  const displayedContent = displayAnswerContent(message.content);
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
        <div className="markdown-answer">
          <ReactMarkdown>{displayedContent}</ReactMarkdown>
        </div>
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
                      <ReactMarkdown>{claim.text}</ReactMarkdown>
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
                                  ? `独立来源不足（${check.actual ?? 0}/${check.required ?? 0}）`
                                  : `证据数量不足（${check.actual ?? 0}/${check.required ?? 0}）`}
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
        {provenance ? (
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
                  Trust v{provenance.policy.trust_contract_version} · Policy v
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
  const [selectedCitationSpan, setSelectedCitationSpan] =
    useState<CitationSpan | null>(null);
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
    setDrawerOpen(!compactViewport);
    setFocusDrawerSelection(false);
    setSelectedCitationSpan(null);
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
            selectedSpan={selectedCitationSpan}
            focusSelected={focusDrawerSelection}
            modal={compactViewport}
            onSelect={(citation) => {
              setFocusDrawerSelection(false);
              setSelectedCitation(citation);
              setSelectedCitationSpan(null);
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

function DocumentTemporalDialog({
  open,
  document,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  open: boolean;
  document: ProductDocument | null;
  pending: boolean;
  error: Error | null;
  onClose: () => void;
  onSubmit: (file: File | null, temporal: DocumentGovernanceInput) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [publishedAt, setPublishedAt] = useState("");
  const [effectiveAt, setEffectiveAt] = useState("");
  const [supersededAt, setSupersededAt] = useState("");
  const [versionFamily, setVersionFamily] = useState("");
  const [validationError, setValidationError] = useState("");
  const titleId = useId();
  const editing = Boolean(document);
  const dialogRef = useModalFocus({
    open,
    onDismiss: onClose,
    dismissBlocked: pending,
  });
  useEffect(() => {
    if (!open) return;
    setFile(null);
    setPublishedAt(document?.published_at || "");
    setEffectiveAt(document?.effective_at || "");
    setSupersededAt(document?.superseded_at || "");
    setVersionFamily(document?.version_family || "");
    setValidationError("");
  }, [document, open]);

  if (!open) return null;
  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={() => {
        if (!pending) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="dialog-card document-temporal-dialog"
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">文档治理</span>
            <h2 id={titleId}>{editing ? "编辑文档治理信息" : "上传 PDF"}</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label="关闭"
            disabled={pending}
          >
            <XMarkIcon aria-hidden="true" />
          </button>
        </div>
        <p className="dialog-description">
          文档系列用于比较同一制度的不同版本；发布日期用于解释“今天、明天、下周”。系统不会使用文件名、修改时间或上传时间推断这些信息。
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!editing && !file) {
              setValidationError("请选择一个 PDF 文件。");
              return;
            }
            if (
              supersededAt &&
              ((publishedAt && supersededAt < publishedAt) ||
                (effectiveAt && supersededAt < effectiveAt))
            ) {
              setValidationError("失效日期不能早于发布日期或生效日期。");
              return;
            }
            setValidationError("");
            onSubmit(file, {
              published_at: publishedAt || null,
              effective_at: effectiveAt || null,
              superseded_at: supersededAt || null,
              version_family: versionFamily.trim() || null,
            });
          }}
        >
          {!editing ? (
            <label className="form-field">
              <span>PDF 文件</span>
              <input
                data-dialog-initial-focus
                type="file"
                accept="application/pdf,.pdf"
                onChange={(event) => setFile(event.target.files?.[0] || null)}
              />
            </label>
          ) : (
            <p className="document-temporal-file">{document?.display_name}</p>
          )}
          <label className="form-field">
            <span>文档系列标识（推荐）</span>
            <input
              type="text"
              value={versionFamily}
              maxLength={128}
              placeholder="例如：travel-expense-policy"
              onChange={(event) => setVersionFamily(event.target.value)}
            />
            <small>同一制度的历次版本填写相同标识，最新版本只在同系列内比较</small>
          </label>
          <div className="document-temporal-fields">
            <label className="form-field">
              <span>发布日期（推荐）</span>
              <input
                type="date"
                data-dialog-initial-focus={editing || undefined}
                value={publishedAt}
                onChange={(event) => setPublishedAt(event.target.value)}
              />
              <small>文档内相对时间的唯一锚点</small>
            </label>
            <label className="form-field">
              <span>生效日期（可选）</span>
              <input
                type="date"
                value={effectiveAt}
                onChange={(event) => setEffectiveAt(event.target.value)}
              />
            </label>
            <label className="form-field">
              <span>失效/被替代日期（可选）</span>
              <input
                type="date"
                value={supersededAt}
                onChange={(event) => setSupersededAt(event.target.value)}
              />
            </label>
          </div>
          {validationError ? <p className="dialog-warning">{validationError}</p> : null}
          {error ? <ErrorState message={error.message} /> : null}
          <div className="dialog-actions">
            <button
              className="secondary-button"
              type="button"
              onClick={onClose}
              disabled={pending}
            >
              取消
            </button>
            <button className="product-primary-button" type="submit" disabled={pending}>
              {pending
                ? editing
                  ? "正在保存并排队…"
                  : "正在上传…"
                : editing
                  ? "保存并更新索引"
                  : "上传并处理"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}


function scoreTone(score: number | null): string {
  if (score === null) return "muted";
  if (score >= 80) return "good";
  if (score >= 60) return "warn";
  return "bad";
}

function HealthScoreBar({
  label,
  value,
  detail,
}: {
  label: string;
  value: number | null;
  detail?: string;
}) {
  const tone = scoreTone(value);
  return (
    <div className={`health-score health-score--${tone}`}>
      <div className="health-score-head">
        <span>{label}</span>
        <strong>{value === null ? "样本不足" : value.toFixed(0)}</strong>
      </div>
      <div
        className="health-score-track"
        role="img"
        aria-label={`${label} ${value === null ? "样本不足" : value.toFixed(0)} 分`}
      >
        <div
          className="health-score-fill"
          style={{
            width: value === null ? 0 : `${Math.max(0, Math.min(100, value))}%`,
          }}
        />
      </div>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

function KnowledgeHealthDialog({
  open,
  knowledgeBaseId,
  onClose,
  onUpload,
}: {
  open: boolean;
  knowledgeBaseId: string;
  onClose: () => void;
  onUpload?: () => void;
}) {
  const titleId = useId();
  const queryCache = useQueryClient();
  const createSnapshot = useMutation({
    mutationFn: () => createKnowledgeHealthSnapshot(knowledgeBaseId),
    onSuccess: async () => {
      await Promise.all([
        queryCache.invalidateQueries({
          queryKey: ["knowledge-health", knowledgeBaseId],
        }),
        queryCache.invalidateQueries({
          queryKey: ["knowledge-health-history", knowledgeBaseId],
        }),
        queryCache.invalidateQueries({
          queryKey: ["knowledge-health-trend", knowledgeBaseId],
        }),
      ]);
    },
  });
  const runActions = useMutation({
    mutationFn: (actions: string[]) =>
      runKnowledgeHealthActions(knowledgeBaseId, actions),
    onSuccess: async () => {
      await Promise.all([
        queryCache.invalidateQueries({
          queryKey: ["knowledge-health", knowledgeBaseId],
        }),
        queryCache.invalidateQueries({
          queryKey: ["knowledge-health-history", knowledgeBaseId],
        }),
        queryCache.invalidateQueries({
          queryKey: ["knowledge-health-trend", knowledgeBaseId],
        }),
      ]);
    },
  });
  const handleClose = () => {
    createSnapshot.reset();
    onClose();
  };
  const dialogRef = useModalFocus({
    open,
    onDismiss: handleClose,
    dismissBlocked: false,
  });
  const health = useQuery({
    queryKey: ["knowledge-health", knowledgeBaseId],
    queryFn: () => getKnowledgeHealth(knowledgeBaseId),
    enabled: open,
  });
  const history = useQuery({
    queryKey: ["knowledge-health-history", knowledgeBaseId],
    queryFn: () => listKnowledgeHealthHistory(knowledgeBaseId),
    enabled: open,
  });
  const trend = useQuery({
    queryKey: ["knowledge-health-trend", knowledgeBaseId],
    queryFn: () => getKnowledgeHealthTrend(knowledgeBaseId),
    enabled: open,
  });
  if (!open) return null;

  const current = health.data?.current;
  const snapshot = health.data?.snapshot;
  const items = history.data || [];
  const dataMetrics = current?.metrics.data;
  const retrievalMetrics = current?.metrics.retrieval;
  const trustMetrics = current?.metrics.trust;

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={handleClose}>
      <section
        ref={dialogRef}
        className="dialog-card knowledge-health-dialog"
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">知识健康 · 路线图 v0.4</span>
            <h2 id={titleId}>知识健康</h2>
          </div>
          <button className="icon-button" type="button" onClick={handleClose} aria-label="关闭">
            <XMarkIcon aria-hidden="true" />
          </button>
        </div>
        {health.isLoading ? <LoadingState label="正在计算健康分…" /> : null}
        {health.error ? <ErrorState message={health.error.message} /> : null}
        {current ? (
          <>
            <p className="dialog-description">
              健康分不是黑盒分数：每个指标都来自知识库的真实数据。公式版本
              {current.formula_version}，权重为数据 40% / 检索 30% / 回答可信度 30%。
              {current.status === "partial"
                ? " 部分维度样本不足，综合总分暂不计算。"
                : null}
            </p>
            <div className={`health-overall health-overall--${scoreTone(current.overall_score)}`}>
              <div>
                <span>综合健康分</span>
                <strong>
                  {current.overall_score === null ? "—" : current.overall_score.toFixed(1)}
                </strong>
              </div>
              <div className="health-badges">
                <span className="health-status-badge">
                  {current.status === "complete" ? "可评估" : "样本不足"}
                </span>
                {current.level ? (
                  <span className={`health-level-badge health-level-badge--${current.level}`}>
                    {current.level === "healthy"
                      ? "健康"
                      : current.level === "warning"
                        ? "偏低"
                        : "严重"}
                  </span>
                ) : null}
              </div>
            </div>
            <div className="health-scores">
              <HealthScoreBar
                label="数据健康"
                value={current.data_score}
                detail={`${dataMetrics?.ready_documents ?? 0}/${dataMetrics?.total_documents ?? 0} 文档可用`}
              />
              <HealthScoreBar
                label="检索健康"
                value={current.retrieval_score}
                detail={`${retrievalMetrics?.message_sample_count ?? 0} 条回答样本`}
              />
              <HealthScoreBar
                label="回答可信度"
                value={current.trust_score}
                detail={`${trustMetrics?.claim_count ?? 0} 条声明`}
              />
            </div>
            {snapshot ? (
              <p className="health-last-snapshot">
                最近快照：{new Date(snapshot.created_at).toLocaleString()}，总分{" "}
                {snapshot.overall_score === null ? "—" : snapshot.overall_score.toFixed(1)}
              </p>
            ) : (
              <p className="health-last-snapshot health-last-snapshot--missing">
                还没有保存过快照。点击“生成快照”记录当前状态，便于后续对比变化。
              </p>
            )}
            <details className="health-details-block">
              <summary>指标明细</summary>
              <div className="health-metric-grid">
                <div>
                  <h4>数据健康</h4>
                  <dl>
                    <div><dt>文档总数</dt><dd>{dataMetrics?.total_documents ?? 0}</dd></div>
                    <div><dt>可用于问答</dt><dd>{dataMetrics?.ready_documents ?? 0}</dd></div>
                    <div><dt>处理失败</dt><dd>{dataMetrics?.failed_documents ?? 0}</dd></div>
                    <div><dt>空文档</dt><dd>{dataMetrics?.empty_documents ?? 0}</dd></div>
                    <div><dt>总页数</dt><dd>{dataMetrics?.total_pages ?? 0}</dd></div>
                    <div><dt>索引已验证</dt><dd>{dataMetrics?.index_verified ? "是" : "否"}</dd></div>
                    <div><dt>业务日期覆盖率</dt><dd>{((dataMetrics?.temporal_metadata_ratio ?? 0) * 100).toFixed(0)}%</dd></div>
                  </dl>
                </div>
                <div>
                  <h4>检索健康</h4>
                  <dl>
                    <div><dt>回答样本</dt><dd>{retrievalMetrics?.message_sample_count ?? 0}</dd></div>
                    <div><dt>引用覆盖率</dt><dd>{((retrievalMetrics?.citation_coverage_rate ?? 0) * 100).toFixed(0)}%</dd></div>
                    <div><dt>平均引用数</dt><dd>{(retrievalMetrics?.avg_citations_per_message ?? 0).toFixed(2)}</dd></div>
                    <div><dt>拒答率</dt><dd>{((retrievalMetrics?.refusal_rate ?? 0) * 100).toFixed(0)}%</dd></div>
                    <div><dt>证据不足率</dt><dd>{((retrievalMetrics?.insufficient_evidence_rate ?? 0) * 100).toFixed(0)}%</dd></div>
                    <div><dt>联网引用占比</dt><dd>{((retrievalMetrics?.web_source_rate ?? 0) * 100).toFixed(0)}%</dd></div>
                  </dl>
                </div>
                <div>
                  <h4>回答可信度</h4>
                  <dl>
                    <div><dt>声明总数</dt><dd>{trustMetrics?.claim_count ?? 0}</dd></div>
                    <div><dt>支持率</dt><dd>{((trustMetrics?.supported_claim_rate ?? 0) * 100).toFixed(0)}%</dd></div>
                    <div><dt>冲突率</dt><dd>{((trustMetrics?.conflicting_claim_rate ?? 0) * 100).toFixed(0)}%</dd></div>
                    <div><dt>无效引用率</dt><dd>{((trustMetrics?.invalid_citation_rate ?? 0) * 100).toFixed(0)}%</dd></div>
                    <div><dt>一致性冲突率</dt><dd>{((trustMetrics?.consistency_issue_rate ?? 0) * 100).toFixed(0)}%</dd></div>
                    <div><dt>语义矛盾率</dt><dd>{((trustMetrics?.entailment_contradiction_rate ?? 0) * 100).toFixed(0)}%</dd></div>
                  </dl>
                </div>
              </div>
            </details>
            {current.deductions.length ? (
              <section className="health-list-block" aria-labelledby="health-deductions-title">
                <h3 id="health-deductions-title">扣分原因</h3>
                <ul>
                  {current.deductions.map((deduction) => (
                    <li key={deduction.code}>
                      <strong>{deduction.reason}</strong>
                      <span>{deduction.impact}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {current.actions.length ? (
              <section className="health-list-block" aria-labelledby="health-actions-title">
                <h3 id="health-actions-title">建议动作</h3>
                <ul>
                  {current.actions.map((action) => {
                    const result = runActions.data?.results.find(
                      (item) => item.code === action.code,
                    );
                    return (
                      <li key={action.code}>
                        <strong>{action.action}</strong>
                        <span>{action.priority === "high" ? "优先处理" : "可选"}</span>
                        <button
                          className="health-action-button"
                          type="button"
                          disabled={runActions.isPending}
                          onClick={() => {
                            if (action.code === "UPLOAD_DOCUMENTS" && onUpload) {
                              onUpload();
                              return;
                            }
                            runActions.mutate([action.code]);
                          }}
                        >
                          {action.code === "UPLOAD_DOCUMENTS" ? "去上传" : "执行"}
                        </button>
                        {result ? (
                          <span
                            className={
                              "health-action-result health-action-result--" + result.status
                            }
                          >
                            {result.message}
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
                {runActions.isError ? (
                  <ErrorState message={runActions.error.message} />
                ) : null}
              </section>
            ) : null}
            {items.length ? (
              <section className="health-list-block" aria-labelledby="health-history-title">
                <h3 id="health-history-title">快照历史</h3>
                <ul className="health-history">
                  {items.slice(0, 6).map((item, index) => {
                    const previous = items[index + 1];
                    const delta =
                      previous &&
                      item.overall_score !== null &&
                      previous.overall_score !== null
                        ? item.overall_score - previous.overall_score
                        : null;
                    return (
                      <li key={item.id}>
                        <span>{new Date(item.created_at).toLocaleString()}</span>
                        <strong>
                          {item.overall_score === null ? "—" : item.overall_score.toFixed(1)}
                        </strong>
                        {delta === null ? null : (
                          <span
                            className={
                              delta > 0
                                ? "health-delta--up"
                                : delta < 0
                                  ? "health-delta--down"
                                  : ""
                            }
                          >
                            {delta > 0 ? `+${delta.toFixed(1)}` : delta.toFixed(1)}
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            ) : null}
            {trend.data && trend.data.items.length ? (
              <section className="health-list-block" aria-labelledby="health-trend-title">
                <h3 id="health-trend-title">
                  健康趋势（最近 {trend.data.items.length} 次快照）
                </h3>
                <div
                  className="health-trend"
                  role="img"
                  aria-label="综合健康分趋势"
                >
                  {trend.data.items.slice(-10).map((item) => (
                    <div
                      key={item.created_at}
                      className="health-trend-col"
                      title={
                        "".concat(
                          new Date(item.created_at).toLocaleString(),
                          "：",
                          item.overall === null ? "—" : item.overall.toFixed(0),
                          " 分",
                        )
                      }
                    >
                      <div className="health-trend-bar-wrap">
                        <div
                          className={
                            "health-trend-bar health-trend-bar--" +
                            (item.level ?? "unknown")
                          }
                          style={{
                            height:
                              item.overall === null
                                ? "4%"
                                : Math.max(4, Math.min(100, item.overall)) + "%",
                          }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
            {createSnapshot.isSuccess ? (
              <p className="health-notice" role="status">
                快照已生成并记录。
              </p>
            ) : null}
            {createSnapshot.error ? (
              <ErrorState message={createSnapshot.error.message} />
            ) : null}
            <div className="dialog-actions">
              <button className="secondary-button" type="button" onClick={handleClose}>
                关闭
              </button>
              <button
                className="product-primary-button"
                type="button"
                disabled={createSnapshot.isPending}
                onClick={() => createSnapshot.mutate()}
              >
                {createSnapshot.isPending ? "正在生成…" : "生成快照"}
              </button>
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}


function KnowledgeDetailPage() {
  const { knowledgeBaseId = "" } = useParams();
  const navigate = useNavigate();
  const queryCache = useQueryClient();
  const [documentToDelete, setDocumentToDelete] = useState<ProductDocument | null>(null);
  const [showDeleteKnowledgeBase, setShowDeleteKnowledgeBase] = useState(false);
  const [showTemporalDialog, setShowTemporalDialog] = useState(false);
  const [showHealthDialog, setShowHealthDialog] = useState(false);
  const [documentToEdit, setDocumentToEdit] = useState<ProductDocument | null>(null);
  const knowledgeBase = useQuery({
    queryKey: ["knowledge-base", knowledgeBaseId],
    queryFn: () => getKnowledgeBase(knowledgeBaseId),
  });
  const allKnowledgeBases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => listKnowledgeBases(),
  });
  const myRole = allKnowledgeBases.data?.find(
    (item) => item.id === knowledgeBaseId,
  )?.role;
  const canWrite = myRole !== "viewer";
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
    mutationFn: ({ file, temporal }: { file: File; temporal: DocumentGovernanceInput }) =>
      uploadDocument(knowledgeBaseId, file, temporal),
    onSuccess: async () => {
      await Promise.all([
        queryCache.invalidateQueries({ queryKey: ["documents", knowledgeBaseId] }),
        queryCache.invalidateQueries({ queryKey: ["knowledge-base", knowledgeBaseId] }),
        queryCache.invalidateQueries({ queryKey: ["knowledge-bases"] }),
      ]);
      setShowTemporalDialog(false);
    },
  });
  const updateTemporal = useMutation({
    mutationFn: ({
      documentId,
      temporal,
    }: {
      documentId: string;
      temporal: DocumentGovernanceInput;
    }) => updateDocumentGovernanceMetadata(documentId, temporal),
    onSuccess: async () => {
      await Promise.all([
        queryCache.invalidateQueries({ queryKey: ["documents", knowledgeBaseId] }),
        queryCache.invalidateQueries({ queryKey: ["knowledge-base", knowledgeBaseId] }),
      ]);
      setShowTemporalDialog(false);
      setDocumentToEdit(null);
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
          {canWrite ? (
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
          ) : null}
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
          {canWrite ? (
            <button
              className="product-primary-button"
              type="button"
              onClick={() => {
                upload.reset();
                setDocumentToEdit(null);
                setShowTemporalDialog(true);
              }}
              disabled={upload.isPending}
            >
              <PaperClipIcon aria-hidden="true" />
              {upload.isPending ? "正在上传…" : "上传 PDF"}
            </button>
          ) : null}
          <button
            className="secondary-button"
            type="button"
            onClick={() => setShowHealthDialog(true)}
          >
            <ShieldCheckIcon aria-hidden="true" />
            知识健康
          </button>
        </div>
      </div>

      {myRole === "owner" ? (
        <KnowledgeBaseMembersPanel knowledgeBaseId={knowledgeBaseId} />
      ) : null}

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
        {knowledgeBase.data.document_count && canWrite ? (
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
                      {document.version_family || document.published_at || document.effective_at || document.superseded_at ? (
                        <small className="document-temporal-summary">
                          {document.version_family ? `系列 ${document.version_family}` : "未设置文档系列"}
                          {document.published_at ? ` · 发布 ${document.published_at}` : " · 未设置发布日期"}
                          {document.effective_at ? ` · 生效 ${document.effective_at}` : ""}
                          {document.superseded_at ? ` · 失效 ${document.superseded_at}` : ""}
                        </small>
                      ) : (
                        <small className="document-temporal-summary document-temporal-summary--missing">
                          未设置可信发布日期
                        </small>
                      )}
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
                    <button
                      className="text-button"
                      type="button"
                      disabled={document.status === "processing"}
                      title={
                        document.status === "processing"
                          ? "文档处理完成后才能修改治理信息"
                          : "编辑文档系列和业务日期"
                      }
                      onClick={() => {
                        updateTemporal.reset();
                        setDocumentToEdit(document);
                        setShowTemporalDialog(true);
                      }}
                    >
                      治理信息
                    </button>
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
            <button
              className="product-primary-button"
              type="button"
              onClick={() => {
                upload.reset();
                setDocumentToEdit(null);
                setShowTemporalDialog(true);
              }}
            >
              上传 PDF
            </button>
          </div>
        )}
      </section>
      <DocumentTemporalDialog
        open={showTemporalDialog}
        document={documentToEdit}
        pending={documentToEdit ? updateTemporal.isPending : upload.isPending}
        error={
          (documentToEdit ? updateTemporal.error : upload.error) as Error | null
        }
        onClose={() => {
          if (upload.isPending || updateTemporal.isPending) return;
          setShowTemporalDialog(false);
          setDocumentToEdit(null);
        }}
        onSubmit={(file, temporal) => {
          if (documentToEdit) {
            updateTemporal.mutate({ documentId: documentToEdit.id, temporal });
          } else if (file) {
            upload.mutate({ file, temporal });
          }
        }}
      />
      <KnowledgeHealthDialog
        open={showHealthDialog}
        knowledgeBaseId={knowledgeBaseId}
        onClose={() => setShowHealthDialog(false)}
        onUpload={() => {
          setShowHealthDialog(false);
          setShowTemporalDialog(true);
        }}
      />
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

function AdminUsersPage() {
  const queryClient = useQueryClient();
  const users = useQuery({ queryKey: ["admin-users"], queryFn: listUsers });
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const mutation = useMutation({
    mutationFn: () =>
      createUser({ username, display_name: displayName, password, role }),
    onSuccess: () => {
      setUsername("");
      setDisplayName("");
      setPassword("");
      setRole("member");
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  return (
    <div className="workspace-page admin-users-page">
      <header className="page-heading">
        <div>
          <span className="eyebrow">管理员</span>
          <h1>用户管理</h1>
          <p>创建独立账号；每位用户只能访问自己的知识库、文档与对话。</p>
        </div>
      </header>
      <div className="admin-users-grid">
        <section className="workspace-card">
          <h2>添加用户</h2>
          <form
            className="admin-user-form"
            onSubmit={(event) => {
              event.preventDefault();
              mutation.mutate();
            }}
          >
            <label>
              <span>显示名称</span>
              <input
                value={displayName}
                maxLength={50}
                onChange={(event) => setDisplayName(event.target.value)}
                required
              />
            </label>
            <label>
              <span>用户名</span>
              <input
                value={username}
                maxLength={32}
                autoCapitalize="none"
                onChange={(event) => setUsername(event.target.value)}
                required
              />
            </label>
            <label>
              <span>初始密码</span>
              <input
                value={password}
                type="password"
                minLength={10}
                maxLength={128}
                autoComplete="new-password"
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
            <label>
              <span>角色</span>
              <select
                value={role}
                onChange={(event) => setRole(event.target.value as "admin" | "member")}
              >
                <option value="member">普通成员</option>
                <option value="admin">管理员</option>
              </select>
            </label>
            {mutation.error ? <ErrorState message={mutation.error.message} /> : null}
            <button
              className="product-primary-button"
              type="submit"
              disabled={
                mutation.isPending ||
                !username.trim() ||
                !displayName.trim() ||
                password.length < 10
              }
            >
              {mutation.isPending ? "正在创建…" : "创建用户"}
            </button>
          </form>
        </section>
        <section className="workspace-card">
          <h2>现有用户</h2>
          {users.isLoading ? <LoadingState /> : null}
          {users.error ? <ErrorState message={users.error.message} /> : null}
          <ul className="admin-user-list">
            {users.data?.map((item) => (
              <li key={item.id}>
                <UserCircleIcon aria-hidden="true" />
                <span>
                  <strong>{item.display_name}</strong>
                  <small>@{item.username}</small>
                </span>
                <i>{item.role === "admin" ? "管理员" : "成员"}</i>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

function AdminAuditPage() {
  const pageSize = 20;
  const [page, setPage] = useState(1);
  const [bizType, setBizType] = useState("");
  const [operationType, setOperationType] = useState("");
  const [operatorName, setOperatorName] = useState("");
  const [success, setSuccess] = useState<"" | "true" | "false">("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["admin-audit-logs", page, bizType, operationType, operatorName, success],
    queryFn: () =>
      listAuditLogs({
        page,
        page_size: pageSize,
        biz_type: bizType || undefined,
        operation_type: operationType || undefined,
        operator_name: operatorName || undefined,
        success:
          success === "true" ? true : success === "false" ? false : undefined,
      }),
  });

  const totalPages = query.data
    ? Math.max(1, Math.ceil(query.data.total / pageSize))
    : 1;

  return (
    <div className="workspace-page admin-users-page">
      <header className="page-heading">
        <div>
          <span className="eyebrow">管理员</span>
          <h1>操作审计</h1>
          <p>记录谁在什么时间改了哪些权限与配置，含变更前后快照与差异。</p>
        </div>
      </header>
      <section className="workspace-card">
        <div className="audit-filters">
          <label>
            <span>业务类型</span>
            <input
              value={bizType}
              placeholder="如 workspace_member"
              onChange={(event) => {
                setBizType(event.target.value);
                setPage(1);
              }}
            />
          </label>
          <label>
            <span>操作类型</span>
            <input
              value={operationType}
              placeholder="如 SET_MEMBER_ROLE"
              onChange={(event) => {
                setOperationType(event.target.value);
                setPage(1);
              }}
            />
          </label>
          <label>
            <span>操作人</span>
            <input
              value={operatorName}
              placeholder="显示名称"
              onChange={(event) => {
                setOperatorName(event.target.value);
                setPage(1);
              }}
            />
          </label>
          <label>
            <span>结果</span>
            <select
              value={success}
              onChange={(event) => {
                setSuccess(event.target.value as "" | "true" | "false");
                setPage(1);
              }}
            >
              <option value="">全部</option>
              <option value="true">成功</option>
              <option value="false">失败</option>
            </select>
          </label>
        </div>

        {query.isLoading ? (
          <p className="empty-state">加载中…</p>
        ) : query.isError ? (
          <div className="empty-state">
            <p>加载失败：{query.error?.message || "未知错误"}</p>
            <button type="button" onClick={() => void query.refetch()}>
              重试
            </button>
          </div>
        ) : (
          <table className="audit-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>操作</th>
                <th>业务</th>
                <th>操作人</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {(query.data?.items ?? []).map((log: OperationAuditLog) => (
                <Fragment key={log.id}>
                  <tr
                    className={log.success ? "" : "audit-row-failed"}
                    onClick={() =>
                      setExpanded(expanded === log.id ? null : log.id)
                    }
                  >
                    <td>{new Date(log.created_at).toLocaleString()}</td>
                    <td>
                      <span className="audit-action">{log.action_desc}</span>
                      <span className="audit-operation">{log.operation_type}</span>
                    </td>
                    <td>
                      {log.biz_type}
                      <span className="audit-biz-id">{log.biz_id}</span>
                    </td>
                    <td>
                      {log.operator_name || log.operator_id}
                      {log.ip ? <span className="audit-ip">{log.ip}</span> : null}
                    </td>
                    <td>
                      {log.success
                        ? "成功"
                        : `失败：${log.error_message || "未知错误"}`}
                    </td>
                  </tr>
                  {expanded === log.id ? (
                    <tr className="audit-detail-row">
                      <td colSpan={5}>
                        <div className="audit-snapshot-grid">
                          <div>
                            <h4>变更前</h4>
                            <pre>{JSON.stringify(log.before_snapshot, null, 2)}</pre>
                          </div>
                          <div>
                            <h4>变更后</h4>
                            <pre>{JSON.stringify(log.after_snapshot, null, 2)}</pre>
                          </div>
                          <div>
                            <h4>差异</h4>
                            <pre>{JSON.stringify(log.change_diff, null, 2)}</pre>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}

        <div className="pagination">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            上一页
          </button>
          <span>
            第 {page} / {totalPages} 页（共 {query.data?.total ?? 0} 条）
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage((current) => current + 1)}
          >
            下一页
          </button>
        </div>
      </section>
    </div>
  );
}


function ConsoleRoute() {
  return <ConsoleApp />;
}

function ProductRouter({
  user,
  onLogout,
}: {
  user: ProductUser;
  onLogout: () => void;
}) {
  return (
    <Routes>
      <Route element={<WorkspaceLayout user={user} onLogout={onLogout} />}>
        <Route index element={<NewChatPage />} />
        <Route path="chat/:conversationId" element={<ChatPage />} />
        <Route path="workspaces" element={<WorkspacesPage />} />
        <Route path="knowledge" element={<KnowledgeListPage />} />
        <Route path="knowledge/:knowledgeBaseId" element={<KnowledgeDetailRoute />} />
        {user.role === "admin" ? (
          <Route path="users" element={<AdminUsersPage />} />
        ) : null}
        {user.role === "admin" ? (
          <Route path="admin/audit" element={<AdminAuditPage />} />
        ) : null}
      </Route>
      {user.role === "admin" ? (
        <Route path="console" element={<ConsoleRoute />} />
      ) : null}
    </Routes>
  );
}

function AuthenticatedWorkspace() {
  const queryClient = useQueryClient();
  const auth = useQuery({
    queryKey: ["auth-status"],
    queryFn: getAuthStatus,
    retry: false,
  });
  const logout = useMutation({
    mutationFn: logoutWorkspace,
    onSuccess: () => {
      queryClient.clear();
      queryClient.setQueryData(["auth-status"], {
        setup_required: false,
        authenticated: false,
        user: null,
      });
    },
  });

  if (auth.isLoading) return <LoadingState label="正在检查登录状态…" />;
  if (auth.error) return <ErrorState message={auth.error.message} />;
  if (!auth.data?.authenticated || !auth.data.user) {
    return <AuthScreen setupRequired={Boolean(auth.data?.setup_required)} />;
  }
  return (
    <BrowserRouter>
      <ProductRouter user={auth.data.user} onLogout={() => logout.mutate()} />
    </BrowserRouter>
  );
}

export function App() {
  return (
    <QueryClientProvider client={workspaceQueryClient}>
      <AuthenticatedWorkspace />
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
