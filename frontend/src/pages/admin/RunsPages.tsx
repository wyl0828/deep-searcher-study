import { ArrowLeftIcon, ArrowPathIcon, BeakerIcon, DocumentMagnifyingGlassIcon } from "@heroicons/react/24/outline";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { useUrlTableState } from "../../components/data-table";
import { EmptyState, ErrorState, LoadingState } from "../../components/states";
import {
  RiskBadge,
  StatusBadge,
  TrustStatusBadge,
  type RiskStatus,
  type TraceStageStatus,
  type TrustStatus,
} from "../../components/status";
import {
  getAdminRun,
  listAdminKnowledgeBases,
  listAdminRuns,
  type AdminRunItem,
  type Message,
} from "../../product-api";

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function trustBadgeStatus(value: Message["trust_status"]): TrustStatus {
  if (value === "fully_grounded") return "trusted";
  if (value === "partially_grounded") return "partial";
  if (value === "conflicting_evidence" || value === "insufficient_evidence") return "untrusted";
  return "unknown";
}

function riskBadgeStatus(value: Message["risk_level"]): RiskStatus {
  return value === "low" || value === "medium" || value === "high" ? value : "unknown";
}

function answerStatusBadge(value: string): TraceStageStatus {
  if (value === "succeeded") return "completed";
  if (value === "failed") return "failed";
  if (value === "cancelled") return "failed";
  return "partial";
}

function statusLabel(value: string): string {
  if (value === "succeeded") return "已完成";
  if (value === "failed") return "失败";
  if (value === "cancelled") return "已取消";
  return "处理中";
}

function answerModeLabel(item: AdminRunItem): string {
  const mode = item.answer_run?.answer_mode ?? item.message.answer_mode;
  const resolved = mode || (
    (Array.isArray(item.message.citations) && item.message.citations.length > 0) ||
    item.message.answer_state === "grounded" ||
    item.message.answer_state === "fully_grounded"
      ? "knowledge"
      : "chat"
  );
  if (resolved === "knowledge") return "企业知识";
  if (resolved === "web") return "联网";
  return "通用 AI";
}

function routingField(
  decision: Record<string, unknown> | null | undefined,
  keys: string[],
): string {
  for (const key of keys) {
    const value = decision?.[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
  }
  return "未记录";
}

function routingConfidence(decision: Record<string, unknown> | null | undefined): string {
  const value = decision?.confidence;
  if (typeof value !== "number") return "未记录";
  return value >= 0 && value <= 1 ? `${Math.round(value * 100)}%` : String(value);
}

type TraceStage = {
  key: string;
  label: string;
  status: TraceStageStatus;
  detail: string;
};

function traceStages(run: AdminRunItem): TraceStage[] {
  const persisted = run.answer_run?.stage_results;
  if (!Array.isArray(persisted) || persisted.length === 0) return [];
  const allowedStatuses: TraceStageStatus[] = ["completed", "failed", "partial", "unknown"];
  return persisted.flatMap((raw, index) => {
    const key = typeof raw.key === "string" ? raw.key : `stage-${index}`;
    const label = typeof raw.label === "string" ? raw.label : key;
    const status = allowedStatuses.includes(raw.status as TraceStageStatus)
      ? (raw.status as TraceStageStatus)
      : "unknown";
    const detail = typeof raw.detail === "string" ? raw.detail : "阶段结果已持久化，但没有可展示的说明。";
    return [{ key, label, status, detail }];
  });
}

export function AdminRunsPage() {
  const defaults = {
    page: 1,
    knowledge_base_id: "",
    status: "",
    trust_status: "",
    risk_level: "",
    feedback: "",
  };
  const { state, update, reset } = useUrlTableState(defaults);
  const knowledgeBases = useQuery({ queryKey: ["admin-knowledge-bases"], queryFn: listAdminKnowledgeBases });
  const runs = useQuery({
    queryKey: ["admin-runs", state],
    queryFn: () => listAdminRuns({
      page: state.page,
      page_size: 20,
      knowledge_base_id: state.knowledge_base_id || undefined,
      status: state.status || undefined,
      trust_status: state.trust_status || undefined,
      risk_level: state.risk_level || undefined,
      feedback: state.feedback || undefined,
    }),
  });
  const totalPages = runs.data ? Math.max(1, Math.ceil(runs.data.total / 20)) : 1;
  const hasFilters = Object.entries(state).some(([key, value]) => key !== "page" && value !== "");

  return (
    <section className="admin-page">
      <header className="admin-page-heading">
        <span className="eyebrow">回答质量</span>
        <h1>回答记录</h1>
        <p>先看问题、回答和反馈；需要调查时再下钻到真实持久化的执行详情。</p>
      </header>

      <section className="admin-card admin-table-card">
        <div className="admin-toolbar">
          <label><span>知识库</span><select aria-label="知识库" value={state.knowledge_base_id} onChange={(event) => update({ knowledge_base_id: event.target.value, page: 1 })}><option value="">全部知识库</option>{(knowledgeBases.data || []).map((knowledgeBase) => <option key={knowledgeBase.id} value={knowledgeBase.id}>{knowledgeBase.name}</option>)}</select></label>
          <label><span>回答状态</span><select aria-label="回答状态" value={state.status} onChange={(event) => update({ status: event.target.value, page: 1 })}><option value="">全部</option><option value="succeeded">已完成</option><option value="pending">未完成</option><option value="failed">失败</option></select></label>
          <label><span>可信度</span><select aria-label="可信度状态" value={state.trust_status} onChange={(event) => update({ trust_status: event.target.value, page: 1 })}><option value="">全部</option><option value="fully_grounded">证据充分</option><option value="partially_grounded">部分有证据</option><option value="conflicting_evidence">冲突证据</option><option value="insufficient_evidence">证据不足</option></select></label>
          <label><span>风险</span><select aria-label="风险状态" value={state.risk_level} onChange={(event) => update({ risk_level: event.target.value, page: 1 })}><option value="">全部</option><option value="low">低风险</option><option value="medium">中风险</option><option value="high">高风险</option></select></label>
          <label><span>反馈</span><select aria-label="反馈状态" value={state.feedback} onChange={(event) => update({ feedback: event.target.value, page: 1 })}><option value="">全部</option><option value="negative">有负反馈</option><option value="positive">有正反馈</option><option value="commented">有评论</option></select></label>
          <button className="secondary-button" type="button" onClick={reset} disabled={!hasFilters}>清除筛选</button>
          <button className="secondary-button" type="button" onClick={() => void runs.refetch()}><ArrowPathIcon aria-hidden="true" /> 刷新</button>
        </div>

        {runs.isLoading ? <LoadingState label="正在加载运行记录…" /> : null}
        {runs.error ? <ErrorState message={runs.error.message} /> : null}
        {!runs.isLoading && !runs.error && runs.data?.items.length === 0 ? <EmptyState title={hasFilters ? "没有匹配的回答记录" : "还没有回答记录"} description="回答记录只读取已持久化的消息和执行事实；临时 SSE 阶段不会被伪造成历史记录。" action={hasFilters ? <button className="secondary-button" type="button" onClick={reset}>查看全部</button> : undefined} /> : null}
        {!runs.isLoading && !runs.error && runs.data?.items.length ? <div className="admin-data-table-wrap"><table className="admin-data-table"><thead><tr><th>时间</th><th>用户</th><th>问题</th><th>知识范围</th><th>可信度</th><th>风险</th><th>反馈</th><th>状态</th></tr></thead><tbody>{runs.data.items.map((run) => <tr key={run.message.id}><td>{formatDate(run.message.created_at)}</td><td>{run.owner.display_name}</td><td><Link className="admin-object-link" to={`/admin/runs/${run.message.id}`}><BeakerIcon aria-hidden="true" /><span><strong>{run.question || run.conversation.title || "未命名问题"}</strong><small>{run.message.content.slice(0, 96) || "没有保存回答正文"}</small></span></Link></td><td>{run.knowledge_base?.name || `自动（${run.scope.knowledge_bases.length} 个知识域）`}</td><td><TrustStatusBadge status={trustBadgeStatus(run.message.trust_status)} /></td><td><RiskBadge status={riskBadgeStatus(run.message.risk_level)} /></td><td>{run.feedback.negative ? `${run.feedback.negative} 条负反馈` : "暂无负反馈"}</td><td><StatusBadge status={answerStatusBadge(run.answer_run?.status || run.message.status)} label={statusLabel(run.answer_run?.status || run.message.status)} /></td></tr>)}</tbody></table></div> : null}
        <div className="pagination"><button type="button" disabled={state.page <= 1} onClick={() => update({ page: Math.max(1, state.page - 1) })}>上一页</button><span>第 {state.page} / {totalPages} 页（共 {runs.data?.total ?? 0} 条）</span><button type="button" disabled={state.page >= totalPages} onClick={() => update({ page: state.page + 1 })}>下一页</button></div>
      </section>
    </section>
  );
}

function ExecutionScope({ item }: { item: AdminRunItem }) {
  return (
    <section className="admin-card trace-content-card">
      <div className="admin-section-heading"><div><h2>执行时知识范围</h2><p>这是回答执行时保存的历史事实，不代表用户当前仍拥有这些权限。</p></div><StatusBadge status={item.scope.mode === "auto" ? "completed" : "partial"} label={item.scope.mode === "auto" ? "自动范围" : "固定范围"} /></div>
      <div className="trace-scope-summary"><span>{item.scope.knowledge_bases.length ? item.scope.knowledge_bases.map((knowledgeBase) => knowledgeBase.name).join("、") : "未记录"}</span><small>Collection：{item.scope.collection_names.length ? item.scope.collection_names.join("、") : "未记录"}</small></div>
    </section>
  );
}

function ExecutionStages({ item }: { item: AdminRunItem }) {
  const stages = traceStages(item);
  const currentStage = item.answer_run?.current_stage;
  return (
    <section className="admin-card trace-card">
      <div className="admin-section-heading"><div><h2>执行阶段</h2><p>只展示 AnswerRun 中真实持久化的阶段；没有 instrumentation 时标记为未采集。</p></div></div>
      {currentStage ? <div className="trace-current-stage"><span>当前阶段</span><strong>{currentStage}</strong></div> : null}
      {stages.length ? <ol className="trace-timeline">{stages.map((stage) => <li key={stage.key} className={`trace-timeline-item trace-timeline-item--${stage.status}`}><div className="trace-timeline-marker" aria-hidden="true" /><div className="trace-timeline-content"><div><strong>{stage.label}</strong><StatusBadge status={stage.status} /></div><p>{stage.detail}</p></div></li>)}</ol> : <EmptyState title="未采集" description="当前 AnswerRun 没有保存可展示的阶段结果，不补写执行节点或耗时。" />}
    </section>
  );
}

function RequestLink({ item }: { item: AdminRunItem }) {
  return (
    <dl className="trace-request-details">
      <div><dt>request_id</dt><dd>{item.answer_run?.request_id || "未记录"}</dd></div>
      <div><dt>failure_code</dt><dd>{item.answer_run?.failure_code || "未记录"}</dd></div>
      <div><dt>系统状态</dt><dd><Link to="/admin/diagnostics">查看系统状态</Link></dd></div>
    </dl>
  );
}

function RoutingAudit({ item }: { item: AdminRunItem }) {
  const decision = item.answer_run?.routing_decision;
  return (
    <section className="admin-card trace-content-card">
      <div className="admin-section-heading">
        <div>
          <h2>路由与执行事实</h2>
          <p>只展示 AnswerRun 保存的路由字段；历史缺失值保留为未记录。</p>
        </div>
      </div>
      <dl className="trace-definition-list">
        <div><dt>路由模式</dt><dd>{answerModeLabel(item)}</dd></div>
        <div><dt>intent</dt><dd>{routingField(decision, ["intent", "query_type"])}</dd></div>
        <div><dt>source</dt><dd>{routingField(decision, ["source", "source_type"])}</dd></div>
        <div><dt>reason</dt><dd>{routingField(decision, ["reason", "route_reason"])}</dd></div>
        <div><dt>confidence</dt><dd>{routingConfidence(decision)}</dd></div>
        <div><dt>router 版本</dt><dd>{routingField(decision, ["router_version", "version"])}</dd></div>
        <div><dt>执行阶段</dt><dd>{item.answer_run?.current_stage || "未记录"}</dd></div>
        <div><dt>failure code</dt><dd>{item.answer_run?.failure_code || "未记录"}</dd></div>
      </dl>
    </section>
  );
}

export function AdminRunDetailPage() {
  const { messageId = "" } = useParams();
  const run = useQuery({ queryKey: ["admin-run", messageId], queryFn: () => getAdminRun(messageId), enabled: Boolean(messageId) });

  if (run.isLoading) return <LoadingState label="正在加载回答详情…" />;
  if (run.error) return <ErrorState message={run.error.message} />;
  if (!run.data) return <EmptyState title="没有找到这个回答记录" />;

  const item = run.data;
  const message = item.message;
  const runStatus = item.answer_run?.status || message.status;
  const isFailure = runStatus === "failed" || runStatus === "cancelled";
  const failureReason = message.content || (runStatus === "cancelled" ? "本次回答已停止。" : "问答服务没有完成本次查询。" );

  return (
    <section className="admin-page">
      <header className="admin-page-heading admin-detail-heading">
        <Link className="admin-back-link" to="/admin/runs"><ArrowLeftIcon aria-hidden="true" /> 返回回答记录</Link>
        <div className="admin-detail-title"><DocumentMagnifyingGlassIcon aria-hidden="true" /><div><span className="eyebrow">回答详情</span><h1>{item.question || item.conversation.title || "未命名问题"}</h1><p>{item.knowledge_base?.name || `自动知识范围（${item.scope.knowledge_bases.length} 个）`} · {item.owner.display_name} · {formatDate(message.created_at)}</p></div></div>
        <div className="admin-detail-actions"><StatusBadge status={answerStatusBadge(runStatus)} label={statusLabel(runStatus)} />{!isFailure ? <><TrustStatusBadge status={trustBadgeStatus(message.trust_status)} /><RiskBadge status={riskBadgeStatus(message.risk_level)} /></> : null}</div>
      </header>

      {isFailure ? (
        <section className="admin-card trace-failure-card">
          <div className="admin-section-heading"><div><span className="eyebrow">{runStatus === "cancelled" ? "回答已停止" : "回答失败"}</span><h2>{runStatus === "cancelled" ? "停止原因" : "失败原因"}</h2><p>失败记录只保留可诊断的执行事实，不渲染空的质量、反馈、声明或引用结论。</p></div><StatusBadge status={answerStatusBadge(runStatus)} label={statusLabel(runStatus)} /></div>
          <p className="trace-failure-reason">{failureReason}</p>
          <RequestLink item={item} />
        </section>
      ) : null}

      <ExecutionStages item={item} />
      <ExecutionScope item={item} />
      <RoutingAudit item={item} />

      {!isFailure ? (
        <>
          <div className="admin-dashboard-grid trace-detail-grid">
            <section className="admin-card trace-content-card"><div className="admin-section-heading"><div><h2>回答</h2><p>内容来自持久化 assistant message。</p></div></div><div className="trace-answer-copy">{message.content || "未记录"}</div></section>
            <section className="admin-card trace-content-card"><div className="admin-section-heading"><div><h2>可信度 / 风险</h2><p>缺失字段保留为未知，不转换成 0 或成功。</p></div></div><dl className="trace-definition-list"><div><dt>可信度状态</dt><dd><TrustStatusBadge status={trustBadgeStatus(message.trust_status)} /></dd></div><div><dt>策略动作</dt><dd>{message.policy_action || "未记录"}</dd></div><div><dt>初始风险</dt><dd><RiskBadge status={riskBadgeStatus(message.risk_level)} />{message.risk_factors?.length ? <small>{message.risk_factors.join("、")}</small> : null}</dd></div><div><dt>最终风险</dt><dd><RiskBadge status={riskBadgeStatus(item.answer_run?.effective_risk_level ?? null)} />{item.answer_run?.effective_risk_factors?.length ? <small>{item.answer_run.effective_risk_factors.join("、")}</small> : null}</dd></div><div><dt>查询类型</dt><dd>{message.query_type || "未记录"}</dd></div><div><dt>Provenance</dt><dd>{message.provenance_digest ? message.provenance_digest.slice(0, 24) + "…" : "未记录"}</dd></div><div><dt>用户反馈</dt><dd>{item.feedback.positive} 个赞 · {item.feedback.negative} 个踩 · {item.feedback.comment_count} 条评论</dd></div></dl></section>
          </div>

          <section className="admin-card trace-content-card trace-feedback-card"><div className="admin-section-heading"><div><h2>反馈分析</h2><p>只展示未取消的真实反馈；原因和评论用于定位回答质量问题。</p></div><span className="trace-feedback-summary">{item.feedback.positive} 个赞 · {item.feedback.negative} 个踩 · {item.feedback.comment_count} 条评论</span></div>{item.feedback_items.length ? <ul className="trace-feedback-list">{item.feedback_items.map((feedback, index) => <li key={`${feedback.created_at}-${index}`}><div><StatusBadge status={feedback.vote === -1 ? "failed" : feedback.vote === 1 ? "completed" : "unknown"} label={feedback.vote === -1 ? "负反馈" : feedback.vote === 1 ? "正反馈" : "未评分"} /><time dateTime={feedback.created_at}>{formatDate(feedback.created_at)}</time></div><strong>{feedback.reason || "未选择反馈原因"}</strong><p>{feedback.comment || "未填写补充评论。"}</p></li>)}</ul> : <EmptyState title="没有有效反馈" description="当前回答没有未取消的反馈记录，反馈样本不足。" />}</section>

          <div className="admin-dashboard-grid trace-detail-grid">
            <section className="admin-card trace-content-card"><div className="admin-section-heading"><div><h2>声明</h2><p>声明级核验字段来自 answer_claims。</p></div></div>{message.claims.length ? <ol className="trace-claims-list">{message.claims.map((claim) => <li key={claim.id}><div><strong>#{claim.index}</strong><StatusBadge status={claim.entailment_status} label={claim.entailment_status} /></div><p>{claim.text}</p><small>{claim.citation_indices.length} 条引用 · {claim.support_status}</small></li>)}</ol> : <EmptyState title="没有持久化声明" description="这不等于声明数量为 0；当前记录没有可展示的 claims 数据。" />}</section>
            <section className="admin-card trace-content-card"><div className="admin-section-heading"><div><h2>引用资料</h2><p>引用来自 citations 表，保留来源与可信标记。</p></div></div>{message.citations.length ? <ul className="trace-citations-list">{message.citations.map((citation) => <li key={citation.id}><div><strong>[{citation.index}] {citation.display_name}</strong><StatusBadge status={citation.trusted ? "completed" : "failed"} label={citation.trusted ? "可信来源" : "未确认来源"} /></div><p>{citation.text || "没有保存证据文本"}</p><small>{citation.source_type}{citation.page_number ? ` · 第 ${citation.page_number} 页` : ""}{citation.source_domain ? ` · ${citation.source_domain}` : ""}</small></li>)}</ul> : <EmptyState title="没有持久化引用" description="当前回答没有可展示的 citations 数据。" />}</section>
          </div>
        </>
      ) : null}
    </section>
  );
}
