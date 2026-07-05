import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import { happyHandlers, http, HttpResponse } from "../tests/fixtures";
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
});
