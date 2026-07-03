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
  days: [
    { date: "2025-01-05", n_races: 24, n_with_odds: 24, n_with_results: 24,
      n_predicted_active: 24, n_with_recommendations: 24 },
    { date: "2025-01-06", n_races: 24, n_with_odds: 24, n_with_results: 24,
      n_predicted_active: 0, n_with_recommendations: 0 },  // a HOLE
  ],
};

describe("CoveragePage", () => {
  it("renders per-day counts and highlights days with prediction holes", async () => {
    server.use(http.get(`${BASE}/coverage`, () => HttpResponse.json(coverage)));
    const { container } = renderWithProviders(<CoveragePage />);
    await screen.findByText("2025-01-05");
    expect(screen.getByText("lgbm-042")).toBeInTheDocument();
    const holes = container.querySelectorAll('tr[data-hole="true"]');
    expect(holes).toHaveLength(1);                      // only the 01-06 row
    expect(holes[0].textContent).toContain("2025-01-06");
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
