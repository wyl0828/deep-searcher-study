import { TrashIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { type ReactNode, useId } from "react";

import { useModalFocus } from "./useModalFocus";

type DangerConfirmDialogProps = {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  pendingLabel?: string;
  warning?: string;
  pending: boolean;
  error: Error | null;
  onClose: () => void;
  onConfirm: () => void;
};

export function DangerConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "确认删除",
  pendingLabel = "正在删除…",
  warning = "此操作无法撤销。",
  pending,
  error,
  onClose,
  onConfirm,
}: DangerConfirmDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useModalFocus({
    open,
    onDismiss: onClose,
    dismissBlocked: pending,
  });

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
        className="dialog-card dialog-card--danger"
        role="alertdialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">危险操作</span>
            <h2 id={titleId}>{title}</h2>
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
        <div id={descriptionId} className="dialog-description">
          {description}
        </div>
        <p className="dialog-warning">{warning}</p>
        {error ? (
          <div className="product-state product-state--error" role="alert">
            <span>{error.message}</span>
          </div>
        ) : null}
        <div className="dialog-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={onClose}
            disabled={pending}
            data-dialog-initial-focus
          >
            取消
          </button>
          <button
            className="danger-button"
            type="button"
            onClick={onConfirm}
            disabled={pending}
          >
            <TrashIcon aria-hidden="true" />
            {pending ? pendingLabel : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
