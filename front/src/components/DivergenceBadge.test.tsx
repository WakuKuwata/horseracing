import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DivergenceBadge } from "./DivergenceBadge";

// Words that would turn a neutral comparison into a bet signal — must NEVER appear (021/040 US3).
const FORBIDDEN = ["危険", "妙味", "買い", "儲か", "弱気", "強気", "edge", "バリュー", "推奨"];

describe("DivergenceBadge", () => {
  it("renders neutral factual labels only", () => {
    const { rerender } = render(<DivergenceBadge divergence="market_higher" />);
    expect(screen.getByText("市場評価がモデルより高い")).toBeInTheDocument();
    rerender(<DivergenceBadge divergence="model_higher" />);
    expect(screen.getByText("モデル評価が市場より高い")).toBeInTheDocument();
    rerender(<DivergenceBadge divergence="similar" />);
    expect(screen.getByText("ほぼ同等")).toBeInTheDocument();
  });

  it("renders nothing when divergence is null (suppressed)", () => {
    const { container } = render(<DivergenceBadge divergence={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("INVARIANT: uses no buy-signal / sentiment wording", () => {
    for (const d of ["market_higher", "model_higher", "similar"] as const) {
      const { container } = render(<DivergenceBadge divergence={d} />);
      for (const w of FORBIDDEN) expect(container.textContent).not.toContain(w);
    }
  });

  it("tooltip states it is an opinion difference, not a guarantee, with odds as-of", () => {
    render(<DivergenceBadge divergence="model_higher" oddsAsOf="2008-06-01T05:00:00Z" />);
    const badge = screen.getByText("モデル評価が市場より高い");
    expect(badge.getAttribute("title")).toMatch(/保証するものではありません/);
    expect(badge.getAttribute("title")).toContain("2008-06-01T05:00:00Z");
  });
});
