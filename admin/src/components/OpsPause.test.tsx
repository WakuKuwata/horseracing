import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "../App";
import { happyHandlers, http, HttpResponse } from "../tests/fixtures";
import { OPS_STATUS_ACTIVE, OPS_STATUS_PAUSED, server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { PAUSE_SHORT } from "./OpsPauseBanner";
import { RefreshRangeButton } from "./RefreshRangeButton";

const OPS = "*/ops/v1";

describe("ops pause (B5)", () => {
  it("disables the range refresh with the reason and never reaches the confirm step", async () => {
    server.use(http.get(`${OPS}/status`, () => HttpResponse.json(OPS_STATUS_PAUSED)));
    let posted = 0;
    server.use(http.post(`${OPS}/refresh-range`, () => { posted += 1; return HttpResponse.json({}, { status: 503 }); }));
    renderWithProviders(<RefreshRangeButton dateFrom="2025-01-05" dateTo="2025-01-05" label="この日を更新" />);
    // the paused branch renders a fresh element, so re-query instead of holding a stale node
    await waitFor(() => expect(screen.getByRole("button", { name: "この日を更新" })).toBeDisabled());
    expect(screen.getByTestId("ops-paused-notice")).toHaveTextContent(PAUSE_SHORT);
    await userEvent.click(screen.getByRole("button", { name: "この日を更新" }));
    expect(screen.queryByRole("button", { name: "実行" })).toBeNull();
    expect(posted).toBe(0);
  });

  it("stays enabled when the status endpoint fails (fail-open)", async () => {
    server.use(http.get(`${OPS}/status`, () => HttpResponse.json({ status: 500, code: "x", detail: "x" }, { status: 500 })));
    renderWithProviders(<RefreshRangeButton dateFrom="2025-01-05" dateTo="2025-01-05" label="この日を更新" />);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByRole("button", { name: "この日を更新" })).toBeEnabled();
  });

  it("banner: paused, and separately the wrapper-bypass mismatch", async () => {
    server.use(...happyHandlers); // the index page behind <App/> reads /api/v1/models
    server.use(http.get(`${OPS}/status`, () => HttpResponse.json(OPS_STATUS_PAUSED)));
    const first = renderWithProviders(<App />);
    expect(await screen.findByTestId("ops-pause-banner")).toHaveTextContent("データ取得停止中");
    first.unmount();
    server.use(...happyHandlers);
    server.use(http.get(`${OPS}/status`, () => HttpResponse.json({ ...OPS_STATUS_ACTIVE, policy_mismatch: true })));
    renderWithProviders(<App />);
    expect(await screen.findByTestId("ops-pause-banner")).toHaveTextContent("停止 wrapper を経由せず");
  });
});
