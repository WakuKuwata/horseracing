import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { PurchaseRecordsResponse } from "../api/types";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { PurchaseListPage } from "./PurchaseListPage";

const BASE = "*/api/v1";

const purchaseRecords = {
  records: [
    {
      race_id: "202608310511",
      race_date: "2026-08-31",
      record_id: "record-1",
      kind: "modified",
      result_pending_at_record: true,
      recorded_at: "2026-08-31T05:20:00Z",
      n_corrections: 1,
      was_voided: false,
      bets: [
        {
          bet_type: "win",
          selection: [3],
          amount_yen: 1_000,
          status: "settled_real",
          hit: true,
          payout_yen: 3_200,
          is_estimated: false,
        },
        {
          bet_type: "exacta",
          selection: [3, 7],
          amount_yen: 500,
          status: "settled_estimated",
          hit: true,
          payout_yen: 12_500,
          is_estimated: true,
        },
        {
          bet_type: "wide",
          selection: [2, 8],
          amount_yen: 400,
          status: "pending",
          hit: null,
          payout_yen: null,
          is_estimated: false,
        },
      ],
      anomalies: ["取込時刻を確認してください"],
      note: "購入内容を変更",
    },
  ],
  n_races_recorded: 1,
} satisfies PurchaseRecordsResponse;

function usePurchaseRecordsHandler(response = purchaseRecords) {
  server.use(
    http.get(`${BASE}/purchase-records`, () => HttpResponse.json(response)),
  );
}

describe("PurchaseListPage", () => {
  it("renders the exact payout for a settled real hit", async () => {
    usePurchaseRecordsHandler();
    renderWithProviders(<PurchaseListPage />);

    const payoutCell = await screen.findByRole("cell", {
      name: "的中 払戻 ¥3,200",
    });
    expect(payoutCell).toHaveTextContent(/^的中 払戻 ¥3,200$/);
  });

  it("marks an estimated payout with the double-pseudo PseudoValue attributes", async () => {
    usePurchaseRecordsHandler();
    renderWithProviders(<PurchaseListPage />);

    const payout = (await screen.findByText("¥12,500")).closest('[data-pseudo="true"]');
    expect(payout).not.toBeNull();
    expect(payout).toHaveAttribute("data-pseudo-kind", "double_pseudo");
    expect(payout?.querySelector('[data-pseudo-badge="double_pseudo"]')).not.toBeNull();
  });

  it("renders a pending bet as unsettled", async () => {
    usePurchaseRecordsHandler();
    renderWithProviders(<PurchaseListPage />);

    expect(await screen.findByText("未確定")).toBeInTheDocument();
  });

  it.each([
    ["pending", "results_incomplete", "未精算（結果不足）"],
    ["unsettleable", "win_payout_missing", "未精算（公式払戻未保存）"],
    ["unsettleable", "win_payout_inconsistent", "未精算（公式払戻と結果が不整合）"],
    ["unsettleable", "field_inconsistent", "未精算（出走馬・結果の対応を確認できません）"],
    ["unsettleable", "results_inconsistent", "未精算（出走馬・結果の対応を確認できません）"],
    ["unsettleable", "selection_unknown", "未精算（出走馬・結果の対応を確認できません）"],
  ])("discloses %s reason %s without showing a payout", async (status, reason, text) => {
    const response: PurchaseRecordsResponse = structuredClone(purchaseRecords);
    response.records[0].bets = [{
      bet_type: "win", selection: [5], amount_yen: 100, status,
      settlement_reason: reason, hit: null, payout_yen: null, is_estimated: false,
    }];
    server.use(http.get(`${BASE}/purchase-records`, () => HttpResponse.json(response)));
    renderWithProviders(<PurchaseListPage />);

    expect(await screen.findByRole("cell", { name: text })).toBeInTheDocument();
    expect(screen.queryByText("不的中")).not.toBeInTheDocument();
    expect(screen.queryByText("返還")).not.toBeInTheDocument();
  });

  it("renders the empty state when no races were recorded", async () => {
    usePurchaseRecordsHandler({ records: [], n_races_recorded: 0 });
    renderWithProviders(<PurchaseListPage />);

    expect(await screen.findByText("この期間の記録はありません")).toBeInTheDocument();
  });

  it("renders the typed API error message", async () => {
    server.use(
      http.get(`${BASE}/purchase-records`, () =>
        HttpResponse.json(
          {
            status: 500,
            code: "purchase_records_error",
            detail: "購入記録を取得できませんでした",
          },
          { status: 500 },
        ),
      ),
    );
    renderWithProviders(<PurchaseListPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "購入記録を取得できませんでした",
    );
  });
});
