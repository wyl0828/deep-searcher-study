import {
  ArrowPathIcon,
  CircleStackIcon,
  ExclamationTriangleIcon,
} from "@heroicons/react/24/outline";

import { ProductApiError } from "./product-api";

type QueryFailureStateProps = {
  error: Error;
  pending: boolean;
  onRetry: () => void;
  onOpenKnowledgeBase: () => void;
};

const INDEX_ERRORS = new Set([
  "VECTOR_COLLECTION_NOT_FOUND",
  "VECTOR_DIMENSION_MISMATCH",
]);
const ERROR_TITLES: Record<string, string> = {
  QUERY_CANCELLED: "本次回答已停止",
  RUNTIME_INITIALIZATION_FAILED: "问答服务暂时未就绪",
  VECTOR_DB_UNAVAILABLE: "检索服务暂时不可用",
  VECTOR_COLLECTION_NOT_FOUND: "知识库索引需要处理",
  VECTOR_DIMENSION_MISMATCH: "知识库索引需要处理",
};

export function QueryFailureState({
  error,
  pending,
  onRetry,
  onOpenKnowledgeBase,
}: QueryFailureStateProps) {
  const productError = error instanceof ProductApiError ? error : null;
  const code = productError?.code || "UNKNOWN_ERROR";
  const indexNeedsAttention = INDEX_ERRORS.has(code);
  const cancelled = code === "QUERY_CANCELLED";
  const title = ERROR_TITLES[code] || "本次回答没有完成";

  return (
    <section className="query-failure-state" role="alert" aria-live="assertive">
      <ExclamationTriangleIcon aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{error.message}</p>
        <small>
          {cancelled
            ? "没有保存未完成的回答，你可以重新开始。"
            : "这是系统故障，不代表知识库中没有相关资料。"}
        </small>
        {productError?.requestId ? <small>请求编号：{productError.requestId}</small> : null}
      </div>
      <button
        className="secondary-button"
        type="button"
        onClick={indexNeedsAttention ? onOpenKnowledgeBase : onRetry}
        disabled={pending}
      >
        {indexNeedsAttention ? (
          <CircleStackIcon aria-hidden="true" />
        ) : (
          <ArrowPathIcon aria-hidden="true" />
        )}
        {indexNeedsAttention
          ? "查看知识库"
          : pending
            ? "正在重试…"
            : cancelled
              ? "重新开始"
              : "重新尝试"}
      </button>
    </section>
  );
}
