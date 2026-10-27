import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { OPS_STATUS_PAUSED, server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { RoiPurchaseRecord } from "./RoiPurchaseRecord";

const RID = "202605040907";
const DAY = "2026-10-25";

function noRecords() {
  server.use(http.get("*/api/v1/purchase-records", () => HttpResponse.json({ n_races_recorded: 0, records: [] })));
}

function render() {
  return renderWithProviders(
    <RoiPurchaseRecord raceId={RID} raceDate={DAY} horseNumbers={[1, 2, 3]} hasResults={false} />,
  );
}

/** B6: the ROI view records a skip or a free-form purchase into the same 106 stream. */
describe("RoiPurchaseRecord", () => {
  it("records a skip as presentation_unavailable with no bets and no snapshot", async () => {
    noRecords();
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("*/ops/v1/purchase-records", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ purchase_record_id: "rec-1", recorded_at: "2026-10-25T03:00:00Z" }, { status: 201 });
      }),
    );
    render();
    await userEvent.click(await screen.findByRole("button", { name: "見送りを記録" }));
    expect(await screen.findByTestId("roi-purchase-skip-result")).toHaveTextContent("見送りを記録しました");
    expect(body).toMatchObject({
      race_id: RID, kind: "presentation_unavailable", bets: [], presented_snapshot: null,
      prediction_run_id: null, corrects_record_id: null,
    });
    expect((body as unknown as { client_request_id: string }).client_request_id).toEqual(expect.any(String));
    // one effective record per race: the controls lock after a success
    expect(screen.getByRole("button", { name: "見送りを記録" })).toBeDisabled();
  });

  it("shows the existing record and locks every control (corrections live in the comparison tab)", async () => {
    server.use(http.get("*/api/v1/purchase-records", () => HttpResponse.json({
      n_races_recorded: 1,
      records: [{
        record_id: "rec-9", race_id: RID, race_date: DAY, kind: "freeform", n_corrections: 0,
        recorded_at: "2026-10-25T03:00:00Z", result_pending_at_record: true, was_voided: false,
        anomalies: [], note: null,
        bets: [{ bet_type: "win", selection: [3], amount_yen: 500, odds_used: null, status: "pending",
          hit: null, payout_yen: null, is_estimated: false, settlement_reason: null }],
      }],
    })));
    render();
    expect(await screen.findByTestId("roi-purchase-existing")).toHaveTextContent("記録済み: 自由入力・1点・合計 ¥500");
    expect(screen.getByRole("button", { name: "見送りを記録" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "買い目を追加" })).toBeDisabled();
  });

  it("surfaces a rejected skip with a retry and no silent success", async () => {
    noRecords();
    server.use(http.post("*/ops/v1/purchase-records", () =>
      HttpResponse.json({ status: 422, code: "already_recorded", detail: "dup" }, { status: 422 })));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "見送りを記録" }));
    expect(await screen.findByTestId("roi-purchase-skip-result")).toHaveTextContent("記録できませんでした");
    expect(screen.getByRole("button", { name: "再試行" })).toBeEnabled();
  });

  it("is disabled with the reason while ops is paused (B5)", async () => {
    noRecords();
    server.use(http.get("*/ops/v1/status", () => HttpResponse.json(OPS_STATUS_PAUSED)));
    render();
    await waitFor(() => expect(screen.getByRole("button", { name: "見送りを記録" })).toBeDisabled());
    expect(screen.getByTestId("ops-paused-notice")).toHaveTextContent("購入記録の保存も止まっています");
  });

  it("says nothing that recommends a bet and states the post-result case", async () => {
    noRecords();
    renderWithProviders(
      <RoiPurchaseRecord raceId={RID} raceDate={DAY} horseNumbers={[1, 2]} hasResults />,
    );
    expect(await screen.findByTestId("roi-purchase-post-result-note")).toBeInTheDocument();
    const text = screen.getByTestId("roi-purchase-record").textContent ?? "";
    for (const banned of ["おすすめ", "推奨します", "買うべき", "妙味", "利益"]) {
      expect(text).not.toContain(banned);
    }
  });
});
