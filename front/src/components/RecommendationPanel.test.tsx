import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import { happyHandlers, http, HttpResponse, recommendationResponse } from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { renderWithProviders } from "../tests/utils";
import { RecommendationPanel } from "./RecommendationPanel";

const BASE = "*/api/v1";

describe("RecommendationPanel", () => {
  it("badges pseudo_odds, pseudo_roi and estimated used-odds (no figure unlabelled)", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(
      <RecommendationPanel raceId="200806010111" />,
    );

    // pseudo_roi 0.18 → 18.0%, pseudo_odds 4.5 → ×4.5, estimated used odds 12.3 → ×12.3
    await screen.findByText("18.0%");
    assertPseudoLabelCoverage(container, ["18.0%", "×4.5", "×12.3"]);
  });

  it("shows Kelly stake_fraction as a labelled (double-pseudo) figure (043)", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(
      <RecommendationPanel raceId="200806010111" />,
    );
    // stake_fraction 0.0123 → 1.23%; estimated row → double-pseudo, must be labelled (never bare)
    await screen.findByText("1.23%");
    assertPseudoLabelCoverage(container, ["1.23%"]);
  });

  it("shows realized win backtest (的中/不的中 + real return) without a pseudo badge (049)", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(
      <RecommendationPanel raceId="200806010111" />,
    );
    // settled win rows: one hit (的中, ×3.2 real return), one miss (不的中)
    await screen.findByTestId("win-backtest-summary");
    expect(container.querySelector('[data-result="hit"]')).toHaveTextContent("的中");
    expect(container.querySelector('[data-result="miss"]')).toHaveTextContent("不的中");
    // realized figures are REAL — they must NOT be tagged pseudo (no data-pseudo on result cells)
    const resultCells = container.querySelectorAll('[data-result]');
    expect(resultCells.length).toBeGreaterThan(0);
    resultCells.forEach((el) => {
      expect(el.closest('[data-pseudo="true"]')).toBeNull();
    });
    // the existing pseudo coverage invariant still holds across the whole panel
    assertPseudoLabelCoverage(container, ["18.0%", "×4.5", "×12.3"]);
  });

  it("shows a retrospective win summary labelled as past/参考, not a projection (049 US2)", async () => {
    server.use(...happyHandlers);
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    const summary = await screen.findByTestId("win-backtest-summary");
    // 1 hit of 2 settled → 的中率 50.0%, recovery ×1.60 (=(3.2+0)/2)
    expect(summary).toHaveTextContent("50.0%");
    expect(summary).toHaveTextContent("×1.60");
    expect(summary).toHaveTextContent("将来の的中・利益を示すものではありません");
  });

  it("shows the empty state when there are no recommendations", async () => {
    server.use(
      http.get(`${BASE}/races/:id/recommendations`, () =>
        HttpResponse.json({ ...recommendationResponse, items: [] }),
      ),
    );
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    expect(await screen.findByText("この条件の推奨はありません")).toBeInTheDocument();
  });
});
