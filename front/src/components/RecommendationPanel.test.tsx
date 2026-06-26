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
