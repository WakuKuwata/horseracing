import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import type { RecommendationResponse } from "../api/types";
import { resetBudgetMemoryForTests } from "../lib/budget";
import { server } from "../tests/server";
import { happyHandlers, http, HttpResponse, recommendationResponse } from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { renderWithProviders } from "../tests/utils";
import { RecommendationPanel } from "./RecommendationPanel";

const BASE = "*/api/v1";

/**
 * Slip-view fixture: every row settled=false (T012E requires the slip view, and the default
 * fixture's settled win rows would open the results view instead).
 * Rows: an estimated-odds quinella (double_pseudo), a real-odds win with a Kelly fraction,
 * and a real-odds win with stake_fraction=null (pre-Kelly legacy row → 参考表示).
 */
const unsettledResponse: RecommendationResponse = {
  ...recommendationResponse,
  items: [
    { ...recommendationResponse.items[0] }, // rec-1 quinella double_pseudo f=0.0123
    {
      ...recommendationResponse.items[1],
      recommendation_id: "rec-w1",
      settled: false,
      hit: undefined,
      counterfactual_snapshot_gross_return: undefined,
      counterfactual_snapshot_net_return: undefined,
    }, // win f=0.02 real odds
    {
      ...recommendationResponse.items[2],
      recommendation_id: "rec-w2",
      settled: false,
      hit: undefined,
      counterfactual_snapshot_gross_return: undefined,
      counterfactual_snapshot_net_return: undefined,
    }, // win f=null legacy
  ],
};

/** All pseudo values present in unsettledResponse (exhaustive enumeration — codex H1). */
const ALL_PSEUDO_TEXTS = [
  "×12.3", // estimated used odds (rec-1)
  "×4.5", // pseudo_odds (rec-1)
  "18.0%", // pseudo_roi (rec-1)
  "1.23%", // Kelly (rec-1, double_pseudo)
  "×3.1", // pseudo_odds (rec-w1)
  "35.0%", // pseudo_roi (rec-w1)
  "2.00%", // Kelly (rec-w1)
  "×6.0", // pseudo_odds (rec-w2)
  "10.0%", // pseudo_roi (rec-w2)
];

// NOTE: MSW resolves handlers first-match-wins, so overrides must NOT be listed after
// happyHandlers. The panel only calls the recommendations endpoint — register it alone.
function useSlipHandlers(response: RecommendationResponse = unsettledResponse) {
  server.use(
    http.get(`${BASE}/races/:id/recommendations`, () => HttpResponse.json(response)),
  );
}

async function setBudget(yen: string) {
  const user = userEvent.setup();
  const input = await screen.findByLabelText("このレースの予算");
  await user.clear(input);
  await user.type(input, yen);
  await user.click(screen.getByRole("button", { name: "設定" }));
  return user;
}

beforeEach(() => {
  resetBudgetMemoryForTests();
});

describe("RecommendationPanel — slip view (US1)", () => {
  it("converts stake fractions to ¥100-floored amounts with a consistent summary", async () => {
    useSlipHandlers();
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await setBudget("10000");

    // rec-1: 0.0123×10000=123 → ¥100 / rec-w1: 0.02×10000 → ¥200 / rec-w2: null → 参考表示
    expect(await screen.findByTestId("slip-summary")).toHaveTextContent("¥300");
    expect(screen.getByTestId("slip-summary")).toHaveTextContent("2点・予算の3%");
    expect(screen.getByTestId("bet-slip-card-rec-1")).toHaveTextContent("¥100");
    expect(screen.getByTestId("bet-slip-card-rec-w1")).toHaveTextContent("¥200");
    expect(screen.getByTestId("bet-slip-card-rec-w2")).toHaveTextContent("参考表示");
  });

  it("recomputes every amount immediately when the budget changes (FR-007)", async () => {
    useSlipHandlers();
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await setBudget("10000");
    expect(await screen.findByTestId("slip-summary")).toHaveTextContent("¥300");

    await setBudget("100000");
    // rec-1: 0.0123×100000 → ¥1,200 / rec-w1: ¥2,000
    expect(screen.getByTestId("slip-summary")).toHaveTextContent("¥3,200");
    expect(screen.getByTestId("bet-slip-card-rec-1")).toHaveTextContent("¥1,200");
  });

  it("falls back to badged ratios (with strength, without any yen) when the budget is unset", async () => {
    useSlipHandlers();
    const { container } = renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await screen.findByTestId("budget-unset-hint");
    expect(screen.queryByTestId("slip-summary")).toBeNull();
    expect(container.textContent).not.toMatch(/¥/);
    const card = screen.getByTestId("bet-slip-card-rec-w1");
    expect(card).toHaveTextContent("2.00%");
    expect(within(card).getByText("厚め")).toBeInTheDocument();
    // (b') the pseudo coverage invariant also holds in the fallback rendering
    assertPseudoLabelCoverage(container, ["1.23%", "2.00%"]);
  });

  it("badges the PRIMARY amount of double-pseudo rows, visible before any expansion (codex D3)", async () => {
    useSlipHandlers();
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await setBudget("10000");

    const dp = await screen.findByTestId("bet-slip-card-rec-1");
    const dpAmount = within(dp).getByText("¥100").closest('[data-pseudo="true"]');
    expect(dpAmount).not.toBeNull();
    expect(dpAmount).toHaveAttribute("data-pseudo-kind", "double_pseudo");
    expect(dpAmount).toBeVisible(); // NOT hidden inside the collapsed evidence

    // real-odds row: the amount is plain arithmetic — no pseudo node
    const real = screen.getByTestId("bet-slip-card-rec-w1");
    expect(within(real).getByText("¥200").closest('[data-pseudo="true"]')).toBeNull();
  });

  it("badges a double-pseudo 少額見送り the same way", async () => {
    useSlipHandlers({
      ...unsettledResponse,
      items: [{ ...unsettledResponse.items[0], stake_fraction: 0.007 }], // 70円 → too_small
    });
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await setBudget("10000");
    const card = await screen.findByTestId("bet-slip-card-rec-1");
    const skip = within(card).getByText("少額のため見送り").closest('[data-pseudo="true"]');
    expect(skip).not.toBeNull();
    expect(skip).toHaveAttribute("data-pseudo-kind", "double_pseudo");
  });
});

describe("RecommendationPanel — pseudo invariant (T012E, codex H1)", () => {
  it("expands every evidence disclosure, then asserts exhaustive badge coverage + kinds", async () => {
    useSlipHandlers();
    const { container } = renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await setBudget("10000");
    const user = userEvent.setup();

    const summaries = await screen.findAllByText("根拠を見る");
    expect(summaries.length).toBe(3);
    for (const summary of summaries) {
      const details = summary.closest("details")!;
      const pseudoNode = details.querySelector<HTMLElement>('[data-pseudo="true"]')!;
      expect(pseudoNode).not.toBeNull();
      expect(details).not.toHaveAttribute("open");
      expect(pseudoNode).not.toBeVisible(); // closed → hidden (still in the DOM)
      await user.click(summary);
      expect(details).toHaveAttribute("open");
      expect(pseudoNode).toBeVisible();
    }

    // exhaustive: EVERY pseudo value in the fixture, not a spot-check subset
    assertPseudoLabelCoverage(container, ALL_PSEUDO_TEXTS);

    // per-row kind checks: estimated-odds row is double_pseudo, real-odds row is pseudo
    const dp = screen.getByTestId("bet-slip-card-rec-1");
    expect(within(dp).getByText("18.0%").closest('[data-pseudo="true"]'))
      .toHaveAttribute("data-pseudo-kind", "double_pseudo");
    expect(within(dp).getByText("×12.3").closest('[data-pseudo="true"]'))
      .toHaveAttribute("data-pseudo-kind", "estimated");
    const real = screen.getByTestId("bet-slip-card-rec-w1");
    expect(within(real).getByText("35.0%").closest('[data-pseudo="true"]'))
      .toHaveAttribute("data-pseudo-kind", "pseudo");
    // the real used odds ×3.2 must NOT be inside a pseudo node
    expect(within(real).getByText(/×3\.2/).closest('[data-pseudo="true"]')).toBeNull();
  });
});

describe("RecommendationPanel — results view (US3, FR-020/021/022)", () => {
  it("opens settled races in the results view even when the response is delayed (codex H8)", async () => {
    server.use(
      http.get(`${BASE}/races/:id/recommendations`, async () => {
        await new Promise((r) => setTimeout(r, 30));
        return HttpResponse.json(recommendationResponse);
      }),
    );
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    // after the async response lands, the derived default view must be results
    expect(await screen.findByTestId("results-view")).toBeInTheDocument();
    expect(screen.getByTestId("win-backtest-summary")).toBeInTheDocument();
  });

  it("keeps the pre-087 retrospective semantics (values, labels, hit/miss, no pseudo taint)", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    const summary = await screen.findByTestId("win-backtest-summary");
    // 1 hit of 2 settled → 的中率 50.0%, recovery ×1.60 (=(3.2+0)/2) — unchanged values
    expect(summary).toHaveTextContent("50.0%");
    expect(summary).toHaveTextContent("×1.60");
    expect(summary).toHaveTextContent("反実仮想(判断時オッズ)");
    expect(summary).toHaveTextContent("参考");
    expect(summary).toHaveTextContent("将来の的中・利益を示すものではありません");
    // per-row hit/miss with ResultBadge, never inside a pseudo node
    expect(container.querySelector('[data-result="hit"]')).toHaveTextContent("的中");
    expect(container.querySelector('[data-result="miss"]')).toHaveTextContent("不的中");
    const resultCells = container.querySelectorAll("[data-result]");
    expect(resultCells.length).toBeGreaterThan(0);
    resultCells.forEach((el) => {
      expect(el.closest('[data-pseudo="true"]')).toBeNull();
    });
  });

  it("shows honest baselines and the odds-band breakdown unchanged (064)", async () => {
    server.use(
      http.get(`${BASE}/races/:id/recommendations`, () =>
        HttpResponse.json({
          ...recommendationResponse,
          favorite_baseline: {
            horse_number: 3, odds: 2.0, settled: true, hit: false, dead_heat: false,
            current_odds_gross_return: 0.0, current_odds_net_return: -1.0,
          },
        }),
      ),
    );
    const { container } = renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    const baselines = await screen.findByTestId("win-baselines");
    expect(baselines).toHaveTextContent("賭けない");
    expect(baselines).toHaveTextContent("×1.00");
    expect(baselines).toHaveTextContent("本命ベタ買い");
    expect(baselines).toHaveTextContent("現在オッズ基準");
    expect(baselines.querySelectorAll("[data-result]").length).toBe(0); // neutral facts only
    expect(container.querySelector('[data-testid="win-odds-band"]')).not.toBeNull();
  });

  it("keeps dead-heat and void semantics", async () => {
    server.use(
      http.get(`${BASE}/races/:id/recommendations`, () =>
        HttpResponse.json({
          ...recommendationResponse,
          items: [
            { ...recommendationResponse.items[1], recommendation_id: "dh", dead_heat: true },
            { ...recommendationResponse.items[2], recommendation_id: "vd", hit: null,
              counterfactual_snapshot_gross_return: null, counterfactual_snapshot_net_return: null },
          ],
        }),
      ),
    );
    const { container } = renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await screen.findByTestId("results-view");
    expect(container.querySelector('[data-result="hit"]')).toHaveTextContent("⚑同着");
    expect(container.querySelector('[data-result="void"]')).toHaveTextContent("無効");
  });

  it("toggles results ⇄ slip both ways, with the historical-budget note on a settled slip", async () => {
    server.use(...happyHandlers);
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    const user = userEvent.setup();
    await screen.findByTestId("results-view");

    await user.click(screen.getByRole("button", { name: "買い目を見る" }));
    expect(screen.queryByTestId("results-view")).toBeNull();
    expect(screen.getByTestId("historical-note")).toHaveTextContent("購入履歴ではありません");

    await user.click(screen.getByRole("button", { name: "答え合わせを見る" }));
    expect(await screen.findByTestId("results-view")).toBeInTheDocument();
  });

  it("shows no toggle and opens the slip for races without settled rows", async () => {
    useSlipHandlers();
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await screen.findByTestId("bet-slip-card-rec-1");
    expect(screen.queryByRole("button", { name: "買い目を見る" })).toBeNull();
    expect(screen.queryByRole("button", { name: "答え合わせを見る" })).toBeNull();
    expect(screen.queryByTestId("historical-note")).toBeNull();
  });

  it("resets the manual view override when the race changes (codex H8)", async () => {
    const SETTLED_ID = "200806010111";
    const UNSETTLED_ID = "200806010222";
    server.use(
      http.get(`${BASE}/races/:id/recommendations`, ({ params }) =>
        HttpResponse.json(params.id === SETTLED_ID ? recommendationResponse : unsettledResponse),
      ),
    );
    function Harness() {
      const [rid, setRid] = useState(SETTLED_ID);
      return (
        <>
          <button type="button" onClick={() => setRid(rid === SETTLED_ID ? UNSETTLED_ID : SETTLED_ID)}>
            switch-race
          </button>
          <RecommendationPanel raceId={rid} />
        </>
      );
    }
    renderWithProviders(<Harness />);
    const user = userEvent.setup();

    await screen.findByTestId("results-view"); // settled race → results
    await user.click(screen.getByRole("button", { name: "買い目を見る" })); // manual override
    expect(screen.queryByTestId("results-view")).toBeNull();

    await user.click(screen.getByRole("button", { name: "switch-race" })); // → unsettled race
    await screen.findByTestId("bet-slip-card-rec-1"); // slip default, no toggle
    expect(screen.queryByRole("button", { name: "答え合わせを見る" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "switch-race" })); // back to settled
    // the earlier manual "slip" override must NOT survive the race change
    expect(await screen.findByTestId("results-view")).toBeInTheDocument();
  });
});

describe("RecommendationPanel — honest display invariants (FR-041/042, 064)", () => {
  it("shows the neutral disclosures in the slip view with no profit language", async () => {
    useSlipHandlers();
    const { container } = renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await screen.findByTestId("bet-slip-card-rec-1");
    expect(screen.getByTestId("no-edge-note")).toHaveTextContent("市場に対する再現可能な優位を持ちません");
    expect(screen.getByTestId("model-scope-note")).toHaveTextContent("モデル切替はこの表示に影響しません");
    expect(container.textContent).not.toMatch(/儲か|勝てる|稼げる|妙味/);
  });

  it("shows the same disclosures in the results view with no profit language", async () => {
    server.use(...happyHandlers);
    const { container } = renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    await screen.findByTestId("results-view");
    expect(screen.getByTestId("no-edge-note")).toHaveTextContent("将来の的中・利益を示すものではありません");
    expect(container.textContent).not.toMatch(/儲か|勝てる|稼げる|妙味/);
  });
});

describe("RecommendationPanel — empty / skip states (US4)", () => {
  it("shows the neutral empty message for a generated-but-empty response", async () => {
    server.use(
      http.get(`${BASE}/races/:id/recommendations`, () =>
        HttpResponse.json({ ...recommendationResponse, items: [] }),
      ),
    );
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    expect(await screen.findByText("この条件の推奨はありません")).toBeInTheDocument();
  });

  it("surfaces the honest skip card when the win policy selected nothing (exotic rows remain)", async () => {
    server.use(
      http.get(`${BASE}/races/:id/recommendations`, () =>
        HttpResponse.json({
          ...unsettledResponse,
          win_policy_status: "no_win_selected",
          items: unsettledResponse.items.filter((i) => i.bet_type !== "win"),
        }),
      ),
    );
    renderWithProviders(<RecommendationPanel raceId="200806010111" />);
    const skip = await screen.findByTestId("win-skip-reason");
    expect(skip).toHaveTextContent("単勝は見送り");
    expect(screen.getByTestId("bet-slip-card-rec-1")).toBeInTheDocument(); // exotic card stays
  });
});
