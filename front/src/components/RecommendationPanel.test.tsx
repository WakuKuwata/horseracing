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

  // --- Feature 064: honest decision-support display ------------------------------------------
  it("always shows the neutral no-edge note with no profit language", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    const note = await screen.findByTestId("no-edge-note");
    expect(note).toHaveTextContent("市場に対する再現可能な優位を持ちません");
    expect(note).toHaveTextContent("将来の的中・利益を示すものではありません");
    // no profit language anywhere in the panel
    expect(container.textContent).not.toMatch(/儲か|勝てる|稼げる/);
  });

  it("shows honest baselines (no-bet ×1.00 + favorite) without P/L coloring or ranking", async () => {
    server.use(
      http.get(`${BASE}/races/:id/recommendations`, () =>
        HttpResponse.json({
          ...recommendationResponse,
          win_policy_status: "generated",
          favorite_baseline: {
            horse_number: 3, odds: 2.0, settled: true, hit: false, dead_heat: false,
            realized_return: 0.0, realized_roi: -1.0,
          },
        }),
      ),
    );
    const { container } = renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    const baselines = await screen.findByTestId("win-baselines");
    expect(baselines).toHaveTextContent("賭けない");
    expect(baselines).toHaveTextContent("×1.00");
    expect(baselines).toHaveTextContent("本命ベタ買い");
    // baselines carry no profit/loss coloring hooks (no data-result) — neutral facts only
    expect(baselines.querySelectorAll("[data-result]").length).toBe(0);
    // odds-band breakdown of the displayed settled win rows is present
    expect(container.querySelector('[data-testid="win-odds-band"]')).not.toBeNull();
  });

  it("surfaces an honest skip reason instead of a blank when the win policy selected nothing", async () => {
    server.use(
      http.get(`${BASE}/races/:id/recommendations`, () =>
        HttpResponse.json({
          ...recommendationResponse,
          win_policy_status: "no_win_selected",
          items: recommendationResponse.items.filter((i) => i.bet_type !== "win"),
        }),
      ),
    );
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    const skip = await screen.findByTestId("win-skip-reason");
    expect(skip).toHaveTextContent("単勝は見送り");
  });
});
