/**
 * Feature 051: admin openapi contract checks (mirrors front/src/api/openapi.test.ts).
 * The committed admin/openapi.json is the SAME 014 read-only contract the front pins —
 * key-sorted, GET-only, and containing the endpoints the admin SPA consumes.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const raw = readFileSync(resolve(__dirname, "../../openapi.json"), "utf8");
const spec = JSON.parse(raw) as { paths: Record<string, Record<string, unknown>> };

describe("admin openapi snapshot", () => {
  it("contains the endpoints the admin SPA consumes", () => {
    const paths = Object.keys(spec.paths);
    for (const p of [
      "/api/v1/models",
      "/api/v1/models/{model_version}/calibration",
      "/api/v1/models/{model_version}/importance",
    ]) {
      expect(paths).toContain(p);
    }
  });

  it("contract is read-only: every operation is a GET (no write verbs)", () => {
    const writeVerbs = ["post", "put", "patch", "delete"];
    for (const [path, ops] of Object.entries(spec.paths)) {
      for (const verb of writeVerbs) {
        expect(ops[verb], `${verb.toUpperCase()} ${path} must not exist`).toBeUndefined();
      }
    }
  });

  it("matches the front snapshot byte-for-byte (single 014 contract, no fork)", () => {
    const front = readFileSync(resolve(__dirname, "../../../front/openapi.json"), "utf8");
    expect(raw).toBe(front);
  });
});

describe("admin ops-openapi snapshot (Feature 053)", () => {
  const opsRaw = readFileSync(resolve(__dirname, "../../ops-openapi.json"), "utf8");
  const opsSpec = JSON.parse(opsRaw) as { paths: Record<string, Record<string, unknown>> };

  it("contains the ops action the admin SPA enqueues", () => {
    expect(Object.keys(opsSpec.paths)).toContain("/ops/v1/refresh-range");
    expect(opsSpec.paths["/ops/v1/refresh-range"].post).toBeDefined();
  });

  it("matches the front ops snapshot byte-for-byte (single 024/053 contract, no fork)", () => {
    const front = readFileSync(resolve(__dirname, "../../../front/ops-openapi.json"), "utf8");
    expect(opsRaw).toBe(front);
  });
});
