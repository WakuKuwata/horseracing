import { describe, expect, it } from "vitest";

import { raceMaxFraction, strengthOf } from "./strength";

describe("strengthOf (display contract §4)", () => {
  it("classifies the three tiers", () => {
    expect(strengthOf(0.03, 0.03)?.label).toBe("厚め"); // ratio 1
    expect(strengthOf(0.015, 0.03)?.label).toBe("標準"); // ratio 0.5
    expect(strengthOf(0.005, 0.03)?.label).toBe("抑え"); // ratio 1/6
  });

  it("treats exact thirds as the LOWER tier despite float error (codex D4)", () => {
    // 0.1/0.3 = 0.33333333333333337 (> 1/3 in floats) — must still be 抑え.
    expect(strengthOf(0.1, 0.3)?.label).toBe("抑え");
    // 0.2/0.3 = 0.6666666666666667 (> 2/3 in floats) — must still be 標準.
    expect(strengthOf(0.2, 0.3)?.label).toBe("標準");
  });

  it("labels all rows 厚め when all fractions are equal (documented semantics)", () => {
    expect(strengthOf(0.02, 0.02)?.label).toBe("厚め");
  });

  it("returns 抑え for f=0 with a positive fMax", () => {
    const s = strengthOf(0, 0.03);
    expect(s?.label).toBe("抑え");
    expect(s?.barPct).toBe(0);
  });

  it("returns null for null/negative/non-finite fractions and fMax<=0 (no NaN ever)", () => {
    expect(strengthOf(null, 0.03)).toBeNull();
    expect(strengthOf(undefined, 0.03)).toBeNull();
    expect(strengthOf(-0.01, 0.03)).toBeNull();
    expect(strengthOf(Number.NaN, 0.03)).toBeNull();
    expect(strengthOf(0.02, 0)).toBeNull();
    expect(strengthOf(0.02, -1)).toBeNull();
    expect(strengthOf(0.02, Number.NaN)).toBeNull();
  });

  it("clamps the bar width to [0, 100]", () => {
    expect(strengthOf(0.06, 0.03)?.barPct).toBe(100);
  });
});

describe("raceMaxFraction", () => {
  it("takes the max over non-null finite fractions across ALL rows", () => {
    expect(raceMaxFraction([0.01, null, 0.03, undefined, 0.02])).toBe(0.03);
  });

  it("returns 0 for all-null / non-positive inputs (strength then hides everywhere)", () => {
    expect(raceMaxFraction([null, undefined])).toBe(0);
    expect(raceMaxFraction([0, -0.5])).toBe(0);
    expect(raceMaxFraction([])).toBe(0);
  });
});
