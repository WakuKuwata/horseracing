import { describe, expect, it } from "vitest";

import { isoDaysAgo, toLocalIsoDate } from "./dates";

describe("isoDaysAgo", () => {
  // 00:30 local is a moment whose UTC calendar day differs from the local one in every zone east
  // of UTC (JST included). That is exactly the window where the old toISOString() implementation
  // returned yesterday and the coverage page's default range silently excluded today.
  const localMidnightish = new Date(2026, 7, 23, 0, 30, 0);
  const zoneSplitsTheDay = localMidnightish.getDate() !== localMidnightish.getUTCDate();

  it.runIf(zoneSplitsTheDay)("follows the local calendar, not the UTC one", () => {
    expect(isoDaysAgo(0, localMidnightish)).toBe("2026-08-23");
    // the naive implementation this replaced would have produced a different day here
    expect(localMidnightish.toISOString().slice(0, 10)).not.toBe("2026-08-23");
  });

  it("counts back whole calendar days", () => {
    const noon = new Date(2026, 7, 23, 12, 0, 0); // local 2026-08-23
    expect(isoDaysAgo(0, noon)).toBe("2026-08-23");
    expect(isoDaysAgo(30, noon)).toBe("2026-07-24");
  });

  it("zero-pads month and day", () => {
    expect(toLocalIsoDate(new Date(2026, 0, 5))).toBe("2026-01-05");
  });

  it("does not mutate the Date it is given", () => {
    const now = new Date(2026, 7, 23, 12, 0, 0);
    isoDaysAgo(30, now);
    expect(now.getDate()).toBe(23);
  });
});
