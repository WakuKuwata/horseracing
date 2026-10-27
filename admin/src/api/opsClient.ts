import createClient from "openapi-fetch";

import { parseApiError, type ErrorInfo } from "./client";
import type { paths } from "./ops-schema";

// The ops (write) service (024/053) is a SEPARATE origin path from the read-only 014 API. The admin
// SPA calls {origin}/ops/v1/* — routed by the Vite dev proxy to the ops service. Display data still
// comes from /api/v1 (014); this client only enqueues jobs. LOCALHOST-ONLY (auth deferred, 051).
const baseUrl = typeof window !== "undefined" ? window.location.origin : "";

export const opsApi = createClient<paths>({
  baseUrl,
  fetch: (...args) => globalThis.fetch(...args),
});

export type JobAccepted =
  paths["/ops/v1/refresh-range"]["post"]["responses"][202]["content"]["application/json"];

/** Enqueue a predict+recommend range refresh (053). Returns the accepted job or a typed error. */
export async function postRefreshRange(
  dateFrom: string,
  dateTo: string,
): Promise<{ job?: JobAccepted; error?: ErrorInfo }> {
  const res = await opsApi.POST("/ops/v1/refresh-range", {
    body: { date_from: dateFrom, date_to: dateTo },
  });
  if (res.error !== undefined || !res.response.ok) {
    return { error: parseApiError(res.response.status, res.error) };
  }
  return { job: res.data as JobAccepted };
}

// --- B5 (2026-10-27): service status — is the ops process accepting writes? ----------------------
export type OpsStatus =
  paths["/ops/v1/status"]["get"]["responses"][200]["content"]["application/json"];

/** Whether ops currently accepts writes (172 pause). Throws a typed ErrorInfo on failure. */
export async function getOpsStatus(): Promise<OpsStatus> {
  const res = await opsApi.GET("/ops/v1/status");
  if (res.error !== undefined || !res.response.ok) {
    throw parseApiError(res.response.status, res.error) as ErrorInfo;
  }
  return res.data as OpsStatus;
}
