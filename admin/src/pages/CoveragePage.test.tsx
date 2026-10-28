import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { CoveragePage } from "./CoveragePage";

const BASE = "*/api/v1";

const coverage = {
  date_from: "2025-01-01",
  date_to: "2025-01-31",
  active_model_version: "lgbm-042",
  market_ev_model_version: "mev-ens15-v1",
  quote_token: "oddsobs=quote",
  days: [
    { date: "2025-01-05", n_races: 24, n_with_odds: 24, n_with_results: 24,
      n_predicted_active: 24, n_with_recommendations: 24,
      n_market_ev_computed: 24, n_market_ev_quote: 7, n_market_ev_mix_available: 23 },
    // B11: the legacy prediction is missing but 期待回収率 is complete → NOT a hole any more
    { date: "2025-01-06", n_races: 24, n_with_odds: 24, n_with_results: 24,
      n_predicted_active: 0, n_with_recommendations: 0,
      n_market_ev_computed: 24, n_market_ev_quote: 0, n_market_ev_mix_available: 24 },
    // B11: 期待回収率 missing for 3 races → the ROI hole
    { date: "2025-01-11", n_races: 24, n_with_odds: 24, n_with_results: 24,
      n_predicted_active: 24, n_with_recommendations: 24,
      n_market_ev_computed: 21, n_market_ev_quote: 21, n_market_ev_mix_available: 21 },
  ],
};

describe("CoveragePage", () => {
  it("renders per-day counts and highlights days with 期待回収率 holes (B11)", async () => {
    server.use(http.get(`${BASE}/coverage`, () => HttpResponse.json(coverage)));
    const { container } = renderWithProviders(<CoveragePage />);
    await screen.findByText("2025-01-05");
    expect(screen.getByText("lgbm-042")).toBeInTheDocument();
    expect(screen.getByText("mev-ens15-v1")).toBeInTheDocument();
    expect(screen.getByText("oddsobs=quote")).toBeInTheDocument();
    const holes = container.querySelectorAll('tr[data-hole="true"]');
    expect(holes).toHaveLength(1);                      // only the 01-11 row (ROI hole)
    expect(holes[0].textContent).toContain("2025-01-11");
    expect(holes[0].textContent).toContain("⚠");
    // the ROI columns are rendered, in order, with the nested counts
    const headers = Array.from(container.querySelectorAll("th")).map((th) => th.textContent);
    expect(headers).toEqual([
      "開催日", "レース", "オッズ", "結果", "期待回収率", "うち発走前 quote", "うち混合可",
      "予測(比較用モデル)", "推奨", "",
    ]);
    const firstRow = container.querySelector("tbody tr");
    const cells = Array.from(firstRow!.querySelectorAll("td")).map((td) => td.textContent);
    expect(cells.slice(1, 9)).toEqual(["24", "24", "24", "24", "7", "23", "24", "24"]);
    // the page says what fills which hole (the refresh button does NOT compute 期待回収率)
    expect(screen.getByTestId("coverage-fill-note").textContent).toContain("market-ev --date");
    expect(container.textContent).not.toContain("NaN");
  });

  it("shows the typed error for a too-wide range (422)", async () => {
    server.use(http.get(`${BASE}/coverage`, () =>
      HttpResponse.json(
        { status: 422, code: "range_too_wide", detail: "range must be <= 400 days" },
        { status: 422 })));
    const { container } = renderWithProviders(<CoveragePage />);
    await screen.findByText(/range must be/);
    expect(container.querySelector('[data-code="range_too_wide"]')).not.toBeNull();
  });

  it("shows 未設定 when no model is active", async () => {
    server.use(http.get(`${BASE}/coverage`, () =>
      HttpResponse.json({ ...coverage, active_model_version: null })));
    renderWithProviders(<CoveragePage />);
    expect(await screen.findByText(/未設定/)).toBeInTheDocument();
  });
});
