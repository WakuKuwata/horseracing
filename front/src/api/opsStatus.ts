import { useQuery } from "@tanstack/react-query";

import type { ErrorInfo } from "./client";
import { getOpsStatus, type OpsStatus } from "./opsClient";

export const OPS_STATUS_KEY = ["ops-status"] as const;

/**
 * B5 (2026-10-27): the ops service may be paused (feature 172: `collection_paused`). While paused
 * every write — データ更新・日次更新・予測・買い目生成・購入記録 — is answered 503 before it reaches
 * the app, so the buttons ask first and say why instead of failing.
 *
 * Fail-OPEN on purpose: when this status call itself fails (ops down, proxy refused) the buttons
 * stay enabled and a click surfaces the real error exactly as before. Blocking on an unknown would
 * hide a working write path behind a transient status failure.
 */
export function useOpsStatus() {
  return useQuery<OpsStatus, ErrorInfo>({
    queryKey: OPS_STATUS_KEY,
    queryFn: getOpsStatus,
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: true,
  });
}

export type PauseState = {
  /** true only when ops CONFIRMED it rejects writes right now. Unknown counts as not paused. */
  paused: boolean;
  /** the service's own wording for the pause; null when not paused / unknown */
  detail: string | null;
};

export function usePauseState(): PauseState {
  const q = useOpsStatus();
  const paused = q.data?.collection_paused === true;
  return { paused, detail: paused ? (q.data?.detail ?? null) : null };
}
