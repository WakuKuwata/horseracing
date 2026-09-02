import createClient from "openapi-fetch";

import { parseApiError, type ErrorInfo } from "./client";
import type { paths } from "./ops-schema";

// The ops (write) service (024) is a SEPARATE origin path from the read-only 014 API. The SPA calls
// {origin}/ops/v1/* — routed by the Vite dev proxy to the ops service. Display data still comes from
// /api/v1 (014); this client only triggers refresh jobs and polls their status.
const baseUrl = typeof window !== "undefined" ? window.location.origin : "";

export const opsApi = createClient<paths>({
  baseUrl,
  fetch: (...args) => globalThis.fetch(...args),
});

type S = paths;
export type JobAccepted =
  S["/ops/v1/races/{race_id}/refresh"]["post"]["responses"][202]["content"]["application/json"];
export type Job =
  S["/ops/v1/jobs/{job_id}"]["get"]["responses"][200]["content"]["application/json"];
export type BatchAccepted =
  S["/ops/v1/days/{date}/refresh"]["post"]["responses"][202]["content"]["application/json"];
export type Batch =
  S["/ops/v1/batches/{trace_id}"]["get"]["responses"][200]["content"]["application/json"];

export type JobStatus = Job["status"];

export const TERMINAL: JobStatus[] = ["succeeded", "partial", "failed", "skipped"];

export function isTerminal(status: JobStatus | undefined): boolean {
  return status != null && TERMINAL.includes(status);
}

/** A batch is done when no child is still queued/running. */
export function isBatchDone(status: JobStatus | undefined): boolean {
  return status != null && status !== "queued" && status !== "running";
}

function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw parseApiError(result.response.status, result.error) as ErrorInfo;
  }
  return result.data as T;
}

/** Enqueue a 1-race refresh; returns the accepted job (202). */
export async function refreshRace(raceId: string, force = false): Promise<JobAccepted> {
  return unwrap(
    await opsApi.POST("/ops/v1/races/{race_id}/refresh", {
      params: { path: { race_id: raceId } },
      body: { force },
    }),
  );
}

/** Feature 028: enqueue a 1-race prediction job; returns the accepted job (202). */
export async function predictRace(raceId: string): Promise<JobAccepted> {
  return unwrap(
    await opsApi.POST("/ops/v1/races/{race_id}/predict", {
      params: { path: { race_id: raceId } },
    }),
  );
}

/** Feature 043: generate buy recommendations for one race (ops write path). */
export async function recommendRace(raceId: string): Promise<JobAccepted> {
  return unwrap(
    await opsApi.POST("/ops/v1/races/{race_id}/recommend", {
      params: { path: { race_id: raceId } },
    }),
  );
}

/** Poll a refresh job's status. */
export async function getJob(jobId: string): Promise<Job> {
  return unwrap(
    await opsApi.GET("/ops/v1/jobs/{job_id}", {
      params: { path: { job_id: jobId } },
    }),
  );
}

/** Enqueue a whole-day batch refresh; returns the accepted batch (202). */
export async function refreshDay(date: string, force = false): Promise<BatchAccepted> {
  return unwrap(
    await opsApi.POST("/ops/v1/days/{date}/refresh", {
      params: { path: { date } },
      body: { force },
    }),
  );
}

/** Poll a day batch's aggregate status + children. */
export async function getBatch(traceId: string): Promise<Batch> {
  return unwrap(
    await opsApi.GET("/ops/v1/batches/{trace_id}", {
      params: { path: { trace_id: traceId } },
    }),
  );
}

// --- Feature 106: purchase recording (append-only write path) --------------------------------

type PurchaseWireBet = {
  bet_type: string;
  selection: number[];
  amount_yen: number;
  odds_used: number | null;
};

export type PurchaseRecordCreated =
  S["/ops/v1/purchase-records"]["post"]["responses"][201]["content"]["application/json"];
type PurchaseRecordRequest =
  S["/ops/v1/purchase-records"]["post"]["requestBody"]["content"]["application/json"];

/** camelCase editor bet -> snake_case wire bet (drops rows without an amount). */
function toWireBet(bet: {
  betType: string;
  selection: number[];
  amountYen: number | null;
  oddsUsed: number | null;
}): PurchaseWireBet | null {
  if (bet.amountYen == null) return null;
  return {
    bet_type: bet.betType,
    selection: [...bet.selection],
    amount_yen: bet.amountYen,
    odds_used: bet.oddsUsed,
  };
}

/**
 * Submit one purchase record. Converts the component payload (camelCase, snapshot-embedded
 * run id) to the ops wire shape (snake_case bets + top-level prediction_run_id). The server
 * replays an identical retry (200) — both 200 and 201 resolve here.
 */
export async function submitPurchaseRecord(payload: {
  race_id: string;
  kind: string;
  bets: { betType: string; selection: number[]; amountYen: number | null; oddsUsed: number | null }[];
  presented_snapshot: {
    bets: { betType: string; selection: number[]; amountYen: number | null; oddsUsed: number | null }[];
    win_policy: string;
    prediction_run_id: string | null;
    snapshot_schema_version: 1;
  } | null;
  client_request_id: string;
  corrects_record_id?: string;
}): Promise<PurchaseRecordCreated> {
  const snapshot = payload.presented_snapshot;
  const body: PurchaseRecordRequest = {
    race_id: payload.race_id,
    kind: payload.kind as PurchaseRecordRequest["kind"],
    bets: payload.bets.map(toWireBet).filter((b): b is PurchaseWireBet => b !== null),
    client_request_id: payload.client_request_id,
    prediction_run_id: snapshot?.prediction_run_id ?? null,
    presented_snapshot: snapshot
      ? {
          // freeze the slip AS PRESENTED — a bet whose amount is unconvertible (no budget set)
          // stays in the snapshot with amount_yen null, so the policy line reads "not
          // computable" instead of a false verified-zero (dropping it would claim nothing
          // was presented).
          bets: snapshot.bets.map((b) => ({
            bet_type: b.betType,
            selection: [...b.selection],
            amount_yen: b.amountYen,
            odds_used: b.oddsUsed,
          })),
          win_policy: snapshot.win_policy,
          prediction_run_id: snapshot.prediction_run_id,
          snapshot_schema_version: snapshot.snapshot_schema_version,
        }
      : null,
    corrects_record_id: payload.corrects_record_id ?? null,
  };
  return unwrap(
    await opsApi.POST("/ops/v1/purchase-records", { body }),
  );
}
