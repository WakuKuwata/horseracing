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
