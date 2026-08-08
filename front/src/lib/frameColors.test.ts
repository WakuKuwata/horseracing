import { describe, expect, it } from "vitest";

import { frameChipClass } from "./frameColors";

describe("frameChipClass", () => {
  it("maps frames 1..8 to their fixed JRA color classes", () => {
    for (let frame = 1; frame <= 8; frame++) {
      expect(frameChipClass(frame)).toBe(`frame-chip frame-chip--${frame}`);
    }
  });

  it("degrades everything else to the neutral chip", () => {
    for (const bad of [null, undefined, 0, 9, -1, 1.5, Number.NaN]) {
      expect(frameChipClass(bad as number | null | undefined)).toBe(
        "frame-chip frame-chip--none",
      );
    }
  });
});
