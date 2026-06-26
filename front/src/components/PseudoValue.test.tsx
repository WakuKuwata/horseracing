import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { PseudoValue, SourceBadge } from "./PseudoValue";

describe("PseudoValue", () => {
  it("stamps data-pseudo and renders a badge for every kind", () => {
    const { container } = render(
      <>
        <PseudoValue kind="estimated">×12.3</PseudoValue>
        <PseudoValue kind="pseudo">×4.5</PseudoValue>
        <PseudoValue kind="double_pseudo">-0.12</PseudoValue>
      </>,
    );
    // coverage: every listed pseudo value is badged, and every badged node has a badge chip.
    assertPseudoLabelCoverage(container, ["×12.3", "×4.5", "-0.12"]);
  });

  it("SourceBadge: real source is NOT data-pseudo, estimated source IS", () => {
    const { container } = render(
      <>
        <SourceBadge source="real" coverageScope="full" />
        <SourceBadge source="estimated" />
      </>,
    );
    expect(container.querySelector('[data-source="real"]')).not.toBeNull();
    // the estimated SourceBadge degrades to a pseudo "推定" badge.
    expect(container.querySelector('[data-pseudo-badge="estimated"]')).not.toBeNull();
  });
});
