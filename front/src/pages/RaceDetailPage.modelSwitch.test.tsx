import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { PredictionResponse } from "../api/types";
import { happyHandlers, http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { RaceDetailPage } from "./RaceDetailPage";

const AVAIL = [
  { model_version: "m-active", display_name: "意思決定支援", purpose: "独立予測",
    adoption_status: "active", is_selected: true },
  { model_version: "m-acc", display_name: "精度最優先", purpose: "オッズ込み",
    adoption_status: "candidate", is_selected: false },
];

const activeResp: PredictionResponse = {
  race_id: "200806010111",
  horses: [{ horse_id: "h1", horse_number: 1, win: 0.32, top2: 0.5, top3: 0.7,
             market_win_prob: null, prior_starts_band: "many", divergence: null, explanation: null }],
  joint: null, joint_bet_type: null, joint_logic_version: null,
  market_prob_source: "win_odds_vote_share", canonical_consistent: true,
  odds_as_of: "2008-06-01T05:00:00Z", odds_source: "final",
  run: { prediction_run_id: "run-active", logic_version: "lv", model_version: "m-active",
         computed_at: "2008-05-31T22:00:00Z" },
  available_models: AVAIL,
};

const accResp: PredictionResponse = {
  ...activeResp,
  horses: [{ ...activeResp.horses[0], win: 0.5 }],  // different value to detect the switch
  run: { ...activeResp.run!, prediction_run_id: "run-acc", model_version: "m-acc" },
  available_models: AVAIL.map((m) => ({ ...m, is_selected: m.model_version === "m-acc" })),
};

function predictionsHandler(onModel: (mv: string | null) => Response) {
  return http.get("*/api/v1/races/:id/predictions", ({ request }) =>
    onModel(new URL(request.url).searchParams.get("model_version")),
  );
}

function renderDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="/races/:raceId" element={<RaceDetailPage />} />
    </Routes>,
    { route: "/races/200806010111" },
  );
}

describe("RaceDetailPage model switching (057)", () => {
  it("defaults to the adopted model with an adopted badge and marks it in the selector", async () => {
    server.use(...happyHandlers);
    server.use(predictionsHandler((mv) => HttpResponse.json(mv === "m-acc" ? accResp : activeResp)));
    renderDetail();

    expect(await screen.findByTestId("model-select")).toBeInTheDocument();
    expect(screen.getByTestId("viewing-adopted")).toHaveTextContent("採用モデルを表示中");
    // adopted model marked "（採用）" in its option
    expect(screen.getByRole("option", { name: /意思決定支援（採用）/ })).toBeInTheDocument();
    expect((await screen.findAllByText("32.0%")).length).toBeGreaterThan(0);
  });

  it("switching to a non-adopted model refetches and flips the badge; adopted stays marked", async () => {
    server.use(...happyHandlers);
    server.use(predictionsHandler((mv) => HttpResponse.json(mv === "m-acc" ? accResp : activeResp)));
    renderDetail();

    await screen.findByTestId("model-select");
    await userEvent.selectOptions(screen.getByTestId("model-select"), "m-acc");

    // refetched m-acc's prediction (0.5) and now viewing a non-adopted model
    expect((await screen.findAllByText("50.0%")).length).toBeGreaterThan(0);
    expect(screen.getByTestId("viewing-non-adopted")).toBeInTheDocument();
    // C1: the adopted model is still marked "（採用）" even though a non-adopted one is selected
    expect(screen.getByRole("option", { name: /意思決定支援（採用）/ })).toBeInTheDocument();
  });

  it("shows a distinct 未生成 state (not an alert) when the selected model has no run (404)", async () => {
    server.use(...happyHandlers);
    server.use(
      predictionsHandler((mv) =>
        mv === "m-acc"
          ? HttpResponse.json(
              { status: 404, code: "prediction_unavailable", detail: "no run" },
              { status: 404 },
            )
          : HttpResponse.json(activeResp),
      ),
    );
    renderDetail();

    await screen.findByTestId("model-select");
    await userEvent.selectOptions(screen.getByTestId("model-select"), "m-acc");

    expect(await screen.findByTestId("model-unavailable")).toBeInTheDocument();
    // distinct from the generic error alert
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
