import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "../App";
import { OPS_STATUS_CAPTURING, OPS_STATUS_PAUSED, server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { DayRefreshButton } from "./DayRefreshButton";
import { PAUSE_SHORT } from "./OpsPauseNotice";
import { PredictButton } from "./PredictButton";
import { PurchaseActions } from "./PurchaseActions";
import { RecommendButton } from "./RecommendButton";
import { RefreshButton } from "./RefreshButton";

const BASE = "*/ops/v1";
const RID = "202406050911";

function pause() {
  server.use(http.get(`${BASE}/status`, () => HttpResponse.json(OPS_STATUS_PAUSED)));
}

/**
 * B5 (2026-10-27): while ops reports the 172 pause, every write button is disabled and says why.
 * Unknown status (endpoint failing) must NOT disable anything — the pre-B5 behaviour stays.
 */
describe("ops pause (B5)", () => {
  it.each([
    ["データ更新", () => <RefreshButton raceId={RID} pollMs={10} />],
    ["予測する", () => <PredictButton raceId={RID} pollMs={10} />],
    ["買い目生成", () => <RecommendButton raceId={RID} pollMs={10} />],
    ["この日を更新", () => <DayRefreshButton date="2024-12-28" pollMs={10} />],
  ])("disables %s with the pause reason and never POSTs", async (name, render) => {
    pause();
    let posted = 0;
    server.use(http.post(`${BASE}/*`, () => { posted += 1; return HttpResponse.json({}, { status: 503 }); }));
    renderWithProviders(render());
    const button = screen.getByRole("button", { name });
    await waitFor(() => expect(button).toBeDisabled());
    expect(screen.getByTestId("ops-paused-notice")).toHaveTextContent(PAUSE_SHORT);
    await userEvent.click(button);
    expect(posted).toBe(0);
  });

  it("keeps the button enabled when the status endpoint itself fails (fail-open)", async () => {
    server.use(http.get(`${BASE}/status`, () => HttpResponse.json(
      { status: 500, code: "boom", detail: "x" }, { status: 500 })));
    renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);
    // give the (failing) status query a tick; the button must stay usable throughout
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByRole("button", { name: "データ更新" })).toBeEnabled();
    expect(screen.queryByTestId("ops-paused-notice")).toBeNull();
  });

  it("leaves the pre-B5 behaviour when ops is not paused (default handler)", async () => {
    renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByRole("button", { name: "データ更新" })).toBeEnabled();
    expect(screen.queryByTestId("ops-paused-notice")).toBeNull();
  });

  it("disables purchase recording too, saying the record is also blocked", async () => {
    renderWithProviders(
      <PurchaseActions
        paused
        raceId={RID}
        presentedBets={[]}
        predictionRunId={null}
        winPolicy="no_win_selected"
        presentationAvailable={false}
        hasResults={false}
        submit={async () => { throw new Error("must not be called"); }}
        existingRecord={null}
      />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /見送り/ })).toBeDisabled(),
    );
    expect(screen.getByTestId("ops-paused-notice")).toHaveTextContent("購入記録の保存も止まっています");
    expect(screen.getByRole("button", { name: /そのまま購入/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /変更して購入/ })).toBeDisabled();
  });

  it("shows the header banner only while paused", async () => {
    const { unmount } = renderWithProviders(<App />);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("ops-pause-banner")).toBeNull();
    unmount();
    pause();
    renderWithProviders(<App />);
    expect(await screen.findByTestId("ops-pause-banner")).toHaveTextContent("データ取得停止中");
  });
});

/**
 * 2026-10-31: while today's T−3 capture window is open, only the two refresh buttons are disabled
 * (ops answers 409 capture_window); predict / recommend stay usable — they make no source request.
 */
describe("capture window (2026-10-31)", () => {
  function capturing() {
    server.use(http.get(`${BASE}/status`, () => HttpResponse.json(OPS_STATUS_CAPTURING)));
  }

  it.each([
    ["データ更新", () => <RefreshButton raceId={RID} pollMs={10} />],
    ["この日を更新", () => <DayRefreshButton date="2026-10-31" pollMs={10} />],
  ])("disables %s with the window's closing time and never POSTs", async (name, render) => {
    capturing();
    let posted = 0;
    server.use(http.post(`${BASE}/*`, () => { posted += 1; return HttpResponse.json({}, { status: 409 }); }));
    renderWithProviders(render());
    const button = screen.getByRole("button", { name });
    await waitFor(() => expect(button).toBeDisabled());
    expect(screen.getByTestId("capture-window-notice")).toHaveTextContent("16:25 まで受け付けません");
    expect(screen.queryByTestId("ops-paused-notice")).toBeNull();
    await userEvent.click(button);
    expect(posted).toBe(0);
  });

  it.each([
    ["予測する", () => <PredictButton raceId={RID} pollMs={10} />],
    ["買い目生成", () => <RecommendButton raceId={RID} pollMs={10} />],
  ])("keeps %s enabled", async (name, render) => {
    capturing();
    renderWithProviders(render());
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByRole("button", { name })).toBeEnabled();
    expect(screen.queryByTestId("capture-window-notice")).toBeNull();
  });

  it("shows no capture notice when the window is listed but not active", async () => {
    server.use(http.get(`${BASE}/status`, () => HttpResponse.json({
      ...OPS_STATUS_CAPTURING,
      capture_window: { ...OPS_STATUS_CAPTURING.capture_window, active: false },
    })));
    renderWithProviders(<RefreshButton raceId={RID} pollMs={10} />);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByRole("button", { name: "データ更新" })).toBeEnabled();
    expect(screen.queryByTestId("capture-window-notice")).toBeNull();
  });
});
