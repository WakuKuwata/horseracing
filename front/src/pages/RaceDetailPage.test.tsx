import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import {
  happyHandlers,
  http,
  HttpResponse,
  raceDetail,
  recommendationResponse,
} from "../tests/fixtures";
import { renderWithProviders } from "../tests/utils";
import { RaceDetailPage } from "./RaceDetailPage";

function renderDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="/races/:raceId" element={<RaceDetailPage />} />
    </Routes>,
    { route: "/races/200806010111" },
  );
}

describe("RaceDetailPage", () => {
  it("renders predictions and the prediction-run audit", async () => {
    server.use(...happyHandlers);
    renderDetail();
    // win probability rendered as percentage in the entries table.
    expect((await screen.findAllByText("32.0%")).length).toBeGreaterThan(0);
    // run audit surfaces which prediction_run was selected (constitution V)
    expect(screen.getByText("run-abc")).toBeInTheDocument();
    // model_version appears in the run audit and the calibration panel
    expect(screen.getAllByText("lgbm-006").length).toBeGreaterThan(0);
  });

  it("surfaces prediction API errors without hiding the race entries", async () => {
    server.use(...happyHandlers);
    server.use(
      http.get("*/api/v1/races/:id/predictions", () =>
        HttpResponse.json(
          { status: 503, code: "prediction_unavailable", detail: "prediction fetch failed" },
          { status: 503 },
        ),
      ),
    );
    renderDetail();

    expect(await screen.findByRole("alert")).toHaveTextContent("prediction_unavailable");
    expect(screen.getByText("prediction fetch failed")).toBeInTheDocument();
    expect(await screen.findByText("h1")).toBeInTheDocument();
  });

  // Feature 087 (T013A, codex C1): the betting-slip horse names/frames come from the RACE
  // DETAIL response — the prediction response has no horse_name/frame at all, so a slip that
  // renders names proves the wiring uses raceQuery, not predQuery.
  it("feeds the betting slip from the race-detail entries (names + frame colors)", async () => {
    server.use(
      http.get("*/api/v1/races/:id", () =>
        HttpResponse.json({
          ...raceDetail,
          horses: [
            { horse_id: "h1", horse_number: 1, entry_status: "active", horse_name: "サンプルホース", frame: 3 },
            { horse_id: "h2", horse_number: 2, entry_status: "active", horse_name: null, frame: null },
          ],
        }),
      ),
      http.get("*/api/v1/races/:id/recommendations", () =>
        HttpResponse.json({
          ...recommendationResponse,
          items: [
            { ...recommendationResponse.items[1], recommendation_id: "rec-w1", settled: false,
              hit: undefined, counterfactual_snapshot_gross_return: undefined,
              counterfactual_snapshot_net_return: undefined },
          ],
        }),
      ),
      // overrides FIRST — MSW resolves handlers first-match-wins
      ...happyHandlers,
    );
    renderDetail(); // the 買い目推奨 tab is the default tab

    const card = await screen.findByTestId("bet-slip-card-rec-w1");
    expect(within(card).getByText("サンプルホース")).toBeInTheDocument();
    expect(card.querySelector(".frame-chip--3")).toHaveTextContent("1");
  });
});
