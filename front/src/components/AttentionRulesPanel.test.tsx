import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { AttentionRulesResponse, RuleId, RuleSummary } from "../api/types";
import { DECISION_LABELS, EXCLUSION_LABELS, ROI_BASIS_LABELS } from "../lib/attention";
import { formatPct } from "../lib/format";
import {
  assertListDiscipline,
  assertRoiBasisLabels,
  hasRoiWithLabel,
} from "../tests/attentionScope";
import {
  ATTENTION_RULE_IDS,
  attentionRulesFixture,
  checkpointDecisionFixture,
  happyHandlers,
  http,
  HttpResponse,
} from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { AttentionRulesPanel } from "./AttentionRulesPanel";

const BASE = "*/api/v1";

function serveRules(body: AttentionRulesResponse) {
  // server.use prepends: install the defaults first, then the override (which then wins).
  server.use(...happyHandlers);
  server.use(http.get(`${BASE}/attention-rules`, () => HttpResponse.json(body)));
}

async function renderOpen(body: AttentionRulesResponse = attentionRulesFixture()) {
  serveRules(body);
  const view = renderWithProviders(<AttentionRulesPanel />);
  const panel = screen.getByTestId("attention-rules-panel") as HTMLDetailsElement;
  // 087 lesson: open the fold before asserting anything about its contents.
  await userEvent.click(within(panel).getByText(/注目条件の一覧/, { selector: "summary" }));
  expect(panel.open).toBe(true);
  await screen.findByTestId("attention-rule-S1");
  return { ...view, panel };
}

const counts = {
  voided_scratched: 1,
  before_start: 2,
  post_time_unknown: 3,
  computed_after_post: 4,
  result_known_at_compute: 5,
  observed_after_post: 6,
  pending_result: 7,
  unsettled_horse: 8,
  dead_heat: 9,
};

/** Every stage and record kind at once: S1 passed, S2 undecided, S3 judgment pending,
 *  S4 failed (the 「300 点不通過」 case of US2-3), S5 (control) still 研究中. */
function richRules(): AttentionRulesResponse {
  return attentionRulesFixture({
    S1: {
      prospective: {
        start_date: "2026-10-03",
        stage: "passed",
        checkpoint: 300,
        next_checkpoint: null,
        remaining_to_next: null,
        n_counted: 320,
        n_hits: 15,
        n_picks_total: 365,
        frozen: { valuation_basis: "frozen_pick_odds", roi: 1.231, ci: [1.022, 1.452], p_one_sided: 0.012 },
        stored: {
          valuation_basis: "stored_odds_mutable", roi: 1.187, ci: [0.991, 1.404], n: 318,
          n_missing_stored_odds: 2,
        },
        decisions: [
          checkpointDecisionFixture({ decision: "passed", roi_frozen: 1.255, ci: [1.031, 1.477] }),
        ],
        counts,
        flags: { field_changed_after_pick: 4 },
        by_judged_freshness: {
          "<=10m": { n: 40, hits: 2, roi_frozen: 1.111 },
          "<=60m": { n: 80, hits: 4, roi_frozen: 1.222 },
          ">60m": { n: 200, hits: 9, roi_frozen: 1.333 },
        },
        odds_drift: { n: 318, median_log_ratio: -0.041, p10: -0.212, p90: 0.153 },
      },
    },
    S2: {
      prospective: {
        start_date: "2026-10-03",
        stage: "undecided",
        checkpoint: 600,
        next_checkpoint: null,
        remaining_to_next: null,
        n_counted: 610,
        n_hits: 25,
        frozen: { valuation_basis: "frozen_pick_odds", roi: 1.044, ci: [0.881, 1.219], p_one_sided: 0.31 },
        decisions: [
          checkpointDecisionFixture({ checkpoint: 300, decision: "continue", roi_frozen: 1.066 }),
          checkpointDecisionFixture({
            checkpoint: 600, decision: "undecided", n_counted: 600, roi_frozen: 1.077,
            ci: [0.902, 1.255],
          }),
        ],
      },
    },
    S3: {
      prospective: {
        start_date: "2026-10-03",
        stage: "observing",
        checkpoint: null,
        checkpoint_pending: true,
        next_checkpoint: 300,
        remaining_to_next: 0,
        n_counted: 305,
        n_hits: 18,
        frozen: { valuation_basis: "frozen_pick_odds", roi: 1.088, ci: [0.912, 1.288], p_one_sided: 0.2 },
      },
    },
    S4: {
      prospective: {
        start_date: "2026-10-03",
        stage: "failed",
        checkpoint: 300,
        next_checkpoint: null,
        remaining_to_next: null,
        n_counted: 320,
        n_hits: 9,
        frozen: { valuation_basis: "frozen_pick_odds", roi: 0.902, ci: [0.861, 0.982], p_one_sided: 0.9 },
        decisions: [
          checkpointDecisionFixture({ decision: "failed", roi_frozen: 0.911, ci: [0.86, 0.98] }),
        ],
      },
    },
  });
}

const pct = (ratio: number) => formatPct(ratio, 1);

describe("AttentionRulesPanel (138 T036)", () => {
  it("is a closed <details> by default and opens to the 5 rules in fixed rank order", async () => {
    serveRules(attentionRulesFixture());
    renderWithProviders(<AttentionRulesPanel />);
    const panel = screen.getByTestId("attention-rules-panel") as HTMLDetailsElement;
    expect(panel.tagName).toBe("DETAILS");
    expect(panel.open).toBe(false);
    await userEvent.click(within(panel).getByText(/注目条件の一覧/, { selector: "summary" }));
    expect(panel.open).toBe(true);
    await screen.findByTestId("attention-rule-S1");
    const order = Array.from(panel.querySelectorAll("[data-rule-id]")).map((e) =>
      e.getAttribute("data-rule-id"),
    );
    expect(order).toEqual(ATTENTION_RULE_IDS);
  });

  it("keeps rank order even when the response is shuffled (never re-sorted by results)", async () => {
    const body = attentionRulesFixture({
      // S5 has the best prospective number: still listed last (rank order only)
      S5: { prospective: { frozen: { valuation_basis: "frozen_pick_odds", roi: 1.9, ci: null, p_one_sided: null } } },
    });
    const shuffled = { ...body, items: [body.items[4], body.items[2], body.items[0], body.items[3], body.items[1]] };
    const { panel } = await renderOpen(shuffled);
    const order = Array.from(panel.querySelectorAll("[data-rule-id]")).map((e) =>
      e.getAttribute("data-rule-id"),
    );
    expect(order).toEqual(["S1", "S2", "S3", "S4", "S5"]);
  });

  it("defaultOpen renders it open (the /attention page)", async () => {
    serveRules(attentionRulesFixture());
    renderWithProviders(<AttentionRulesPanel defaultOpen />);
    const panel = screen.getByTestId("attention-rules-panel") as HTMLDetailsElement;
    expect(panel.open).toBe(true);
    expect(await screen.findByTestId("attention-rule-S5")).toBeInTheDocument();
  });

  it("shows the definition, frozen backtest (all/c), bets per year and selected-horse line", async () => {
    await renderOpen();
    const s1 = screen.getByTestId("attention-rule-S1");
    expect(s1).toHaveTextContent(
      "定義: 15 seed 平均の期待回収率が 120% 超、単勝 20 倍以上 40 倍未満、前走から 14〜112 日",
    );
    expect(screen.getByTestId("attention-rule-backtest-all-S1").textContent).toBe(
      "通算 2010〜26 121.1%(区間 105.3〜137.1%)〔確定オッズ近似〕・5,235 点・225 的中・p=0.0006",
    );
    expect(screen.getByTestId("attention-rule-backtest-c-S1").textContent).toBe(
      "確認窓 2019〜26 117.7%(区間 85.5〜151.7%)〔確定オッズ近似〕・1,183 点・47 的中・p=0.11",
    );
    expect(s1).toHaveTextContent("年間点数 2024 年 97・2025 年 105・2026 年(途中) 102");
    expect(s1).toHaveTextContent("多重探索の補正なし");
    expect(s1).toHaveTextContent("B=20,000・seed 20260905");
    // selected horses: mean expected return (an estimate, 推定 badge) vs the realized return
    const selAll = screen.getByTestId("attention-rule-selected-all-S1");
    expect(selAll).toHaveTextContent("選ばれた馬の期待回収率の平均 140.6% 推定");
    expect(selAll).toHaveTextContent("実際の回収率 121.1%〔確定オッズ近似〕・5,235 点");
    expect(selAll.querySelector('[data-pseudo="true"] [data-pseudo-badge]')).not.toBeNull();
    expect(screen.getByTestId("attention-rule-selected-c-S1")).toHaveTextContent(
      "期待回収率の平均 132.4%",
    );
  });

  it("labels the backtest axis with the frozen criterion words", async () => {
    await renderOpen();
    expect(screen.getByTestId("attention-rule-backtest-level-S1").textContent).toBe(
      "過去検証の軸: 通算の区間下限 100% 超・確認窓 110% 以上",
    );
    expect(screen.getByTestId("attention-rule-backtest-level-S3").textContent).toBe(
      "過去検証の軸: 通算・確認窓とも 100% 超",
    );
    expect(screen.getByTestId("attention-rule-backtest-level-S5").textContent).toBe(
      "過去検証の軸: 通算か確認窓が 100% 以下",
    );
  });

  it("shows the price-noise test as numbers per σ (ROI, n, overlap) with no strength words", async () => {
    await renderOpen();
    const row = screen.getByTestId("attention-price-noise-S1-0.1");
    expect(row).toHaveTextContent("σ=0.1(ずれ 10%)");
    expect(row).toHaveTextContent("116.6%〔確定オッズ近似〕");
    expect(row).toHaveTextContent("6,432 点(ずれ無しの 1.2 倍)");
    expect(row).toHaveTextContent("59%");
    expect(screen.getByTestId("attention-price-noise-S1-0.3")).toHaveTextContent(
      "96.7%〔確定オッズ近似〕",
    );
    const block = screen.getByTestId("attention-rule-price-noise-S1");
    expect(block.textContent).not.toMatch(/強い|弱い|頑健|脆/);
    // the settlement basis is the same closing approximation as the ROI labels — never the word
    // 保存オッズ, which belongs to the mutable prospective reference basis 〔保存オッズ(参考)・近似〕
    expect(block).toHaveTextContent("精算はずらす前の確定オッズ近似 × 100 円・10 反復の平均");
    expect(block.textContent).not.toMatch(/保存オッズ/);
  });

  it("badges: S1/S2 探索後固定 + 前向き未確認, S3/S4 none, S5 対照", async () => {
    await renderOpen();
    for (const id of ["S1", "S2"] as RuleId[]) {
      expect(screen.getByTestId(`attention-badge-posthoc-${id}`)).toHaveTextContent("探索後固定");
      expect(screen.getByTestId(`attention-badge-unconfirmed-${id}`)).toHaveTextContent("前向き未確認");
    }
    for (const id of ["S3", "S4", "S5"] as RuleId[]) {
      expect(screen.queryByTestId(`attention-badge-posthoc-${id}`)).toBeNull();
      expect(screen.queryByTestId(`attention-badge-unconfirmed-${id}`)).toBeNull();
    }
    expect(screen.getByTestId("attention-badge-control-S5")).toHaveTextContent("対照");
    expect(screen.queryByTestId("attention-badge-control-S1")).toBeNull();
    expect(screen.getByTestId("attention-rule-S5")).toHaveTextContent("対照 S5");
  });

  it("pre-launch: every rule 研究中, 集計開始前, next checkpoint 300, no decisions, zero counts", async () => {
    await renderOpen();
    for (const id of ATTENTION_RULE_IDS) {
      expect(screen.getByTestId(`attention-rule-stage-${id}`).textContent).toBe("研究中");
      expect(screen.getByTestId(`attention-rule-start-${id}`)).toHaveTextContent(
        "集計開始 集計開始前(集計開始日は未設定)・集計方針 v1",
      );
      expect(screen.getByTestId(`attention-rule-tally-${id}`)).toHaveTextContent(
        "集計 0 点・0 的中・次のチェックポイント 300 点(あと 300 点)",
      );
      expect(screen.getByTestId(`attention-rule-decisions-${id}`)).toHaveTextContent(
        "判定記録: まだありません",
      );
      expect(screen.getByTestId(`attention-count-${id}-total`)).toHaveTextContent("0");
      // no value yet: placeholder, never 0%
      expect(screen.getByTestId(`attention-rule-frozen-${id}`)).toHaveTextContent(
        "回収率(段階判定の基準) —〔判断時オッズ・近似〕",
      );
      expect(screen.getByTestId(`attention-odds-drift-${id}`)).toHaveTextContent("対象なし");
    }
  });

  it("S4 failed at 300: kept on screen with the stage name, its reason and the decision record", async () => {
    await renderOpen(richRules());
    expect(screen.getByTestId("attention-rule-S4")).toBeInTheDocument();
    expect(screen.getByTestId("attention-rule-stage-S4").textContent).toBe("300 点不通過");
    expect(screen.getByTestId("attention-rule-stage-reason-S4")).toHaveTextContent(
      "300 点時点で区間の上限が 100% 未満でした",
    );
    expect(screen.getByTestId("attention-rule-stage-reason-S4")).toHaveTextContent(
      "チップは外さず",
    );
    const decisions = screen.getByTestId("attention-rule-decisions-S4");
    expect(within(decisions).getByTestId("attention-decision-300")).toHaveTextContent(
      "300 点: 不通過(判定 2027/09/20 12:00)・300 点・12 的中・回収率 91.1%(区間 86.0〜98.0%)〔判断時オッズ・近似〕",
    );
    expect(screen.getByTestId("attention-rule-tally-S4")).toHaveTextContent(
      "次のチェックポイント なし(判定は終了)",
    );
  });

  it("S1 passed: 「300 点通過」, the 前向き未確認 badge drops, 探索後固定 stays", async () => {
    await renderOpen(richRules());
    expect(screen.getByTestId("attention-rule-stage-S1").textContent).toBe("300 点通過");
    expect(screen.getByTestId("attention-badge-posthoc-S1")).toBeInTheDocument();
    expect(screen.queryByTestId("attention-badge-unconfirmed-S1")).toBeNull();
    expect(screen.getByTestId("attention-rule-start-S1")).toHaveTextContent(
      "集計開始 2026-10-03・集計方針 v1",
    );
    expect(screen.getByTestId("attention-rule-frozen-S1")).toHaveTextContent(
      "123.1%(区間 102.2〜145.2%)〔判断時オッズ・近似〕・p=0.012",
    );
    expect(screen.getByTestId("attention-rule-stored-S1")).toHaveTextContent(
      "参考の回収率 118.7%(区間 99.1〜140.4%)〔保存オッズ(参考)・近似〕・318 点(保存オッズの無い 2 点は除く",
    );
  });

  it("S2 undecided and S3 judgment pending are spelled out", async () => {
    await renderOpen(richRules());
    expect(screen.getByTestId("attention-rule-stage-S2").textContent).toBe("判定保留");
    expect(screen.getByTestId("attention-rule-stage-reason-S2")).toHaveTextContent("判定保留(検出力不足)");
    const s2 = screen.getByTestId("attention-rule-decisions-S2");
    expect(within(s2).getByTestId("attention-decision-300")).toHaveTextContent(
      `300 点: ${DECISION_LABELS.continue}`,
    );
    expect(within(s2).getByTestId("attention-decision-600")).toHaveTextContent("600 点: 判定保留");
    expect(screen.getByTestId("attention-rule-stage-S3").textContent).toBe("観察中・判定待ち");
    expect(screen.getByTestId("attention-rule-stage-reason-S3")).toHaveTextContent("判定待ち");
  });

  it("lists the 9 exclusive exclusion classes in Japanese, Σ line, and the field-change flag apart", async () => {
    await renderOpen(richRules());
    const table = screen.getByTestId("attention-counts-S1");
    const rows = Array.from(table.querySelectorAll("tbody tr")).map((r) => r.textContent);
    expect(rows).toEqual([
      "集計対象320",
      "集計外: 取消 void1",
      "集計外: 集計開始前2",
      "集計外: 発走時刻不明3",
      "集計外: 発走後の計算4",
      "集計外: 計算時に結果確定済み5",
      "集計外: 発走後のオッズ6",
      "集計外: 結果未確定7",
      "集計外: 自馬の結果なし8",
      "集計外: 同着9",
      "合計(保存した該当の総数)365",
    ]);
    expect(Object.values(EXCLUSION_LABELS)).toHaveLength(9);
    expect(screen.getByTestId("attention-rule-S1")).toHaveTextContent(
      "集計対象 320 + 集計外 45 = 合計 365",
    );
    // the audit flag is shown separately, never one of the exclusive classes
    expect(table).not.toHaveTextContent("出走馬変更");
    expect(screen.getByTestId("attention-flag-S1")).toHaveTextContent(
      "監査フラグ 出走馬変更: 4 件",
    );
  });

  it("shows the judged-freshness breakdown and the odds-drift diagnostic as numbers", async () => {
    await renderOpen(richRules());
    expect(screen.getByTestId("attention-freshness-S1-<=10m")).toHaveTextContent(
      "10 分以内402111.1%〔判断時オッズ・近似〕",
    );
    expect(screen.getByTestId("attention-freshness-S1->60m")).toHaveTextContent(
      "60 分超2009133.3%〔判断時オッズ・近似〕",
    );
    expect(screen.getByTestId("attention-odds-drift-S1")).toHaveTextContent(
      "318 点・中央値 -0.041・10 パーセント点 -0.212・90 パーセント点 0.153",
    );
  });

  it("every ROI number carries its basis label (enumerated over all rules and bases)", async () => {
    const body = richRules();
    const { panel } = await renderOpen(body);
    const roiNodes = assertRoiBasisLabels(panel);

    const closing = ROI_BASIS_LABELS.closing_odds_approx;
    const frozen = ROI_BASIS_LABELS.frozen_pick_odds;
    const stored = ROI_BASIS_LABELS.stored_odds_mutable;
    for (const rule of body.items as RuleSummary[]) {
      const section = screen.getByTestId(`attention-rule-${rule.id}`);
      const nodes = Array.from(section.querySelectorAll<HTMLElement>('[data-kind="roi"]'));
      const bt = rule.backtest;
      const expected: [number | null, string][] = [
        [bt.all.roi, closing],
        [bt.c.roi, closing],
        [bt.selected.all.realized_roi, closing],
        [bt.selected.c.realized_roi, closing],
        ...rule.price_noise.map((n): [number, string] => [n.roi, closing]),
        [rule.prospective.frozen.roi, frozen],
        [rule.prospective.stored.roi, stored],
        ...rule.prospective.decisions.map((d): [number, string] => [d.roi_frozen, frozen]),
        ...Object.values(rule.prospective.by_judged_freshness).map(
          (b): [number | null, string] => [b.roi_frozen, frozen],
        ),
      ];
      for (const [value, label] of expected) {
        const shown = value === null ? "—" : pct(value);
        expect(hasRoiWithLabel(nodes, shown, label), `${rule.id} ${shown} 〔${label}〕`).toBe(true);
      }
    }
    expect(roiNodes.length).toBeGreaterThan(0);
  });

  it("display discipline: no steering words, no 印/推奨/おすすめ/通常, no profit colours, no win probability", async () => {
    const { panel } = await renderOpen(richRules());
    assertListDiscipline(panel);
    expect(panel.textContent).not.toMatch(/勝率/);
    // the selected-horse calibration is in expected-return units only
    expect(panel.textContent).not.toMatch(/p̂/);
  });

  it("shows the API disclaimer", async () => {
    await renderOpen();
    expect(screen.getByTestId("attention-rules-disclaimer")).toHaveTextContent(
      "的中や利益を保証するものではありません。",
    );
  });

  it("loading and error states are neutral (no alert role)", async () => {
    server.use(...happyHandlers);
    server.use(
      http.get(`${BASE}/attention-rules`, () =>
        HttpResponse.json({ status: 503, code: "error", detail: "boom" }, { status: 503 }),
      ),
    );
    renderWithProviders(<AttentionRulesPanel defaultOpen />);
    expect(screen.getByTestId("attention-rules-loading")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("attention-rules-error")).toBeInTheDocument());
    expect(screen.getByTestId("attention-rules-error")).toHaveTextContent("HTTP 503");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
