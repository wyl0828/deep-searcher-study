import { ArrowLeftIcon, ArrowPathIcon, CircleStackIcon, DocumentTextIcon, PlusIcon } from "@heroicons/react/24/outline";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useUrlTableState } from "../../components/data-table";
import { EmptyState, ErrorState, LoadingState } from "../../components/states";
import { IndexStatusBadge, KnowledgeHealthBadge, type KnowledgeHealthStatus, StatusBadge } from "../../components/status";
import {
  createKnowledgeBase,
  getAdminKnowledgeBaseAccessSummary,
  getAdminKnowledgeHealth,
  listAdminKnowledgeBaseDocuments,
  listAdminKnowledgeBases,
  retryAdminDocument,
  uploadDocument,
  type AdminKnowledgeBase,
  type KnowledgeBase,
  type ProductDocument,
} from "../../product-api";
import "../../workspace.css";

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function indexStatus(value: KnowledgeBase["index_status"]) {
  return value === "verified" ? "verified" : "needs_rebuild";
}

function healthStatus(value: AdminKnowledgeBase["health_status"]): KnowledgeHealthStatus {
  return value || "unknown";
}

function processingLabel(status: string) {
  return ({ queued: "排队中", processing: "处理中", succeeded: "已完成", dead_letter: "处理失败" } as Record<string, string>)[status] || status;
}

export function AdminKnowledgeListPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const createMutation = useMutation({
    mutationFn: () => createKnowledgeBase({ name: name.trim(), description: description.trim() }),
    onSuccess: () => {
      setName(""); setDescription(""); setShowCreate(false);
      void queryClient.invalidateQueries({ queryKey: ["admin-knowledge-bases"] });
    },
  });
  const { state, update, reset } = useUrlTableState({ q: "", index: "", health: "" });
  const query = useQuery({ queryKey: ["admin-knowledge-bases"], queryFn: listAdminKnowledgeBases });
  const items = (query.data || []).filter((item) => {
    if (item.name === "__legacy__" || item.name.includes("__legacy__")) return false;
    const haystack = `${item.name} ${item.description}`.toLowerCase();
    return (!state.q || haystack.includes(state.q.toLowerCase()))
      && (!state.index || indexStatus(item.index_status) === state.index)
      && (!state.health || healthStatus(item.health_status) === state.health);
  });
  const hasFilters = Boolean(state.q || state.index || state.health);
  return (
    <section className="admin-page">
      <header className="admin-page-heading admin-page-heading--with-action">
        <div><span className="eyebrow">知识管理</span><h1>知识库</h1><p>管理员创建知识库、上传企业资料并查看文档处理和健康状态。</p></div>
        <button className="product-primary-button" type="button" onClick={() => setShowCreate((value) => !value)}><PlusIcon aria-hidden="true" /> 新建知识库</button>
      </header>
      {showCreate ? <section className="admin-card admin-create-card"><form className="admin-inline-form" onSubmit={(event) => { event.preventDefault(); createMutation.mutate(); }}><label><span>知识库名称</span><input value={name} maxLength={40} required placeholder="例如：公司制度" onChange={(event) => setName(event.target.value)} /></label><label><span>描述</span><input value={description} maxLength={200} placeholder="可选" onChange={(event) => setDescription(event.target.value)} /></label><button className="product-primary-button" type="submit" disabled={createMutation.isPending || !name.trim()}>{createMutation.isPending ? "创建中…" : "创建"}</button></form>{createMutation.error ? <ErrorState message={createMutation.error.message} /> : null}</section> : null}
      <section className="admin-card admin-table-card">
        <div className="admin-toolbar"><label className="admin-search-field"><span>搜索知识库</span><input value={state.q} placeholder="名称、描述" onChange={(event) => update({ q: event.target.value }, { replace: true })} /></label><label><span>索引状态</span><select value={state.index} onChange={(event) => update({ index: event.target.value })}><option value="">全部</option><option value="verified">已验证</option><option value="needs_rebuild">需要重建</option></select></label><label><span>健康结论</span><select value={state.health} onChange={(event) => update({ health: event.target.value })}><option value="">全部</option><option value="healthy">健康</option><option value="warning">预警</option><option value="critical">严重</option><option value="partial">部分数据</option><option value="unknown">尚无结论</option></select></label><button className="secondary-button" type="button" onClick={reset} disabled={!hasFilters}>清除筛选</button></div>
        {query.isLoading ? <LoadingState label="正在加载知识库…" /> : null}
        {query.error ? <ErrorState message={query.error.message} /> : null}
        {!query.isLoading && !query.error && items.length === 0 ? <EmptyState title={hasFilters ? "没有匹配的知识库" : "还没有知识库"} description={hasFilters ? "调整筛选条件后再试。" : "点击右上角“新建知识库”，然后上传企业资料。"} action={hasFilters ? <button className="secondary-button" type="button" onClick={reset}>查看全部</button> : undefined} /> : null}
        {items.length > 0 ? <div className="admin-data-table-wrap"><table className="admin-data-table"><thead><tr><th>知识库</th><th>文档</th><th>索引状态</th><th>健康结论</th><th>更新时间</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><Link className="admin-object-link" to={`/admin/knowledge/${item.id}`}><CircleStackIcon aria-hidden="true" /><span><strong>{item.name}</strong><small>{item.description || "暂无描述"}</small></span></Link></td><td><strong>{item.ready_document_count}</strong><small> / {item.document_count} 可问答</small></td><td><IndexStatusBadge status={indexStatus(item.index_status)} /></td><td><KnowledgeHealthBadge status={healthStatus(item.health_status)} />{item.health_score != null ? <small className="admin-health-list-score">{item.health_score.toFixed(1)} 分</small> : null}</td><td>{formatDate(item.updated_at)}</td></tr>)}</tbody></table></div> : null}
      </section>
    </section>
  );
}

export function AdminKnowledgeDetailPage() {
  const { knowledgeBaseId = "" } = useParams();
  const queryClient = useQueryClient();
  const [uploadError, setUploadError] = useState("");
  const knowledgeBases = useQuery({ queryKey: ["admin-knowledge-bases"], queryFn: listAdminKnowledgeBases });
  const documents = useQuery({ queryKey: ["admin-knowledge-documents", knowledgeBaseId], queryFn: () => listAdminKnowledgeBaseDocuments(knowledgeBaseId), enabled: Boolean(knowledgeBaseId) });
  const health = useQuery({ queryKey: ["admin-knowledge-health", knowledgeBaseId], queryFn: () => getAdminKnowledgeHealth(knowledgeBaseId), enabled: Boolean(knowledgeBaseId) });
  const access = useQuery({ queryKey: ["admin-knowledge-access-summary", knowledgeBaseId], queryFn: () => getAdminKnowledgeBaseAccessSummary(knowledgeBaseId), enabled: Boolean(knowledgeBaseId) });
  const uploadMutation = useMutation({ mutationFn: (file: File) => uploadDocument(knowledgeBaseId, file, { published_at: null, effective_at: null, superseded_at: null, version_family: null }), onSuccess: () => { setUploadError(""); void queryClient.invalidateQueries({ queryKey: ["admin-knowledge-documents", knowledgeBaseId] }); void queryClient.invalidateQueries({ queryKey: ["admin-knowledge-bases"] }); }, onError: (error) => setUploadError(error.message) });
  const retryMutation = useMutation({ mutationFn: (documentId: string) => retryAdminDocument(documentId), onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["admin-knowledge-documents", knowledgeBaseId] }); void queryClient.invalidateQueries({ queryKey: ["admin-knowledge-bases"] }); }, onError: (error) => setUploadError(error.message) });
  const { state, update } = useUrlTableState({ q: "" });
  const knowledgeBase = knowledgeBases.data?.find((item) => item.id === knowledgeBaseId);
  const items = (documents.data || []).filter((document) => document.display_name.toLowerCase().includes(state.q.toLowerCase()));
  if (knowledgeBases.isLoading || documents.isLoading) return <section className="admin-page"><LoadingState label="正在加载知识库详情…" /></section>;
  if (knowledgeBases.error) return <section className="admin-page"><ErrorState message={knowledgeBases.error.message} /></section>;
  if (documents.error || !knowledgeBase) return <section className="admin-page"><EmptyState title={knowledgeBase ? "文档暂时不可用" : "没有找到这个知识库"} action={<Link className="secondary-button" to="/admin/knowledge">返回知识库</Link>} /></section>;
  const currentHealth = health.data?.current;
  const currentHealthStatus = currentHealth?.status === "partial" ? "partial" : currentHealth?.level || "unknown";
  return (
    <section className="admin-page">
      <header className="admin-page-heading admin-detail-heading"><Link className="admin-back-link" to="/admin/knowledge"><ArrowLeftIcon aria-hidden="true" /> 返回知识库</Link><div className="admin-detail-title"><CircleStackIcon aria-hidden="true" /><div><span className="eyebrow">知识库详情</span><h1>{knowledgeBase.name}</h1><p>{knowledgeBase.description || "暂无描述"}</p></div></div><div className="admin-detail-actions"><IndexStatusBadge status={indexStatus(knowledgeBase.index_status)} /><span className="admin-detail-hint">文档处理状态在下方查看</span></div></header>
      <section className="admin-card admin-health-card"><div className="admin-section-heading"><div><h2>知识健康</h2><p>健康结论来自当前知识库真实数据；样本不足时保留为未知或部分数据。</p></div><KnowledgeHealthBadge status={currentHealthStatus} /></div>{health.isLoading ? <LoadingState label="正在加载健康结论…" /> : null}{health.error ? <ErrorState message={health.error.message} /> : null}{currentHealth ? <div className="admin-health-summary"><div className="admin-health-score"><span>综合健康分</span><strong>{currentHealth.overall_score == null ? "尚无结论" : currentHealth.overall_score.toFixed(1)}</strong><small>公式 {currentHealth.formula_version} · {currentHealth.status === "complete" ? "可评估" : "样本不足"}</small></div><div className="admin-health-dimensions"><div><span>数据健康</span><strong>{currentHealth.data_score == null ? "尚无结论" : currentHealth.data_score.toFixed(1)}</strong></div><div><span>检索健康</span><strong>{currentHealth.retrieval_score == null ? "尚无结论" : currentHealth.retrieval_score.toFixed(1)}</strong></div><div><span>回答可信度</span><strong>{currentHealth.trust_score == null ? "尚无结论" : currentHealth.trust_score.toFixed(1)}</strong></div></div></div> : null}</section>
      <div className="admin-detail-metrics"><div><strong>{knowledgeBase.document_count}</strong><span>文档总数</span></div><div><strong>{knowledgeBase.ready_document_count}</strong><span>可用于问答</span></div><div><strong>{knowledgeBase.conversation_count}</strong><span>关联对话</span></div><div><KnowledgeHealthBadge status={currentHealthStatus} /><span>健康结论</span></div></div>
      <section className="admin-card admin-table-card"><div className="admin-section-heading"><div><h2>文档</h2><p>状态来自文档处理生命周期；失败文档可以在这里重新处理。</p></div><label className="secondary-button admin-upload-button"><PlusIcon aria-hidden="true" /> 上传文档<input type="file" accept=".pdf,.txt,.md,.docx,.xlsx,.csv" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) uploadMutation.mutate(file); event.currentTarget.value = ""; }} /></label><label className="admin-search-field"><span>搜索文档</span><input value={state.q} placeholder="文件名" onChange={(event) => update({ q: event.target.value }, { replace: true })} /></label></div>{items.length === 0 ? <EmptyState title={state.q ? "没有匹配的文档" : "还没有文档"} /> : <div className="admin-data-table-wrap"><table className="admin-data-table"><thead><tr><th>文件</th><th>状态</th><th>处理</th><th>治理信息</th><th>更新时间</th></tr></thead><tbody>{items.map((document: ProductDocument) => <tr key={document.id}><td><span className="admin-object-link"><DocumentTextIcon aria-hidden="true" /><span><strong>{document.display_name}</strong><small>{document.id}</small></span></span></td><td><StatusBadge status={document.status} />{document.error ? <small className="admin-error-copy">{document.error.message}</small> : null}</td><td>{document.processing ? <span>{processingLabel(document.processing.status)}{document.processing.error ? ` · ${document.processing.error.message}` : ""}</span> : "尚无处理记录"}{document.status === "failed" ? <button className="secondary-button table-action-button" type="button" onClick={() => retryMutation.mutate(document.id)} disabled={retryMutation.isPending}><ArrowPathIcon aria-hidden="true" /> 重试</button> : null}</td><td>{document.version_family || document.published_at || document.effective_at ? "已填写" : "尚未填写"}</td><td>{formatDate(document.updated_at)}</td></tr>)}</tbody></table></div>}{uploadMutation.isPending ? <p className="admin-inline-note">正在上传并创建文档处理任务…</p> : null}{uploadError ? <ErrorState message={uploadError} /> : null}</section>
      <section className="admin-card admin-table-card admin-permissions-card"><div className="admin-section-heading"><div><h2>访问范围</h2><p>这里是反向查看；权限修改统一在用户管理的全员、部门和用户入口完成。</p></div></div>{access.isLoading ? <LoadingState label="正在加载访问范围…" /> : null}{access.error ? <ErrorState message={access.error.message} /> : null}{access.data ? <div className="admin-access-summary"><div><span>全员可访问</span><strong>{access.data.is_company_wide ? "是" : "否"}</strong></div><div><span>授权部门</span><strong>{access.data.departments.length ? access.data.departments.map((item) => item.name).join("、") : "暂无"}</strong></div><div><span>个人额外用户</span><strong>{access.data.direct_users.length ? access.data.direct_users.map((item) => `${item.display_name}（@${item.username}）`).join("、") : "暂无"}</strong></div><div><span>可访问用户</span><strong>{access.data.accessible_user_count} 人</strong></div></div> : null}</section>
    </section>
  );
}
