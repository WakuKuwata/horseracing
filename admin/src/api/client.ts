import createClient from "openapi-fetch";

import type { paths } from "./schema";

// Same-origin baseUrl: the SPA calls {origin}/api/v1/* — routed by the Vite dev proxy (no CORS
// change). We use the absolute origin (not a bare relative "") so fetch works identically under
// jsdom/undici in tests, where relative URLs cannot be parsed without a base.
const baseUrl = typeof window !== "undefined" ? window.location.origin : "";
// Defer to the live global fetch on every call (don't let openapi-fetch capture a stale reference).
// In production this is a no-op wrapper; in tests it ensures MSW's patched fetch is the one used.
export const api = createClient<paths>({
  baseUrl,
  fetch: (...args) => globalThis.fetch(...args),
});

export type ErrorInfo = { status: number; code: string; detail: string };

/**
 * Defensive error parser. The 014 API returns the {status, code, detail} ErrorBody for typed
 * errors, but FastAPI's default validation body is {detail: [...]}. Accept BOTH so the UI never
 * shows a blank/garbled error.
 */
export function parseApiError(status: number, body: unknown): ErrorInfo {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.code === "string" && typeof b.detail === "string") {
      return { status: typeof b.status === "number" ? b.status : status, code: b.code, detail: b.detail };
    }
    if (Array.isArray(b.detail)) {
      return { status, code: "validation_error", detail: JSON.stringify(b.detail) };
    }
    if (typeof b.detail === "string") {
      return { status, code: "error", detail: b.detail };
    }
  }
  return { status, code: "error", detail: `HTTP ${status}` };
}
