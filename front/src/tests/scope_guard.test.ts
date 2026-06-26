// @vitest-environment node
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const srcDir = fileURLToPath(new URL("../", import.meta.url));

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    return /\.(ts|tsx)$/.test(e.name) ? [p] : [];
  });
}

describe("read-only scope guard", () => {
  // Exclude test files — they may construct mock write responses for assertions.
  const sources = walk(srcDir).filter((p) => !/\.test\.tsx?$/.test(p));

  it("never invokes a write HTTP method via the typed client", () => {
    const offenders: string[] = [];
    for (const file of sources) {
      const text = readFileSync(file, "utf8");
      if (/\bapi\.(POST|PUT|PATCH|DELETE)\b/.test(text)) {
        offenders.push(file);
      }
    }
    expect(offenders, `write-method calls found in: ${offenders.join(", ")}`).toEqual([]);
  });

  it("only ever calls api.GET", () => {
    const methodCalls = new Set<string>();
    for (const file of sources) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(/\bapi\.([A-Z]+)\b/g)) methodCalls.add(m[1]);
    }
    for (const m of methodCalls) expect(m).toBe("GET");
  });
});
