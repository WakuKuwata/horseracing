import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ROI_BASIS_LABELS } from "../lib/attention";
import { EXPECTED_RETURN_SCOPE, UNMEASURED_CLAIMS } from "../lib/forbiddenPhrases";
import { marketEvProspectiveFixture, prospectiveColumnFixture, http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { MarketEvProspectivePanel } from "./MarketEvProspectivePanel";

const BASE = "*/api/v1";

describe("MarketEvProspectivePanel (B8)", () => {
  it("shows both columns' counts, the official-payout recovery with its interval and 判断保留", async () => {
    server.use(
      http.get(`${BASE}/market-ev/prospective`, () => HttpResponse.json(marketEvProspectiveFixture())),
    );
    renderWithProviders(<MarketEvProspectivePanel />);
    const er = await screen.findByTestId("prospective-column-expected_return");
    expect(within(er).getByText("期待回収率(120%超の馬)")).toBeInTheDocument();
    expect(er).toHaveTextContent("8 点・7 レース・的中 1");
    expect(er).toHaveTextContent("800 円 / 1,160 円");
    const roi = within(er).getByTestId("prospective-roi-expected_return");
    expect(roi).toHaveTextContent("145.0%");
    expect(roi).toHaveTextContent("区間 0.0〜232.0%");
    expect(roi).toHaveTextContent(`〔${ROI_BASIS_LABELS.official_win_payout}〕`);
    expect(within(er).getByTestId("prospective-status-expected_return")).toHaveTextContent(
      "判断保留(300 点まであと 292 点)",
    );
    const mix = screen.getByTestId("prospective-column-expected_return_mix");
    expect(mix).toHaveTextContent("4 点・3 レース・的中 1");
    expect(mix).toHaveTextContent("区間なし(開催日が 1 日しかありません)");
    // the funnel sums to the candidates and the eligible row is last
    const funnel = screen.getByTestId("prospective-funnel");
    expect(within(funnel).getByTestId("prospective-funnel-computed_after_post")).toHaveTextContent("79");
    expect(within(funnel).getByTestId("prospective-funnel-eligible")).toHaveTextContent("38");
    const days = screen.getByTestId("prospective-days");
    expect(within(days).getAllByRole("row").map((r) => r.getAttribute("data-testid"))).toEqual([
      null, "prospective-day-2026-10-12", "prospective-day-2026-10-24",
    ]);
  });

  it("every recovery figure carries the official-payout basis and the text keeps the display rules", async () => {
    server.use(
      http.get(`${BASE}/market-ev/prospective`, () => HttpResponse.json(marketEvProspectiveFixture())),
    );
    renderWithProviders(<MarketEvProspectivePanel />);
    const root = await screen.findByTestId("market-ev-prospective");
    await within(root).findByTestId("prospective-days");
    const rois = root.querySelectorAll('[data-kind="roi"]');
    expect(rois.length).toBe(2);
    for (const node of rois) {
      expect(node.textContent).toContain(ROI_BASIS_LABELS.official_win_payout);
    }
    expect(root.textContent).not.toMatch(EXPECTED_RETURN_SCOPE);
    expect(root.textContent).not.toMatch(UNMEASURED_CLAIMS);
    expect(root.textContent).not.toMatch(/買うべき|推奨|おすすめ|通過|合格|採用/);
    expect(root.textContent).toContain("採否の規則は登録していない");
    expect(root.querySelector(".profit, .loss, [data-profit]")).toBeNull();
  });

  it("an empty record reads as zero points and 判断保留, never as a result", async () => {
    const zero = { n_races: 0, n_races_with_bets: 0, n_bets: 0, n_hits: 0, stake_yen: 0,
      return_yen: 0, recovery_rate: null, ci: null, p_one_sided: null, n_days: 0,
      remaining_to_min: 300 } as const;
    server.use(
      http.get(`${BASE}/market-ev/prospective`, () =>
        HttpResponse.json(marketEvProspectiveFixture({
          funnel: { since: "2026-10-05", n_candidate_races: 0, jump_race: 0, not_quote: 0,
            result_known_at_compute: 0, not_single_snapshot: 0, post_time_unknown: 0,
            observed_after_compute: 0, computed_after_post: 0, field_mismatch: 0,
            invalid_values: 0, pending_result: 0, payout_race_missing: 0,
            payout_inconsistent: 0, eligible: 0 },
          columns: [
            prospectiveColumnFixture(zero),
            prospectiveColumnFixture({ ...zero, column: "expected_return_mix", threshold: 1.0,
              start_date: "2026-10-19" }),
          ],
          by_day: [],
          last_eligible_race_date: null,
        })),
      ),
    );
    renderWithProviders(<MarketEvProspectivePanel />);
    const er = await screen.findByTestId("prospective-column-expected_return");
    expect(er).toHaveTextContent("0 点・0 レース・的中 0");
    expect(within(er).getByTestId("prospective-roi-expected_return")).toHaveTextContent("—");
    expect(within(er).getByTestId("prospective-status-expected_return")).toHaveTextContent(
      "判断保留(300 点まであと 300 点)",
    );
    expect(screen.getByTestId("prospective-days-empty")).toHaveTextContent("まだ記録がありません");
  });

  it("at the minimum it still decides nothing", async () => {
    server.use(
      http.get(`${BASE}/market-ev/prospective`, () =>
        HttpResponse.json(marketEvProspectiveFixture({
          columns: [
            prospectiveColumnFixture({ n_bets: 300, stake_yen: 30000, remaining_to_min: 0 }),
            prospectiveColumnFixture({ column: "expected_return_mix", threshold: 1.0,
              start_date: "2026-10-19", n_bets: 300, stake_yen: 30000, remaining_to_min: 0 }),
          ],
        })),
      ),
    );
    renderWithProviders(<MarketEvProspectivePanel />);
    const status = await screen.findByTestId("prospective-status-expected_return");
    expect(status).toHaveTextContent("300 点に到達");
    expect(status).toHaveTextContent("ここでは判定しません");
  });

  it("a failed read is a typed error state", async () => {
    server.use(
      http.get(`${BASE}/market-ev/prospective`, () =>
        HttpResponse.json({ status: 500, code: "internal", detail: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(<MarketEvProspectivePanel />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });
});
