import {
  ArrowPathIcon,
  InboxIcon,
  ShieldExclamationIcon,
} from "@heroicons/react/24/outline";
import type { ReactNode } from "react";

export function LoadingState({ label = "正在加载…" }: { label?: string }) {
  return (
    <div className="product-state product-state--loading" role="status" aria-live="polite">
      <ArrowPathIcon aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="product-state product-state--error" role="alert">
      <span>{message}</span>
    </div>
  );
}

export function EmptyState({
  title = "暂无内容",
  description,
  action,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <section className="semantic-empty-state" aria-live="polite">
      <InboxIcon aria-hidden="true" />
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
      {action ? <div className="semantic-state-action">{action}</div> : null}
    </section>
  );
}

export function ForbiddenState({
  title = "无权访问此页面",
  description = "你的账号已登录，但当前角色没有管理后台权限。",
  action,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <main className="semantic-forbidden-state" role="alert">
      <ShieldExclamationIcon aria-hidden="true" />
      <span className="eyebrow">403 Forbidden</span>
      <h1>{title}</h1>
      <p>{description}</p>
      {action ? <div className="semantic-state-action">{action}</div> : null}
    </main>
  );
}
