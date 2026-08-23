import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

type UrlValue = string | number | null | undefined;

function decodeValue<T extends UrlValue>(raw: string | null, fallback: T): T {
  if (raw === null || raw === "") return fallback;
  if (typeof fallback === "number") {
    const parsed = Number(raw);
    return (Number.isFinite(parsed) ? parsed : fallback) as T;
  }
  return raw as T;
}

export function useUrlTableState<T extends Record<string, UrlValue>>(defaults: T) {
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.toString();
  const state = useMemo(() => {
    const next = {} as T;
    for (const [key, fallback] of Object.entries(defaults) as [keyof T, T[keyof T]][]) {
      next[key] = decodeValue(searchParams.get(String(key)), fallback);
    }
    return next;
  }, [defaults, search, searchParams]);

  const update = useCallback(
    (patch: Partial<T>, options: { replace?: boolean } = {}) => {
      const next = new URLSearchParams(searchParams);
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined || value === null || value === "") {
          next.delete(key);
        } else {
          next.set(key, String(value));
        }
      }
      setSearchParams(next, { replace: options.replace ?? false });
    },
    [searchParams, setSearchParams],
  );

  const reset = useCallback(() => {
    const next = new URLSearchParams();
    for (const [key, value] of Object.entries(defaults)) {
      if (value !== undefined && value !== null && value !== "") {
        next.set(key, String(value));
      }
    }
    setSearchParams(next, { replace: false });
  }, [defaults, setSearchParams]);

  return { state, update, reset };
}
