import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RaceDivergenceSummary } from "./RaceDivergenceSummary";
import type { RaceDivergence } from "../api/types";

const AVAILABLE: RaceDivergence = {
  available: true,
  summary: "本命(1番人気)をモデルは市場より低く評価・モデル上位に人気薄あり",
  favorite_direction: "model_lower",
  underrated_longshots: [{ horse_number: 4, popularity_rank: 5, p: 0.3, q: 0.05 }],
  rank_agreement: 2 / 3,
  model_version: "lgbm-061",
};

describe("RaceDivergenceSummary", () => {
  it("renders the neutral summary, longshot facts, and agreement", () => {
    render(<RaceDivergenceSummary divergence={AVAILABLE} />);
    expect(screen.getByTestId("divergence-summary")).toHaveTextContent("低く評価");
    expect(screen.getByTestId("divergence-longshots")).toHaveTextContent("4番");
    expect(screen.getByTestId("divergence-agreement")).toHaveTextContent("66.7%");
    // 057: which model's p is being compared
    expect(screen.getByText("lgbm-061")).toBeInTheDocument();
  });

  it("is suppressed (renders nothing) when unavailable", () => {
    const { container } = render(
      <RaceDivergenceSummary divergence={{ ...AVAILABLE, available: false }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("uses NO buy/edge/value wording and NO P&L colour or sorting", () => {
    const { container } = render(<RaceDivergenceSummary divergence={AVAILABLE} />);
    expect(container.textContent).not.toMatch(/妙味|危険|儲|回収率|edge|買うべき|勝てる|おすすめ/);
    expect(container.querySelector(".good, .bad, .danger, .success, .profit")).toBeNull();
    expect(container.querySelector("button, [role='button']")).toBeNull();
    // explicitly states it is not a verdict / not a buy recommendation
    expect(container.textContent).toMatch(/買い推奨ではありません|どちらが当たるかを示すものではありません/);
  });

  it("renders nothing when divergence is absent", () => {
    const { container } = render(<RaceDivergenceSummary divergence={null} />);
    expect(container.firstChild).toBeNull();
  });
});
