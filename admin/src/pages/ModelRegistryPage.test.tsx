import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { happyHandlers, http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { ModelRegistryPage } from "./ModelRegistryPage";

const BASE = "*/api/v1";

describe("ModelRegistryPage", () => {
  it("shows the active model first with its OOS metrics and 運用中 badge", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(<ModelRegistryPage />);
    await screen.findByText("lgbm-042");
    expect(screen.getByText("運用中")).toBeInTheDocument();
    expect(screen.getByText("0.21706")).toBeInTheDocument();   // win LogLoss transcribed
    expect(screen.getByText("2025-10-25")).toBeInTheDocument(); // train_through (050)
    expect(container.querySelector('tr[data-active="true"]')).not.toBeNull();
  });

  it("renders metric-less models with em-dashes, never NaN (null-safe)", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(<ModelRegistryPage />);
    await screen.findByText("lgbm-old");
    expect(container.textContent).not.toContain("NaN");
    // the lgbm-old row has null metrics → at least one placeholder cell present
    expect(container.textContent).toContain("—");
  });

  it("shows the typed empty state when no models exist", async () => {
    server.use(http.get(`${BASE}/models`, () => HttpResponse.json({ items: [] })));
    renderWithProviders(<ModelRegistryPage />);
    expect(await screen.findByText("モデルがまだ登録されていません")).toBeInTheDocument();
  });
});
