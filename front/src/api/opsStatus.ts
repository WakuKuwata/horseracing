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
    // 2026-10-31: the capture window opens/closes on the clock while a race page sits open, so
    // re-ask once a minute (one tiny GET) instead of only on focus; the ops 409 still protects.
    refetchInterval: 60_000,
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

export type CaptureWindowState = {
  /** true only when ops CONFIRMED today's near-post capture window is open right now. */
  active: boolean;
  /** JST "HH:MM" the window closes at; null when not active / unknown */
  until: string | null;
  /** the service's own wording; null when not active */
  detail: string | null;
};

function jstClock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("ja-JP", {
    hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Tokyo",
  }).format(d);
}

/**
 * 2026-10-31: while today's T−3 capture (174) is running, a data refresh would compete with it
 * for the one shared request slot, so ops refuses refreshes (409 capture_window) until the last
 * race's post time. Only データ更新 / この日を更新 read this; 予測・買い目生成・購入記録 never do
 * (they make no source request). Fail-open like usePauseState: unknown = not active.
 */
export function useCaptureWindowState(): CaptureWindowState {
  const q = useOpsStatus();
  const w = q.data?.capture_window ?? null;
  const active = w?.active === true;
  return {
    active,
    until: active && w ? jstClock(w.ends_at) : null,
    detail: active && w ? w.detail : null,
  };
}
