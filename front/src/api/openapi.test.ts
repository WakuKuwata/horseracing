// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const root = fileURLToPath(new URL("../../", import.meta.url));

function sortKeysDeep(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeysDeep);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value as Record<string, unknown>)
        .sort()
        .map((k) => [k, sortKeysDeep((value as Record<string, unknown>)[k])]),
    );
  }
  return value;
}

describe("openapi snapshot", () => {
  const raw = readFileSync(`${root}openapi.json`, "utf8");
  const spec = JSON.parse(raw) as Record<string, unknown>;

  it("is committed in deterministic key-sorted form (no spurious drift on re-save)", () => {
    const resorted = `${JSON.stringify(sortKeysDeep(spec), null, 2)}\n`;
    expect(raw).toBe(resorted);
  });

  it("exposes exactly the read-only endpoints the SPA consumes", () => {
    const paths = Object.keys((spec.paths as Record<string, unknown>) ?? {}).sort();
    expect(paths).toEqual(
      [
        "/api/v1/health",
        "/api/v1/races",
        "/api/v1/races/{race_id}",
        "/api/v1/races/{race_id}/odds",
        "/api/v1/races/{race_id}/predictions",
        "/api/v1/races/{race_id}/recommendations",
        "/api/v1/models/{model_version}/calibration",
        // Feature 040: split-gain importance (read-only)
        "/api/v1/models/{model_version}/importance",
        // Feature 051: model registry list (read-only; consumed by the admin SPA)
        "/api/v1/models",
        // Feature 052: coverage + job history (read-only; consumed by the admin SPA)
        "/api/v1/coverage",
        "/api/v1/jobs",
        // Feature 054: persisted diagnostics (read-only; consumed by the admin SPA)
        "/api/v1/diagnostics/segment-edge",
        // Feature 029: horse + jockey profile + paged history (read-only)
        "/api/v1/horses/{horse_id}",
        "/api/v1/horses/{horse_id}/history",
        "/api/v1/jockeys/{jockey_id}",
        "/api/v1/jockeys/{jockey_id}/history",
      ].sort(),
    );
  });

  it("contract is read-only: every operation is a GET (no write verbs)", () => {
    const writeVerbs = ["post", "put", "patch", "delete"];
    for (const [path, ops] of Object.entries(spec.paths as Record<string, object>)) {
      for (const verb of Object.keys(ops)) {
        expect(
          writeVerbs.includes(verb.toLowerCase()),
          `${verb.toUpperCase()} ${path} is a write verb — the SPA contract must stay read-only`,
        ).toBe(false);
      }
    }
  });
});
