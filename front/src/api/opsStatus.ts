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
    // 2026-10-31: the capture holds open/close on the clock while a race page sits open, so
    // re-ask once a minute (one tiny GET) instead of only on focus; ops waits regardless.
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
  /** true only when ops CONFIRMED a per-race hold of today's near-post capture is open right now. */
  active: boolean;
  /** JST "HH:MM:SS" the open hold ends at; null when not active / unknown */
  until: string | null;
  /** the service's own wording; null when not active */
  detail: string | null;
};

function jstClock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("ja-JP", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Tokyo",
  }).format(d);
}

/**
 * 2026-11-02: on a capture day (174) ops holds its own fetching for ~35 s around each race's T−3
 * record (post − 4 min). A refresh clicked inside a hold is ACCEPTED and simply starts a few
 * seconds later (the worker waits the hold out; nothing is refused), so the refresh buttons
 * stay enabled and only show a muted note while a hold is open. Until 2026-11-02 the hold was
 * the whole race day and the buttons were disabled (409); the user rejected that. Status is
 * polled once a minute, so a 35 s hold can pass unnoticed here — the ops side guarantees the
 * wait regardless. Fail-open like usePauseState: unknown = not active.
 */
export function useCaptureWindowState(): CaptureWindowState {
  const q = useOpsStatus();
  const w = q.data?.capture_window ?? null;
  const active = w?.active === true;
  const end = w?.hold_ends_at ?? null;
  return {
    active,
    until: active && end ? jstClock(end) : null,
    detail: active && w ? w.detail : null,
  };
}
