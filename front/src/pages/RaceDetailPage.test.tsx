import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import {
  happyHandlers,
  http,
  HttpResponse,
  marketEvAvailable,
  raceDetail,
  recommendationResponse,
} from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
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

  // Feature 137: the page fetches the market-aware expected return on its own (no model_version),
  // puts the note right before the entries table and passes the value into the table column.
  it("wires 期待回収率: note before the table, badged column, API-flagged highlight", async () => {
    const marketEvUrls: string[] = [];
    server.use(
      http.get("*/api/v1/races/:id", () =>
        HttpResponse.json({
          ...raceDetail,
          horses: [
            { horse_id: "h1", horse_number: 1, entry_status: "started", horse_name: "イチ" },
            { horse_id: "h2", horse_number: 2, entry_status: "started", horse_name: "ニ" },
          ],
        }),
      ),
      http.get("*/api/v1/races/:id/market-ev", ({ request }) => {
        marketEvUrls.push(request.url);
        return HttpResponse.json(marketEvAvailable);
      }),
      // overrides FIRST — MSW resolves handlers first-match-wins
      ...happyHandlers,
    );
    const { container } = renderDetail();

    // distinct values (1.237 / 0.864) so no other test's percentage lookup collides
    expect(await screen.findByText("123.7%")).toBeInTheDocument();
    expect(screen.getByText("86.4%")).toBeInTheDocument();
    assertPseudoLabelCoverage(container, ["123.7%", "86.4%"]);

    const note = screen.getByTestId("expected-return-note");
    expect(note).toHaveTextContent("市場連動モデル(mev-binary-v2)");
    const table = container.querySelector("table.entries-table")!;
    // the note sits BEFORE the entries table
    expect(note.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // h1 is flagged by the API → highlighted with the chip; h2 is not
    const flagged = container.querySelectorAll("tr.entry--ev-over");
    expect(flagged).toHaveLength(1);
    expect(within(flagged[0] as HTMLElement).getByText("イチ")).toBeInTheDocument();
    expect(within(flagged[0] as HTMLElement).getByText("120%超")).toBeInTheDocument();

    // independent of the win-model selection: no model_version (or any) query parameter
    expect(marketEvUrls.length).toBeGreaterThan(0);
    for (const u of marketEvUrls) expect(new URL(u).search).toBe("");
  });

  it("default (not computed): reason in the note, no 期待回収率 column", async () => {
    server.use(...happyHandlers);
    renderDetail();
    expect(
      await screen.findByText("このレースの期待回収率はまだ計算されていません"),
    ).toBeInTheDocument();
    expect(screen.queryByText("期待回収率", { selector: "th" })).toBeNull();
  });
});
