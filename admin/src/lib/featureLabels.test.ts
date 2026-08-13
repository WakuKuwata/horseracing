import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { featureLabel } from "./featureLabels";

// The feature-label table is single-sourced in front/ (Feature 040) and mirrored here so the
// admin console never shows a raw column name where a label exists. Byte-equality with the
// front copy prevents the two tables from forking (same discipline as the openapi snapshots).
describe("featureLabels", () => {
  it("is byte-identical to the front copy (no fork)", () => {
    const mine = readFileSync(resolve(__dirname, "./featureLabels.ts"), "utf8");
    const front = readFileSync(
      resolve(__dirname, "../../../front/src/components/featureLabels.ts"),
      "utf8",
    );
    expect(mine).toBe(front);
  });

  it("fails open for unknown names (shown as-is, never hidden)", () => {
    expect(featureLabel("some_future_column")).toEqual({ label: "some_future_column" });
  });

  it("labels post-040 feature generations (041/056/058/059/061/069)", () => {
    expect(featureLabel("asof_late_gain_avg").label).toBe("直線での順位上げ（平均）");
    expect(featureLabel("asof_pm_support_last").label).toBe("過去走の市場支持（前走）");
    expect(featureLabel("asof_spdfig_best").label).toBe("スピード指数（最良）");
    expect(featureLabel("win_rate_vs_field").label).toBe("勝率（出走馬平均比）");
    expect(featureLabel("asof_mkt_rank_avg").label).toBe("過去走の人気順位（平均）");
    expect(featureLabel("sire_line").label).toBe("父系統");
  });
});
