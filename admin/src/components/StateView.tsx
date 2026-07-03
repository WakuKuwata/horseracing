import type { ReactNode } from "react";

import type { ErrorInfo } from "../api/client";

/**
 * Three DISTINCT states (FR-011): loading, typed error (404/409/422 → {status,code,detail}),
 * and empty (a successful 200 with no rows). Never conflate "no data" with "error".
 */

export function LoadingView({ label = "読み込み中…" }: { label?: string }) {
  return (
    <div className="state state--loading" role="status" aria-busy="true">
      {label}
    </div>
  );
}

export function EmptyView({ message = "該当するデータがありません" }: { message?: string }) {
  return (
    <div className="state state--empty" data-state="empty">
      {message}
    </div>
  );
}

export function ErrorView({ error }: { error: ErrorInfo }) {
  return (
    <div className="state state--error" role="alert" data-state="error" data-code={error.code}>
      <strong>エラー {error.status}</strong>
      <span className="state__code"> ({error.code})</span>
      <p className="state__detail">{error.detail}</p>
    </div>
  );
}

/**
 * Convenience wrapper for react-query results. Renders the right state and only calls `children`
 * when data is present. `isEmpty` lets callers define emptiness (e.g. items.length === 0).
 */
export function QueryStateView<T>({
  isLoading,
  error,
  data,
  isEmpty,
  loadingLabel,
  emptyMessage,
  children,
}: {
  isLoading: boolean;
  error: ErrorInfo | null;
  data: T | undefined;
  isEmpty?: (data: T) => boolean;
  loadingLabel?: string;
  emptyMessage?: string;
  children: (data: T) => ReactNode;
}) {
  if (isLoading) return <LoadingView label={loadingLabel} />;
  if (error) return <ErrorView error={error} />;
  if (data === undefined) return <LoadingView label={loadingLabel} />;
  if (isEmpty?.(data)) return <EmptyView message={emptyMessage} />;
  return <>{children(data)}</>;
}
