import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { DiagnosticsPage } from "./DiagnosticsPage";

const BASE = "*/api/v1";

const response = {
  kind: "segment_edge",
  computed_at: "2026-07-03T10:00:00Z",
  date_from: "2021-01-01",
  date_to: "2025-10-26",
  logic_version: "diag=segment_edge;axes=047-preregistered;v=diag-0.1.0",
  n_horses: 181341,
  note: "SECONDARY diagnostic (047). Not a buy signal.",
  rows: [
    { axis: "q_band", segment: "0.05-0.15", n: 50000, win_rate: 0.06,
      logloss_p: 0.24, logloss_q: 0.2275, gap: 0.0125, mean_p: 0.07, mean_q: 0.08 },
    { axis: "q_band", segment: "q>=0.30(本命)", n: 12000, win_rate: 0.413,
      logloss_p: 0.65, logloss_q: 0.419, gap: 0.2306, mean_p: 0.185, mean_q: 0.405 },
    { axis: "surface", segment: "芝", n: 90000, win_rate: 0.08,
      logloss_p: 0.234, logloss_q: 0.202, gap: 0.032, mean_p: 0.08, mean_q: 0.085 },
  ],
};

describe("DiagnosticsPage", () => {
  it("renders per-axis tables in PERSISTED order with freshness + disclaimer", async () => {
    server.use(http.get(`${BASE}/diagnostics/segment-edge`, () => HttpResponse.json(response)));
    const { container } = renderWithProviders(<DiagnosticsPage />);
    await screen.findByText("q_band");
    expect(screen.getByText("surface")).toBeInTheDocument();
    // disclaimer + freshness always on screen
    expect(container.textContent).toContain("SECONDARY");
    expect(container.textContent).toContain("2021-01-01");
    expect(screen.getByText("181,341")).toBeInTheDocument();
    // rows stay in persisted (pre-registered) order — NOT sorted by gap: within q_band,
    // 0.05-0.15 (gap +0.0125) comes BEFORE q>=0.30 (gap +0.2306)
    const cells = [...container.querySelectorAll("td:first-child")].map((c) => c.textContent);
    expect(cells.indexOf("0.05-0.15")).toBeLessThan(cells.indexOf("q>=0.30(本命)"));
    expect(container.textContent).not.toContain("NaN");
  });

  it("shows the CLI instruction when nothing is persisted (typed 404)", async () => {
    server.use(http.get(`${BASE}/diagnostics/segment-edge`, () =>
      HttpResponse.json(
        { status: 404, code: "diagnostic_unavailable", detail: "none" }, { status: 404 })));
    const { container } = renderWithProviders(<DiagnosticsPage />);
    await screen.findByText(/永続化された診断がまだありません/);
    expect(container.textContent).toContain("segment-diagnostic");
    expect(container.querySelector('[data-code="diagnostic_unavailable"]')).not.toBeNull();
  });
});
