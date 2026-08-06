import { useLayoutEffect, useRef } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

type ModalFocusOptions = {
  open: boolean;
  onDismiss: () => void;
  dismissBlocked?: boolean;
};

function focusableElements(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => !element.hidden && element.getAttribute("aria-hidden") !== "true",
  );
}

export function useModalFocus({
  open,
  onDismiss,
  dismissBlocked = false,
}: ModalFocusOptions) {
  const dialogRef = useRef<HTMLElement>(null);
  const onDismissRef = useRef(onDismiss);
  const dismissBlockedRef = useRef(dismissBlocked);
  onDismissRef.current = onDismiss;
  dismissBlockedRef.current = dismissBlocked;

  useLayoutEffect(() => {
    if (!open) return undefined;
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const initialFocus =
      dialog.querySelector<HTMLElement>("[data-dialog-initial-focus]") ||
      focusableElements(dialog)[0] ||
      dialog;
    initialFocus.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!dismissBlockedRef.current) {
          event.preventDefault();
          onDismissRef.current();
        }
        return;
      }
      if (event.key !== "Tab") return;
      const elements = focusableElements(dialog);
      if (!elements.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = elements[0];
      const last = elements[elements.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [open]);

  return dialogRef;
}
