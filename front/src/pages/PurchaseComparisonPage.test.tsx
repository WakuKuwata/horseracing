import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { PurchaseComparisonResponse } from "../api/types";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { PurchaseComparisonPage } from "./PurchaseComparisonPage";

const comparisonResponse: PurchaseComparisonResponse = {
  as_of: "2026-09-02T03:04:05Z",
  scope: "all",
  include_post_hoc: true,
  series: {
    actual: [
      {
        race_id: "race-b",
        race_date: "2026-08-02",
        net_yen: 2500,
        cumulative_net_yen: 1300,
      },
      {
        race_id: "race-a",
        race_date: "2026-08-01",
        net_yen: -1200,
        cumulative_net_yen: -1200,
      },
    ],
    policy: [
      {
        race_id: "race-b",
        race_date: "2026-08-02",
        net_yen: 600,
        cumulative_net_yen: 600,
      },
      {
        race_id: "race-a",
        race_date: "2026-08-01",
        net_yen: null,
        cumulative_net_yen: null,
      },
    ],
    no_bet: 0,
  },
  cumulative: {
    actual: 1300,
    policy: 600,
    no_bet: 0,
    diff_actual_vs_policy: null,
    diff_actual_vs_no_bet: 1300,
  },
  pending: { n_races: 2, n_bets: 5, amount_yen: 3400 },
  coverage_rate: {
    overall: 0.625,
    pre_ingestion: 0.5,
    post_ingestion: 0.125,
    n_all_races: 8,
    n_recorded_races: 5,
  },
  n_races: 4,
  n_estimated_settlements: 2,
  estimated_amount_yen: 1750,
  estimator_provenance: "estimate_market_odds test fixture",
  n_post_hoc: 3,
  n_corrections: 2,
  n_presentation_unavailable: 1,
  notes: [
    "counterfactual_snapshot",
    "pre_tax",
    "asymmetric_scope",
    "coverage_denominator_all_races",
  ],
};

function installComparisonHandler(
  capture?: (url: URL) => void,
  response: PurchaseComparisonResponse = comparisonResponse,
) {
  server.use(
    http.get("*/api/v1/purchase-comparison", ({ request }) => {
      capture?.(new URL(request.url));
      return HttpResponse.json(response);
    }),
  );
}

describe("PurchaseComparisonPage", () => {
  it("renders the exact comparison, pending, coverage, and disclosure values", async () => {
    installComparisonHandler();
    renderWithProviders(<PurchaseComparisonPage />);

    const table = await screen.findByRole("table", { name: "購入結果の三者比較" });
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("2026-08-01");
    expect(rows[2]).toHaveTextContent("2026-08-02");

    const firstRaceCells = within(screen.getByTestId("comparison-row-race-a")).getAllByRole("cell");
    expect(firstRaceCells[0]).toHaveTextContent("-1,200");
    expect(firstRaceCells[1]).toHaveTextContent("算出不能");
    expect(firstRaceCells[1]).toHaveTextContent("提示スナップショット無し");
    expect(firstRaceCells[2]).toHaveTextContent("0");

    const secondRaceCells = within(screen.getByTestId("comparison-row-race-b")).getAllByRole("cell");
    expect(secondRaceCells[0]).toHaveTextContent("2,500");
    expect(secondRaceCells[1]).toHaveTextContent("600");
    expect(secondRaceCells[2]).toHaveTextContent("0");

    const totalCells = within(screen.getByTestId("comparison-totals")).getAllByRole("cell");
    expect(totalCells[0]).toHaveTextContent("1,300");
    expect(totalCells[1]).toHaveTextContent("600");
    expect(totalCells[2]).toHaveTextContent("0");

    expect(screen.getByTestId("cumulative-differences")).toHaveTextContent(
      "政策線が算出できないため比較不能",
    );
    expect(screen.getByTestId("cumulative-differences")).toHaveTextContent(
      "実購入−賭けない+1,300円",
    );
    expect(screen.getByTestId("pending-summary")).toHaveTextContent(
      "未確定 2レース・5点・¥3,400(集計に含まれていません)",
    );

    const coverage = screen.getByTestId("coverage-facts");
    expect(coverage).toHaveTextContent("全体62.5%");
    expect(coverage).toHaveTextContent("結果取込前に記録50.0%");
    expect(coverage).toHaveTextContent("結果取込後に記録12.5%");
    expect(coverage).toHaveTextContent("記録レース / 全開催レース5 / 8");
    expect(coverage).toHaveTextContent("訂正2 件");
    expect(coverage).toHaveTextContent("提示不明1 件");

    const estimated = screen.getByTestId("estimated-settlements");
    expect(estimated).toHaveTextContent("うち推定精算 2 件(¥1,750)");
    const pseudoValue = estimated.querySelector('[data-pseudo="true"]');
    expect(pseudoValue).toHaveAttribute("data-pseudo-kind", "double_pseudo");

    expect(screen.getByTestId("permanent-disclosures")).toHaveTextContent(
      "政策線は記録時に凍結した提示スナップショットによる反実仮想の算出値であり、実現値ではありません",
    );
    expect(screen.getByText(/算出時点:/)).toHaveTextContent("2026-09-02 03:04:05 UTC");
    expect(document.body).not.toHaveTextContent(/利益|儲|勝てる|おすすめ/);
  });

  it("refetches with scope=win_only when the symmetric view is selected", async () => {
    const requestedScopes: Array<string | null> = [];
    installComparisonHandler((url) => requestedScopes.push(url.searchParams.get("scope")));
    const user = userEvent.setup();
    renderWithProviders(<PurchaseComparisonPage />);

    await screen.findByRole("table", { name: "購入結果の三者比較" });
    await user.click(screen.getByRole("radio", { name: "単勝のみ=対称ビュー" }));

    await waitFor(() => expect(requestedScopes).toContain("win_only"));
    expect(screen.getByRole("radio", { name: "単勝のみ=対称ビュー" })).toBeChecked();
  });

  it("renders separate loading and empty states", async () => {
    server.use(
      http.get("*/api/v1/purchase-comparison", async () => {
        await delay(30);
        return HttpResponse.json({ ...comparisonResponse, n_races: 0 });
      }),
    );
    renderWithProviders(<PurchaseComparisonPage />);

    expect(screen.getByRole("status")).toHaveTextContent("購入比較を読み込み中…");
    expect(await screen.findByText("この期間の記録はありません")).toHaveAttribute(
      "data-state",
      "empty",
    );
  });

  it("renders a typed error state", async () => {
    server.use(
      http.get("*/api/v1/purchase-comparison", () =>
        HttpResponse.json(
          { status: 422, code: "invalid_date_range", detail: "from must not exceed to" },
          { status: 422 },
        ),
      ),
    );
    renderWithProviders(<PurchaseComparisonPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveAttribute("data-code", "invalid_date_range");
    expect(alert).toHaveTextContent("エラー 422");
    expect(alert).toHaveTextContent("from must not exceed to");
  });
});
