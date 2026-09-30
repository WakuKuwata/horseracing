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
        <PseudoValue kind="market_q">30.0%</PseudoValue>
        <PseudoValue kind="expected_return">124.3%</PseudoValue>
      </>,
    );
    // coverage: every listed pseudo value is badged, and every badged node has a badge chip.
    assertPseudoLabelCoverage(container, ["×12.3", "×4.5", "-0.12", "30.0%", "124.3%"]);
  });

  it("expected_return (137) is labelled 推定 with the market-aware-model disclosure", () => {
    const { container } = render(<PseudoValue kind="expected_return">124.3%</PseudoValue>);
    const node = container.querySelector('[data-pseudo-kind="expected_return"]');
    expect(node).not.toBeNull();
    const badge = node?.querySelector('[data-pseudo-badge="expected_return"]');
    expect(badge).toHaveTextContent("推定");
    expect(badge?.getAttribute("title")).toBe(
      "市場連動モデルの推定(勝率×単勝オッズ)。実績ではありません",
    );
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
