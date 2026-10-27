import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { PredictionResponse } from "../api/types";
import { happyHandlers, http, HttpResponse, marketEvAvailable, raceDetail } from "../tests/fixtures";
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

beforeEach(() => {
  server.use(http.get("*/api/v1/purchase-records", () => HttpResponse.json({ records: [] })));
});

describe("RaceDetailPage model switching (057)", () => {
  it("keeps ROI values unchanged through model switching and preserves selection across tabs", async () => {
    const marketUrls: string[] = [];
    const predictionUrls: string[] = [];
    server.use(
      http.get("*/api/v1/races/:id", () => HttpResponse.json({ ...raceDetail,
        horses: [{ horse_id: "h1", horse_number: 1, entry_status: "started" }],
      })),
      http.get("*/api/v1/races/:id/market-ev", ({ request }) => {
        marketUrls.push(request.url);
        return HttpResponse.json(marketEvAvailable);
      }),
      http.get("*/api/v1/races/:id/predictions", ({ request }) => {
        predictionUrls.push(request.url);
        return HttpResponse.json(new URL(request.url).searchParams.get("model_version") === "m-acc" ? accResp : activeResp);
      }),
      ...happyHandlers,
    );
    renderDetail();
    const roi = await screen.findByRole("table", { name: "期待回収率の出走表" });
    expect(await within(roi).findByText("123.7%")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "予測モデルとの比較" }));
    await screen.findByTestId("model-select");
    await userEvent.selectOptions(screen.getByTestId("model-select"), "m-acc");
    expect(await screen.findByTestId("viewing-non-adopted")).toBeInTheDocument();
    expect(within(roi).getByText("123.7%")).toBeVisible();
    expect(within(roi).queryByText("50.0%")).toBeNull();
    expect(predictionUrls.every((url) => !new URL(url).searchParams.has("bet_type"))).toBe(true);
    await userEvent.click(screen.getByText("モデル情報・結合確率", { selector: "summary" }));
    await waitFor(() => expect(predictionUrls.some((url) => {
      const params = new URL(url).searchParams;
      return params.get("model_version") === "m-acc" && params.get("bet_type") === "quinella";
    })).toBe(true));
    await userEvent.click(screen.getByRole("tab", { name: "オッズ" }));
    await userEvent.click(screen.getByRole("tab", { name: "予測モデルとの比較" }));
    expect(screen.getByTestId("model-select")).toHaveValue("m-acc");
    expect(marketUrls.length).toBeGreaterThan(0);
    expect(marketUrls.every((url) => new URL(url).search === "")).toBe(true);
    expect(predictionUrls.some((url) => new URL(url).searchParams.get("model_version") === "m-acc")).toBe(true);
  });

  it("defaults to the adopted model with an adopted badge and marks it in the selector", async () => {
    server.use(...happyHandlers);
    server.use(predictionsHandler((mv) => HttpResponse.json(mv === "m-acc" ? accResp : activeResp)));
    renderDetail();
    await userEvent.click(screen.getByRole("tab", { name: "予測モデルとの比較" }));

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
    await userEvent.click(screen.getByRole("tab", { name: "予測モデルとの比較" }));

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
    await userEvent.click(screen.getByRole("tab", { name: "予測モデルとの比較" }));

    await screen.findByTestId("model-select");
    await userEvent.selectOptions(screen.getByTestId("model-select"), "m-acc");

    expect(await screen.findByTestId("model-unavailable")).toBeInTheDocument();
    // distinct from the generic error alert
    expect(within(screen.getByRole("region", { name: "予測モデルとの比較" })).queryByRole("alert")).toBeNull();
    expect(within(screen.getByRole("table", { name: "期待回収率の出走表" })).queryByRole("alert")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "採用モデルの表示へ戻る" }));
    expect(await screen.findByTestId("viewing-adopted")).toBeInTheDocument();
    expect(screen.queryByTestId("model-unavailable")).toBeNull();
  });
});
