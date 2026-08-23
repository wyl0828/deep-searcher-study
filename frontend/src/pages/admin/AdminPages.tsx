import { ArrowPathIcon } from "@heroicons/react/24/outline";
import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";

import {
  type DashboardKpi,
  type OperationAuditLog,
  getDashboardOverview,
  getDashboardTrends,
  listAuditLogs,
} from "../../product-api";
import "../../workspace.css";

import { useUrlTableState } from "../../components/data-table";
import { ErrorState, LoadingState } from "../../components/states";
import { KnowledgeHealthBadge, StatusBadge } from "../../components/status";

function metricValue(
  metric: { value: number | null; sample_count: number },
  format: (value: number) => string,
) {
  return metric.value == null ? "样本不足" : format(metric.value);
}

export function AdminDashboardPage() {
  const overview = useQuery({ queryKey: ["admin-dashboard-overview"], queryFn: getDashboardOverview });
  const [days, setDays] = useState<number>(7);
  const trends = useQuery({ queryKey: ["admin-dashboard-trends", days], queryFn: () => getDashboardTrends(days) });

  if (overview.isLoading) return <section className="admin-page"><LoadingState label="正在加载运营概览…" /></section>;
  if (overview.error || !overview.data) return <section className="admin-page"><ErrorState message={overview.error?.message || "运营数据暂时不可用。"} /><button className="secondary-button" type="button" onClick={() => void overview.refetch()}>重试</button></section>;

  const kpis = overview.data.kpis;
  const distribution = overview.data.health_distribution;
  const total = distribution.healthy + distribution.warning + distribution.critical + distribution.partial + distribution.unknown;
  const series = trends.data?.series || [];
  const hasTrendActivity = series.some((item) => item.data.some((point) => point.value > 0));
  const maxValue = Math.max(1, ...series.flatMap((item) => item.data.map((point) => point.value)));

  const renderKpi = (label: string, kpi?: DashboardKpi & { ready?: number | null; failed?: number | null }) => (
    <div className="dashboard-kpi" key={label}>
      <span className="dashboard-kpi-label">{label}</span>
      <strong>{kpi?.value ?? "未记录"}</strong>
      {label === "今日问答" ? <small className="dashboard-kpi-detail">上海自然日 · AnswerRun 创建尝试</small> : kpi?.delta != null ? <span className="dashboard-kpi-delta">+{kpi.delta} / 24h</span> : <span className="dashboard-kpi-delta dashboard-kpi-delta--unknown">暂无变化数据</span>}
      {label === "文档" && (kpi?.ready != null || kpi?.failed != null) ? <small className="dashboard-kpi-detail">{kpi.ready ?? "未记录"} 可问答 · {kpi.failed ?? "未记录"} 失败</small> : null}
    </div>
  );

  return (
    <section className="admin-page">
      <header className="admin-page-heading admin-dashboard-heading"><div><span className="eyebrow">运营总览</span><h1>概览</h1><p>先看知识健康、回答质量和今日问答，再进入异常与运营明细。</p></div><div className="admin-dashboard-actions"><span>更新于 {new Date(overview.data.updated_at).toLocaleString("zh-CN")}</span><button className="secondary-button" type="button" onClick={() => void overview.refetch()}><ArrowPathIcon aria-hidden="true" /> {overview.isFetching ? "刷新中…" : "刷新"}</button></div></header>

      <div className="dashboard-kpis">{renderKpi("用户", kpis.users)}{renderKpi("知识库", kpis.knowledge_bases)}{renderKpi("可回答文档", kpis.documents.ready == null ? undefined : { ...kpis.documents, value: kpis.documents.ready })}{renderKpi("今日问答", kpis.answers_today)}{renderKpi("负反馈", kpis.feedback)}</div>

      <section className="admin-card dashboard-panel">
        <div className="dashboard-panel-heading"><div><h2>知识健康分布</h2><p>{total} 个知识库；没有健康快照的知识库标记为“未评估”。</p></div><Link to="/admin/knowledge">查看知识库</Link></div>
        {([ ["healthy", distribution.healthy], ["warning", distribution.warning], ["critical", distribution.critical], ["partial", distribution.partial], ["unknown", distribution.unknown] ] as const).map(([key, value]) => <div className="dashboard-health-row" key={key}><KnowledgeHealthBadge status={key} label={key === "unknown" ? "未评估" : undefined} /><div className="dashboard-heat-track"><div className={"dashboard-heat-fill " + key} style={{ width: (total ? (value / total) * 100 : 0) + "%" }} /></div><strong>{value}</strong></div>)}
      </section>

      <section className="admin-card dashboard-panel dashboard-quality-panel">
        <div className="dashboard-panel-heading"><div><h2>回答质量</h2><p>每项指标展示自己的样本量和分母；没有样本时标记为“样本不足”。</p></div><Link to="/admin/runs">查看回答记录</Link></div>
        <div className="dashboard-quality-grid">
          <div><span>成功率</span><strong>{metricValue(overview.data.quality.success_rate, (value) => `${value}%`)}</strong><small>{overview.data.quality.success_rate.numerator == null ? "样本不足" : `${overview.data.quality.success_rate.numerator} / ${overview.data.quality.success_rate.denominator} 次（失败计入分母）`}</small></div>
          <div><span>负反馈率</span><strong>{metricValue(overview.data.quality.negative_feedback_rate, (value) => `${value}%`)}</strong><small>{overview.data.quality.negative_feedback_rate.numerator == null ? "样本不足" : `${overview.data.quality.negative_feedback_rate.numerator} / ${overview.data.quality.negative_feedback_rate.denominator} 条有效反馈`}</small></div>
          <div><span>反馈覆盖率</span><strong>{metricValue(overview.data.quality.feedback_coverage_rate, (value) => `${value}%`)}</strong><small>{overview.data.quality.feedback_coverage_rate.numerator == null ? "样本不足" : `${overview.data.quality.feedback_coverage_rate.numerator} / ${overview.data.quality.feedback_coverage_rate.denominator} 个终态回答`}</small></div>
          <div><span>无引用回答</span><strong>{metricValue(overview.data.quality.uncited_answer_count, (value) => String(value))}</strong><small>{overview.data.quality.uncited_answer_count.denominator == null ? "样本不足" : `成功回答中 ${overview.data.quality.uncited_answer_count.numerator} / ${overview.data.quality.uncited_answer_count.denominator}`}</small></div>
          <div><span>平均响应时间</span><strong>{metricValue(overview.data.quality.average_latency_ms, (value) => `${value} ms`)}</strong><small>{overview.data.quality.average_latency_ms.sample_count ? `${overview.data.quality.average_latency_ms.sample_count} 个有延迟记录` : "未采集"}</small></div>
          <div><span>主动取消</span><strong>{overview.data.quality.cancelled_count}</strong><small>单独统计，不降低成功率</small></div>
        </div>
      </section>

      <section className="admin-card dashboard-panel">
        <div className="dashboard-panel-heading"><div><h2>趋势</h2><p>后端提供文档、消息和负反馈的日粒度趋势；趋势是运营明细，不替代今日问答。</p></div><div className="dashboard-window-tabs"><button className={days === 7 ? "active" : ""} type="button" onClick={() => setDays(7)}>7 天</button><button className={days === 30 ? "active" : ""} type="button" onClick={() => setDays(30)}>30 天</button></div></div>
        {trends.isLoading ? <LoadingState label="正在加载趋势…" /> : null}
        {trends.error ? <ErrorState message={trends.error.message} /> : null}
        {!hasTrendActivity ? <p className="dashboard-empty-copy">最近 {days} 天暂无问答活动</p> : null}
        {hasTrendActivity ? series.map((item) => <div className="dashboard-series" key={item.name}><h4>{item.name === "messages" ? "问答消息" : item.name === "feedback" ? "反馈" : item.name === "documents" ? "文档" : item.name}</h4><div className="dashboard-series-track">{item.data.map((point) => <div className="dashboard-series-col" key={point.ts} style={{ height: (point.value / maxValue) * 100 + "%" }} title={point.ts + ":" + point.value} />)}</div></div>) : null}
      </section>

      <section className="admin-card dashboard-panel dashboard-operations-panel"><div className="dashboard-panel-heading"><div><h2>运营明细</h2><p>连接器、审计、会话和累计消息保留在这里，不抢占主 KPI。</p></div></div><div className="dashboard-operation-grid"><div><span>累计会话</span><strong>{kpis.conversations.value ?? "未记录"}</strong></div><div><span>累计问答消息</span><strong>{kpis.messages.value ?? "未记录"}</strong></div><div><span>连接器同步</span><strong>{kpis.connector_syncs.value ?? "未记录"}</strong></div><div><span>审计记录</span><strong>{kpis.audit_logs.value ?? "未记录"}</strong></div></div></section>

      <div className="admin-dashboard-links"><Link className="admin-dashboard-link-card" to="/admin/knowledge"><strong>文档为什么不能问？</strong><span>进入知识库文档详情，查看处理状态、失败原因和重试动作。</span></Link><Link className="admin-dashboard-link-card" to="/admin/audit"><strong>谁改了权限或配置？</strong><span>进入审计日志，筛选操作人、结果和时间范围。</span></Link><Link className="admin-dashboard-link-card" to="/admin/runs"><strong>一次回答是怎么完成的？</strong><span>回答记录先看问题与质量，执行详情只展示已有真实 instrumentation。</span></Link></div>
    </section>
  );
}

function AuditSnapshot({ snapshot }: { snapshot: unknown }) {
  const asRecord = (value: unknown): Record<string, unknown> | null => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
  const displayName = (value: unknown) => {
    const record = asRecord(value);
    return String(record?.knowledge_base_name ?? record?.name ?? record?.knowledge_base_id ?? record?.id ?? value ?? "未记录");
  };
  if (Array.isArray(snapshot)) {
    return snapshot.length ? <ul className="audit-snapshot-list">{snapshot.map((item, index) => <li key={`${displayName(item)}-${index}`}><strong>{displayName(item)}</strong>{asRecord(item)?.knowledge_base_id ? <small>{String(asRecord(item)?.knowledge_base_id)}</small> : null}</li>)}</ul> : <span className="audit-empty-value">空集合</span>;
  }
  const record = asRecord(snapshot);
  if (record && (record.name || record.id || record.knowledge_base_name || record.knowledge_base_id)) return <span>{displayName(snapshot)}</span>;
  return <pre>{JSON.stringify(snapshot, null, 2)}</pre>;
}

export function AdminAuditPage() {
  const pageSize = 20;
  const defaults = { page: 1, biz_type: "", biz_id: "", operation_type: "", operator: "", result: "", from: "", to: "" };
  const { state, update, reset } = useUrlTableState(defaults);
  const [expanded, setExpanded] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["admin-audit-logs", state],
    queryFn: () => listAuditLogs({
      page: state.page,
      page_size: pageSize,
      biz_type: state.biz_type || undefined,
      biz_id: state.biz_id || undefined,
      operation_type: state.operation_type || undefined,
      operator_name: state.operator || undefined,
      success: state.result === "true" ? true : state.result === "false" ? false : undefined,
      begin_time: state.from ? state.from + "T00:00:00" : undefined,
      end_time: state.to ? state.to + "T23:59:59.999" : undefined,
    }),
  });
  const totalPages = query.data ? Math.max(1, Math.ceil(query.data.total / pageSize)) : 1;
  const hasFilters = Object.entries(state).some(([key, value]) => key !== "page" && value !== "");
  return (
    <div className="admin-page admin-users-page">
      <header className="page-heading"><div><span className="eyebrow">管理员</span><h1>操作审计</h1><p>记录谁在什么时间改了哪些权限与配置，含变更前后快照与差异。</p></div></header>
      <section className="workspace-card">
        <div className="audit-filters"><label><span>业务类型</span><input value={state.biz_type} placeholder="如 knowledge_base" onChange={(event) => update({ biz_type: event.target.value, page: 1 }, { replace: true })} /></label><label><span>业务 ID</span><input value={state.biz_id} placeholder="精确定位对象" onChange={(event) => update({ biz_id: event.target.value, page: 1 }, { replace: true })} /></label><label><span>操作类型</span><input value={state.operation_type} placeholder="如 SET_DEPARTMENT_KB_ACCESS" onChange={(event) => update({ operation_type: event.target.value, page: 1 }, { replace: true })} /></label><label><span>操作人</span><input value={state.operator} placeholder="显示名称或账号" onChange={(event) => update({ operator: event.target.value, page: 1 }, { replace: true })} /></label><label><span>结果</span><select value={state.result} onChange={(event) => update({ result: event.target.value, page: 1 })}><option value="">全部</option><option value="true">成功</option><option value="false">失败</option></select></label><label><span>开始日期</span><input type="date" value={state.from} onChange={(event) => update({ from: event.target.value, page: 1 })} /></label><label><span>结束日期</span><input type="date" value={state.to} onChange={(event) => update({ to: event.target.value, page: 1 })} /></label><div className="audit-filter-actions"><button className="secondary-button" type="button" onClick={reset} disabled={!hasFilters}>清除筛选</button></div></div>
        {query.isLoading ? <p className="empty-state">加载中…</p> : query.isError ? <div className="empty-state"><p>加载失败：{query.error?.message || "未知错误"}</p><button type="button" onClick={() => void query.refetch()}>重试</button></div> : <table className="audit-table"><thead><tr><th>时间</th><th>操作</th><th>业务</th><th>操作人</th><th>结果</th></tr></thead><tbody>{(query.data?.items ?? []).map((log: OperationAuditLog) => <Fragment key={log.id}><tr className={log.success ? "" : "audit-row-failed"} onClick={() => setExpanded(expanded === log.id ? null : log.id)}><td>{new Date(log.created_at).toLocaleString()}</td><td><span className="audit-action">{log.action_desc}</span><span className="audit-operation">{log.operation_type}</span></td><td>{log.biz_type}<span className="audit-biz-id">{log.biz_id}</span></td><td>{log.operator_name || log.operator_id}{log.ip ? <span className="audit-ip">{log.ip}</span> : null}</td><td>{log.success ? "成功" : "失败：" + (log.error_message || "未知错误")}</td></tr>{expanded === log.id ? <tr className="audit-detail-row"><td colSpan={5}><div className="audit-snapshot-grid"><div><h4>变更前</h4><AuditSnapshot snapshot={log.before_snapshot} /></div><div><h4>变更后</h4><AuditSnapshot snapshot={log.after_snapshot} /></div><div><h4>差异</h4><pre>{JSON.stringify(log.change_diff, null, 2)}</pre></div></div></td></tr> : null}</Fragment>)}</tbody></table>}
        <div className="pagination"><button type="button" disabled={state.page <= 1} onClick={() => update({ page: Math.max(1, state.page - 1) })}>上一页</button><span>第 {state.page} / {totalPages} 页（共 {query.data?.total ?? 0} 条）</span><button type="button" disabled={state.page >= totalPages} onClick={() => update({ page: state.page + 1 })}>下一页</button></div>
      </section>
    </div>
  );
}
