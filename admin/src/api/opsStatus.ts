import { useQuery } from "@tanstack/react-query";

import type { ErrorInfo } from "./client";
import { getOpsStatus, type OpsStatus } from "./opsClient";

export const OPS_STATUS_KEY = ["ops-status"] as const;

/**
 * B5 (2026-10-27): ops may be paused (feature 172: `collection_paused`); every write is then a 503
 * before it reaches the app. The admin asks first. Fail-OPEN: an unreachable status endpoint leaves
 * the buttons enabled and a click surfaces the real error (mirrors front/src/api/opsStatus.ts).
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

export function usePauseState(): { paused: boolean; detail: string | null; mismatch: boolean } {
  const q = useOpsStatus();
  const paused = q.data?.collection_paused === true;
  return {
    paused,
    detail: paused ? (q.data?.detail ?? null) : null,
    // the policy file says paused but this ops process is not enforcing it (started without the
    // 172 wrapper) — an operator-facing fact, shown only in the admin
    mismatch: q.data?.policy_mismatch === true,
  };
}
