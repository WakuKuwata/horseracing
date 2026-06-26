import { describe, expect, it } from "vitest";

import {
  PLACEHOLDER,
  formatDateTime,
  formatNum,
  formatOdds,
  formatPct,
  formatSelection,
} from "./format";

describe("formatNum", () => {
  it("formats finite numbers with fixed digits", () => {
    expect(formatNum(1.2345)).toBe("1.23");
    expect(formatNum(1.2345, 3)).toBe("1.234");
    expect(formatNum(0)).toBe("0.00");
  });

  it("returns placeholder for null/undefined/NaN", () => {
    expect(formatNum(null)).toBe(PLACEHOLDER);
    expect(formatNum(undefined)).toBe(PLACEHOLDER);
    expect(formatNum(Number.NaN)).toBe(PLACEHOLDER);
  });
});

describe("formatPct", () => {
  it("formats fractions as percentages", () => {
    expect(formatPct(0.123)).toBe("12.3%");
    expect(formatPct(1)).toBe("100.0%");
  });
  it("placeholder for null", () => {
    expect(formatPct(null)).toBe(PLACEHOLDER);
  });
});

describe("formatOdds", () => {
  it("prefixes ×", () => {
    expect(formatOdds(3.4)).toBe("×3.4");
  });
  it("placeholder for null", () => {
    expect(formatOdds(null)).toBe(PLACEHOLDER);
  });
});

describe("formatSelection", () => {
  it("joins with dashes", () => {
    expect(formatSelection([1, 2, 3])).toBe("1-2-3");
  });
  it("placeholder for empty/null", () => {
    expect(formatSelection([])).toBe(PLACEHOLDER);
    expect(formatSelection(null)).toBe(PLACEHOLDER);
  });
});

describe("formatDateTime", () => {
  it("normalizes ISO datetime", () => {
    expect(formatDateTime("2008-06-01T15:40:00Z")).toBe("2008-06-01 15:40:00 UTC");
  });
  it("placeholder for null", () => {
    expect(formatDateTime(null)).toBe(PLACEHOLDER);
  });
});
