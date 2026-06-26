import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import { happyHandlers } from "../tests/fixtures";
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
    // win probability rendered as percentage
    expect(await screen.findByText("32.0%")).toBeInTheDocument();
    // run audit surfaces which prediction_run was selected (constitution V)
    expect(screen.getByText("run-abc")).toBeInTheDocument();
    expect(screen.getByText("lgbm-006")).toBeInTheDocument();
  });
});
