export type TraceStageStatus = "completed" | "failed" | "partial" | "unknown";
export type KnowledgeHealthStatus =
  | "healthy"
  | "warning"
  | "critical"
  | "partial"
  | "unknown";
export type IndexStatus = "verified" | "processing" | "needs_rebuild" | "failed" | "unknown";
export type TrustStatus = "trusted" | "partial" | "untrusted" | "unknown";
export type RiskStatus = "low" | "medium" | "high" | "unknown";
export type IngestJobStatusValue = "queued" | "running" | "succeeded" | "failed" | "partial" | "unknown";
export type ConnectorSyncStatusValue = "idle" | "running" | "succeeded" | "failed" | "partial" | "unknown";

const labels: Record<string, string> = {
  healthy: "健康",
  warning: "预警",
  critical: "严重",
  partial: "部分数据",
  unknown: "尚无结论",
  completed: "已完成",
  pending: "等待完成",
  verified: "已验证",
  processing: "处理中",
  needs_rebuild: "需要重建",
  failed: "失败",
  not_indexed: "尚未建立索引",
  not_assessed: "尚未评估",
  not_evaluated: "尚未评估",
  dead_letter: "失败",
  trusted: "可信",
  untrusted: "不可信",
  low: "低风险",
  medium: "中风险",
  high: "高风险",
  queued: "等待处理",
  running: "正在处理",
  ready: "可用于问答",
  succeeded: "已完成",
  idle: "未运行",
};

export function StatusBadge({
  status,
  label,
  detail,
}: {
  status: string;
  label?: string;
  detail?: string;
}) {
  const displayLabel = label || labels[status] || status;
  return (
    <span className={`semantic-status semantic-status--${status}`} data-status={status}>
      <span className="semantic-status-dot" aria-hidden="true" />
      <span>{displayLabel}</span>
      {detail ? <small>{detail}</small> : null}
    </span>
  );
}

export function DocumentStatusBadge({ status }: { status: string }) {
  const label = labels[status] || status;
  return (
    <span
      className={`document-status document-status--${status}`}
      role="cell"
      aria-label={`文档状态：${label}`}
      aria-live="polite"
      aria-atomic="true"
    >
      {status === "ready" ? "可用于问答" : label}
    </span>
  );
}

export function IndexStatusBadge({ status }: { status: IndexStatus }) {
  return <StatusBadge status={status} />;
}

export function TrustStatusBadge({ status }: { status: TrustStatus }) {
  return <StatusBadge status={status} />;
}

export function RiskBadge({ status }: { status: RiskStatus }) {
  return <StatusBadge status={status} />;
}

export function KnowledgeHealthBadge({
  status,
  label,
}: {
  status: KnowledgeHealthStatus;
  label?: string;
}) {
  return <StatusBadge status={status} label={label} />;
}

export function IngestJobStatus({ status }: { status: IngestJobStatusValue }) {
  return <StatusBadge status={status} />;
}

export function ConnectorSyncStatus({ status }: { status: ConnectorSyncStatusValue }) {
  return <StatusBadge status={status} />;
}
