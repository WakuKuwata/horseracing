import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { happyHandlers, http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { ModelDetailPage } from "./ModelDetailPage";

const BASE = "*/api/v1";

function renderDetail(mv: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/models/:modelVersion" element={<ModelDetailPage />} />
    </Routes>,
    { route: `/models/${mv}` },
  );
}

describe("ModelDetailPage", () => {
  it("shows metadata + adoption verdict + calibration bins + importance", async () => {
    server.use(...happyHandlers);
    renderDetail("lgbm-042");
    await screen.findByText("adopted=True(機械通過)");
    expect(screen.getByText("jockey_place_rate")).toBeInTheDocument();  // importance row
    expect(screen.getByText("0.0480")).toBeInTheDocument();             // realized_rate bin
    expect(screen.getByText("2025-10-25")).toBeInTheDocument();         // train_through
    // Feature 057: purpose metadata rendered
    expect(screen.getByTestId("model-display-name")).toHaveTextContent("意思決定支援モデル");
    expect(screen.getByTestId("model-purpose")).toHaveTextContent("市場から独立した予測");
  });

  it("shows 未収録 for typed 404 calibration_unavailable / importance_unavailable", async () => {
    // NOTE: within one server.use(...) the FIRST matching handler wins — the 404 overrides must
    // come BEFORE the happy handlers.
    server.use(
      http.get(`${BASE}/models/:mv/calibration`, () =>
        HttpResponse.json(
          { status: 404, code: "calibration_unavailable", detail: "none" }, { status: 404 })),
      http.get(`${BASE}/models/:mv/importance`, () =>
        HttpResponse.json(
          { status: 404, code: "importance_unavailable", detail: "none" }, { status: 404 })),
      ...happyHandlers,
    );
    const { container } = renderDetail("lgbm-042");
    await screen.findByText(/reliability が記録されていません/);
    expect(container.querySelector('[data-code="importance_unavailable"]')).not.toBeNull();
  });

  it("shows a not-found message for an unknown model_version", async () => {
    server.use(
      http.get(`${BASE}/models`, () => HttpResponse.json({ items: [] })),
      http.get(`${BASE}/models/:mv/calibration`, () =>
        HttpResponse.json(
          { status: 404, code: "model_not_found", detail: "none" }, { status: 404 })),
      http.get(`${BASE}/models/:mv/importance`, () =>
        HttpResponse.json(
          { status: 404, code: "model_not_found", detail: "none" }, { status: 404 })),
    );
    renderDetail("lgbm-nope");
    expect(await screen.findByText(/登録されていません/)).toBeInTheDocument();
  });
});
