import {
  ArrowPathIcon,
  CircleStackIcon,
  CpuChipIcon,
  LinkIcon,
  WrenchScrewdriverIcon,
} from "@heroicons/react/24/outline";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { EmptyState, ErrorState, LoadingState } from "../../components/states";
import { StatusBadge } from "../../components/status";
import {
  getSystemDiagnostics,
  type SystemDiagnostics,
  type SystemHealthService,
} from "../../product-api";

const SERVICE_LABELS: Record<keyof SystemDiagnostics["services"], string> = {
  fastapi: "应用服务",
  milvus: "向量数据库",
  llm: "生成模型",
  embedding: "Embedding 模型",
  ingest_worker: "文档处理",
};

function overallBadgeStatus(status: SystemDiagnostics["status"]) {
  if (status === "ready") return "healthy";
  if (status === "degraded") return "warning";
  return "critical";
}

function serviceBadgeStatus(service: SystemHealthService) {
  if (service.state === "ready") return "healthy";
  if (service.state === "not_ready") return "critical";
  return "unknown";
}

function serviceDetail(service: SystemHealthService) {
  if (service.code) return service.code;
  if (service.state === "ready") return "检查通过";
  return "尚无更具体的诊断结论";
}

export function AdminDiagnosticsPage() {
  const diagnostics = useQuery({
    queryKey: ["admin-diagnostics"],
    queryFn: getSystemDiagnostics,
    staleTime: 0,
  });

  const data = diagnostics.data;
  return (
    <section className="admin-page">
      <header className="admin-page-heading admin-diagnostics-heading">
        <div>
          <span className="eyebrow">系统管理</span>
          <h1>系统状态</h1>
          <p>查看应用服务、模型、向量检索和文档处理的当前可用性。</p>
        </div>
        <button
          className="secondary-button"
          type="button"
          onClick={() => void diagnostics.refetch()}
          disabled={diagnostics.isFetching}
        >
          <ArrowPathIcon aria-hidden="true" />
          {diagnostics.isFetching ? "检查中…" : "刷新状态"}
        </button>
      </header>

      {diagnostics.isLoading ? <LoadingState label="正在运行深度诊断…" /> : null}
      {diagnostics.error ? (
        <section className="admin-card diagnostics-error-card">
          <ErrorState message={diagnostics.error.message} />
          <button
            className="secondary-button"
            type="button"
            onClick={() => void diagnostics.refetch()}
          >
            重试
          </button>
        </section>
      ) : null}
      {!diagnostics.isLoading && !diagnostics.error && !data ? (
        <EmptyState
          title="暂无诊断结果"
          description="当前没有可展示的健康探针结果。"
        />
      ) : null}

      {data ? (
        <>
          <section className="admin-card diagnostics-overview-card">
            <div className="diagnostics-overview-icon">
              <WrenchScrewdriverIcon aria-hidden="true" />
            </div>
            <div>
              <span className="eyebrow">深度探针</span>
              <h2>当前系统状态</h2>
              <p>
                {data.status === "ready"
                  ? "核心服务和文档处理均已通过检查。"
                  : data.status === "degraded"
                    ? "系统可继续工作，但至少有一项依赖或文档处理组件需要处理。"
                    : "当前系统无法完整提供产品能力，请先处理失败检查项。"}
              </p>
            </div>
            <StatusBadge
              status={overallBadgeStatus(data.status)}
              label={
                data.status === "ready"
                  ? "全部可用"
                  : data.status === "degraded"
                    ? "部分可用"
                    : "不可用"
              }
            />
            <dl className="diagnostics-meta">
              <div>
                <dt>request_id</dt>
                <dd>{data.request_id || "尚无结论"}</dd>
              </div>
              <div>
                <dt>配置集合</dt>
                <dd>{data.config.collection}</dd>
              </div>
            </dl>
          </section>

          <section className="admin-card diagnostics-services-card">
            <div className="admin-section-heading">
              <div>
                <span className="eyebrow">依赖检查</span>
                <h2>服务与运行组件</h2>
              </div>
            </div>
            <div className="diagnostics-service-grid">
              {(Object.entries(data.services) as [
                keyof SystemDiagnostics["services"],
                SystemHealthService,
              ][]).map(([key, service]) => (
                <div className="diagnostics-service-item" key={key}>
                  {key === "milvus" ? (
                    <CircleStackIcon aria-hidden="true" />
                  ) : key === "llm" || key === "embedding" ? (
                    <CpuChipIcon aria-hidden="true" />
                  ) : (
                    <LinkIcon aria-hidden="true" />
                  )}
                  <div>
                    <strong>{SERVICE_LABELS[key]}</strong>
                    <small>{serviceDetail(service)}</small>
                  </div>
                  <StatusBadge
                    status={serviceBadgeStatus(service)}
                    label={
                      service.state === "ready"
                        ? "就绪"
                        : service.state === "not_ready"
                          ? "未就绪"
                          : "未知"
                    }
                  />
                </div>
              ))}
            </div>
          </section>

          <div className="diagnostics-two-column">
            <section className="admin-card diagnostics-config-card">
              <div className="admin-section-heading">
                <div>
                  <span className="eyebrow">运行配置</span>
                  <h2>当前使用的模型</h2>
                </div>
              </div>
              <dl className="diagnostics-config-list">
                <div>
                  <dt>生成模型</dt>
                  <dd>{data.config.llm_model}</dd>
                </div>
                <div>
                  <dt>Embedding 模型</dt>
                  <dd>{data.config.embedding_model}</dd>
                </div>
                <div>
                  <dt>默认集合</dt>
                  <dd>{data.config.collection}</dd>
                </div>
              </dl>
            </section>
            <section className="admin-card diagnostics-recent-errors-card">
              <div className="admin-section-heading"><div><span className="eyebrow">运行情况</span><h2>最近异常</h2><p>当前诊断接口返回的错误会在对应服务卡片中显示。</p></div></div>
              <p className="admin-muted">暂无单独的异常列表。</p>
            </section>
          </div>

          <nav className="diagnostics-links" aria-label="诊断后续操作">
            <Link className="admin-dashboard-link-card" to="/admin/knowledge">
              <strong>查看失败文档</strong>
              <span>定位文档为什么不能问</span>
            </Link>
            <Link className="admin-dashboard-link-card" to="/admin/runs?status=failed">
              <strong>查看失败回答</strong>
              <span>进入真实持久化的回答记录</span>
            </Link>
            <Link className="admin-dashboard-link-card" to="/admin/knowledge">
              <strong>查看知识健康</strong>
              <span>从知识库详情检查健康结论</span>
            </Link>
          </nav>
        </>
      ) : null}
    </section>
  );
}
