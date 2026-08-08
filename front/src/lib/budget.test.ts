import { describe, expect, it } from "vitest";

import { computeAmount, formatYen, summarizeAmounts, validateBudget } from "./budget";

describe("computeAmount (display contract §1)", () => {
  it("floors to the ¥100 unit", () => {
    expect(computeAmount(0.024, 10000)).toEqual({ kind: "amount", yen: 200 });
    expect(computeAmount(0.0123, 10000)).toEqual({ kind: "amount", yen: 100 });
  });

  it("snaps float-error boundaries instead of losing a whole ¥100 unit (codex D2)", () => {
    // 0.036 * 25000 === 899.9999999999999 in JS — naive floor loses ¥100.
    expect(computeAmount(0.036, 25000)).toEqual({ kind: "amount", yen: 900 });
    // 0.35 * 22000 === 7699.999999999999 — naive floor gives ¥7,600.
    expect(computeAmount(0.35, 22000)).toEqual({ kind: "amount", yen: 7700 });
  });

  it("does NOT snap genuine sub-unit values", () => {
    // 899.9 is a real value 0.1 yen below the boundary — floors to 800.
    expect(computeAmount(0.08999, 10000)).toEqual({ kind: "amount", yen: 800 });
  });

  it("returns too_small under ¥100 (never a fabricated amount)", () => {
    expect(computeAmount(0.007, 10000)).toEqual({ kind: "too_small" }); // 70円
    expect(computeAmount(0.0099, 10000)).toEqual({ kind: "too_small" }); // 99円
    expect(computeAmount(0, 10000)).toEqual({ kind: "too_small" });
  });

  it("returns exactly ¥100 at the boundary", () => {
    expect(computeAmount(0.01, 10000)).toEqual({ kind: "amount", yen: 100 });
  });

  it("returns reference for null stake_fraction (no invented amount — 案A)", () => {
    expect(computeAmount(null, 10000)).toEqual({ kind: "reference" });
    expect(computeAmount(undefined, 10000)).toEqual({ kind: "reference" });
  });

  it("only ever produces multiples of 100", () => {
    for (const f of [0.001, 0.0155, 0.033, 0.21, 0.5, 0.987]) {
      const vm = computeAmount(f, 12345);
      if (vm.kind === "amount") expect(vm.yen % 100).toBe(0);
    }
  });
});

describe("validateBudget (acceptance is separate from arithmetic — codex M1)", () => {
  it("accepts positive integers >= 100, multiples NOT enforced", () => {
    expect(validateBudget(100)).toBe(100);
    expect(validateBudget(550)).toBe(550);
    expect(validateBudget("10000")).toBe(10000);
  });

  it("rejects everything else as null", () => {
    for (const bad of ["", "99", 99, 0, -100, 100.5, NaN, Infinity, "1e4", "abc", "100.5", Number.MAX_SAFE_INTEGER + 1]) {
      expect(validateBudget(bad)).toBeNull();
    }
  });
});

describe("summarizeAmounts (display contract §2)", () => {
  it("sums only kind=amount rows and counts them", () => {
    const s = summarizeAmounts(
      [
        { kind: "amount", yen: 200 },
        { kind: "too_small" },
        { kind: "reference" },
        { kind: "amount", yen: 100 },
      ],
      10000,
    );
    expect(s.totalYen).toBe(300);
    expect(s.count).toBe(2);
    expect(s.budgetPct).toBe(3);
    expect(s.overBudget).toBe(false);
  });

  it("rounds the budget percentage to an integer (0% allowed)", () => {
    expect(summarizeAmounts([{ kind: "amount", yen: 400 }], 1000000).budgetPct).toBe(0);
    expect(summarizeAmounts([{ kind: "amount", yen: 5500 }], 10000).budgetPct).toBe(55);
  });

  it("reports over-budget without shrinking anything", () => {
    const s = summarizeAmounts(
      [
        { kind: "amount", yen: 8000 },
        { kind: "amount", yen: 4000 },
      ],
      10000,
    );
    expect(s.totalYen).toBe(12000); // amounts untouched — neutral wording is the only response
    expect(s.overBudget).toBe(true);
  });

  it("yields ¥0 / 0 bets when every row is too_small or reference", () => {
    const s = summarizeAmounts([{ kind: "too_small" }, { kind: "reference" }], 10000);
    expect(s.totalYen).toBe(0);
    expect(s.count).toBe(0);
  });
});

describe("formatYen (single ja-JP path)", () => {
  it("formats with the ¥ prefix and thousands separators", () => {
    expect(formatYen(0)).toBe("¥0");
    expect(formatYen(100)).toBe("¥100");
    expect(formatYen(12000)).toBe("¥12,000");
  });
});
