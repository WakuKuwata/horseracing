import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AttentionAvailable, AttentionHorse } from "../api/types";
import { ROI_BASIS_LABELS } from "../lib/attention";
import {
  assertAttentionDiscipline,
  assertRoiBasisCoverage,
} from "../tests/attentionDiscipline";
import { assertRoiBasisLabels } from "../tests/attentionScope";
import {
  attentionAvailableFixture,
  attentionHorseFixture,
  attentionRulesFixture,
  checkpointDecisionFixture,
  evSnapshotFixture,
  http,
  HttpResponse,
  pickStatusFor,
  stageDetail,
  stagesFor,
} from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { AttentionPanel } from "./AttentionPanel";

const MIN = 60 * 1000;
// A race that has not started yet: post 15:25 JST; rendered 3 h 10 min before the post.
const POST = "2026-10-03T06:25:00Z";
const NOW = new Date(POST).getTime() - (3 * 60 + 10) * MIN;
const PRE_POST: Pick<AttentionAvailable, "post_time" | "has_results" | "judged_at"> = {
  post_time: POST,
  has_results: false,
  judged_at: "2026-10-03T00:12:00Z", // 09:12 JST
};
// The default fixture race (2008, results in) → 価格鮮度 is 「発走後」.
const SETTLED = attentionAvailableFixture();

function useRules(rules = attentionRulesFixture()) {
  server.use(http.get("*/api/v1/attention-rules", () => HttpResponse.json(rules)));
}

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function renderPanel(
  horse: AttentionHorse,
  race: Pick<AttentionAvailable, "post_time" | "has_results" | "judged_at"> = SETTLED,
  now: number = NOW,
  cancelled = false,
) {
  return renderWithProviders(
    <AttentionPanel horse={horse} race={race} now={now} cancelled={cancelled} />,
  );
}

async function panel(): Promise<HTMLElement> {
  // the frozen registry arrives from /attention-rules — wait for it before asserting
  await screen.findByText(/通算\(2010〜26\)/);
  return screen.getByTestId("attention-panel");
}

/** An S2 horse: chip S1 + sub chip S2 (US1 scenario 2). */
function s2Horse(overrides: Partial<AttentionHorse> = {}): AttentionHorse {
  const applicable = ["S1", "S2", "S3", "S4", "S5"] as AttentionHorse["applicable"];
  return attentionHorseFixture({
    applicable,
    chip_s2: true,
    stages: stagesFor(applicable),
    pick_status: pickStatusFor(applicable),
    ...overrides,
  });
}

describe("AttentionPanel (138 T035)", () => {
  it("S1 horse at zero prospective picks: header, applicable ids, 4 axes, judged vs current", async () => {
    useRules();
    const { container } = renderPanel(attentionHorseFixture());
    const root = await panel();

    // header: chip label, both S1/S2 badges, progress as text label + dots
    expect(screen.getByTestId("attention-panel-title").textContent).toBe("注目条件 S1・研究中");
    expect(screen.getByTestId("attention-badge-posthoc")).toHaveTextContent("探索後固定");
    expect(screen.getByTestId("attention-badge-unconfirmed")).toHaveTextContent("前向き未確認");
    expect(screen.getByTestId("attention-progress").textContent).toBe("検証の進み具合●○○(3 段階中 1)");
    expect(root.querySelector(".attn-chip--sub")).toBeNull();

    // applicable ids: inclusion S1 ⊂ S3, S4 + the control listed as 対照 S5
    expect(screen.getByTestId("attention-applicable").textContent).toBe(
      "S1, S3, S4(対照 S5 にも該当)・判断時点 = このレースの最初の計算(2008/06/01 09:12)",
    );

    // 過去検証: frozen values with CI, n, hits, one-sided p, basis label, no multiplicity correction
    const backtest = screen.getByTestId("attention-backtest");
    expect(backtest).toHaveTextContent("通算の区間下限 100% 超・確認窓 110% 以上");
    expect(backtest).toHaveTextContent(
      "通算(2010〜26) 121.1%(区間 105.3〜137.1%・5,235 点・225 的中・p=0.0006)〔確定オッズ近似〕",
    );
    expect(backtest).toHaveTextContent(
      "確認窓(2019〜26) 117.7%(区間 85.5〜151.7%・1,183 点・47 的中・p=0.11)〔確定オッズ近似〕",
    );
    expect(backtest).toHaveTextContent("多重探索の補正なし");
    expect(screen.getByTestId("attention-selected").textContent).toBe(
      "選ばれた馬の期待回収率の平均 140.6% 推定(実際の回収率 121.1%〔確定オッズ近似〕)",
    );

    // 前向き検証: stage, counts, policy, start date (unset pre-launch), both bases, next checkpoint
    const prospective = screen.getByTestId("attention-prospective");
    expect(prospective).toHaveTextContent(
      "研究中(集計 0 点・0 的中・集計方針 v1・集計開始 未設定)",
    );
    expect(prospective).toHaveTextContent(
      "回収率 —〔判断時オッズ・近似〕・参考 —〔保存オッズ(参考)・近似〕",
    );
    expect(prospective).toHaveTextContent("次のチェックポイント(300 点)まで 300 点");

    // 価格ずれ試験: numbers per σ (ROI / n ratio / overlap), no strength words
    const rows = screen.getAllByTestId("attention-price-noise-row").map((r) => r.textContent);
    expect(rows).toEqual([
      "ずれ 10% で 116.6%〔確定オッズ近似〕(選ばれる馬は 1.2 倍・重なり 59%)",
      "ずれ 20% で 104.5%〔確定オッズ近似〕(選ばれる馬は 1.9 倍・重なり 30%)",
      "ずれ 30% で 96.7%〔確定オッズ近似〕(選ばれる馬は 2.8 倍・重なり 17%)",
    ]);
    expect(screen.getByTestId("attention-price-noise").textContent).not.toMatch(
      /強い|弱い|頑健|堅牢/,
    );

    // 価格鮮度: a settled race → 発走後 (no elapsed-time claim)
    expect(screen.getByTestId("attention-freshness-label").textContent).toBe("発走後");
    expect(screen.getByTestId("attention-freshness")).toHaveTextContent(
      "この間にオッズがどれだけ動くかは測っていません",
    );

    // 期待回収率: judged (frozen pick) vs current (latest ens15 row), both pseudo-badged
    expect(screen.getByTestId("attention-judged").textContent).toBe(
      "判断時点(2008/06/01 09:12): 15 seed 平均 125.0% 推定・単 seed 128.0% 推定・単勝 32.5 倍",
    );
    expect(screen.getByTestId("attention-current").textContent).toBe(
      "現在値(2008/06/01 15:00): 15 seed 平均 124.6% 推定・単 seed 126.0% 推定・単勝 30.8 倍" +
        "・S1 の条件を現在値でも満たす",
    );
    assertPseudoLabelCoverage(container, ["125.0%", "128.0%", "124.6%", "126.0%", "140.6%"]);
    expect(screen.queryByTestId("attention-field-changed")).toBeNull();
    expect(screen.getByTestId("attention-panel-disclaimer")).toHaveTextContent(
      "的中や利益を保証するものではありません",
    );
  });

  it("価格鮮度 from the CURRENT row and the render time: 42 min old, 3 h 10 min to post → 60 分以内", async () => {
    useRules();
    renderPanel(
      attentionHorseFixture({
        // judged odds are hours old — they must NOT drive freshness
        judged: evSnapshotFixture({ odds_observed_at: iso(NOW - 9 * 60 * MIN) }),
        current: evSnapshotFixture({ odds_observed_at: iso(NOW - 42 * MIN) }),
        levels: { backtest: 3, prospective: 2, price_noise: 2 },
      }),
      PRE_POST,
    );
    await panel();
    expect(screen.getByTestId("attention-freshness-label").textContent).toBe("60 分以内");
    expect(screen.getByTestId("attention-freshness")).toHaveTextContent(
      "60 分以内(最新の計算に使ったオッズの取得から 42 分・発走まで 3 時間 10 分)",
    );
    // emphasis = min(3, 2, 2, 2) = 2
    expect(screen.getByTestId("attention-progress").textContent).toBe("検証の進み具合●●○(3 段階中 2)");
  });

  it("emphasis is the minimum of the 4 axes (synthetic all-3 state → ●●●)", async () => {
    useRules();
    renderPanel(
      attentionHorseFixture({
        current: evSnapshotFixture({ odds_observed_at: iso(NOW - 5 * MIN) }),
        levels: { backtest: 3, prospective: 3, price_noise: 3 },
      }),
      PRE_POST,
    );
    await panel();
    expect(screen.getByTestId("attention-freshness-label").textContent).toBe("10 分以内");
    expect(screen.getByTestId("attention-progress").textContent).toBe("検証の進み具合●●●(3 段階中 3)");
  });

  it("「判断時点のみ該当」 forces the weakest emphasis even when every axis is 3 (US1 scenario 8)", async () => {
    useRules();
    renderPanel(
      attentionHorseFixture({
        chip_now: "no_longer",
        current: evSnapshotFixture({
          odds: 45.0, ens_expected_return: 1.22, single_expected_return: 1.19,
          odds_observed_at: iso(NOW - 5 * MIN),
        }),
        levels: { backtest: 3, prospective: 3, price_noise: 3 },
      }),
      PRE_POST,
    );
    await panel();
    expect(screen.getByTestId("attention-progress").textContent).toBe("検証の進み具合●○○(3 段階中 1)");
    const current = screen.getByTestId("attention-current");
    expect(current).toHaveTextContent("単勝 45.0 倍");
    expect(current).toHaveTextContent("判断時点のみ該当(現在値では S1 の条件を満たしません)");
    // judged values stay the frozen pick values (32.5 倍)
    expect(screen.getByTestId("attention-judged")).toHaveTextContent("単勝 32.5 倍");
  });

  it("「現在値なし」 + field changed after the pick (US1 scenario 11)", async () => {
    useRules();
    renderPanel(
      attentionHorseFixture({
        chip_now: "unknown",
        current: null,
        field_changed_after_pick: true,
        levels: { backtest: 3, prospective: 3, price_noise: 3 },
      }),
      PRE_POST,
    );
    await panel();
    expect(screen.getByTestId("attention-current")).toHaveTextContent("現在値: 現在値なし");
    expect(screen.getByTestId("attention-freshness-label").textContent).toBe("最新の計算なし");
    expect(screen.getByTestId("attention-progress").textContent).toBe("検証の進み具合●○○(3 段階中 1)");
    expect(screen.getByTestId("attention-field-changed")).toHaveTextContent(
      "出走馬がその後変わっています",
    );
  });

  it("S2 horse: sub chip S2 in the header, applicable S1, S2, S3, S4 (US1 scenario 2)", async () => {
    useRules();
    renderPanel(s2Horse());
    const root = await panel();
    expect(root.querySelector(".attn-chip--sub")?.textContent).toBe("S2");
    expect(screen.getByTestId("attention-applicable").textContent).toMatch(
      /^S1, S2, S3, S4\(対照 S5 にも該当\)/,
    );
  });

  it("S1 failed at 300 and S3 observing → panel on S3, S1 listed with its tag (US1 scenario 5)", async () => {
    useRules(
      attentionRulesFixture({
        S1: {
          prospective: {
            stage: "failed", checkpoint: 300, n_counted: 320,
            decisions: [checkpointDecisionFixture({ decision: "failed", ci: [0.62, 0.97],
              roi_frozen: 0.78 })],
          },
        },
        S3: { prospective: { stage: "observing", n_counted: 120, n_hits: 7 } },
      }),
    );
    const stages = {
      S1: stageDetail("failed", 300),
      S3: stageDetail("observing"),
      S4: stageDetail("observing"),
    };
    renderPanel(
      attentionHorseFixture({
        chip_rule: "S3",
        chip_stage: stages.S3,
        stages: stagesFor(["S1", "S3", "S4", "S5"], stages),
        levels: { backtest: 2, prospective: 2, price_noise: 2 },
      }),
    );
    await panel();
    expect(screen.getByTestId("attention-panel-title").textContent).toBe("注目条件 S3・観察中");
    expect(screen.getByTestId("attention-applicable").textContent).toMatch(
      /^S1\(300 点不通過\), S3, S4\(対照 S5 にも該当\)/,
    );
    // the S3 numbers are shown (chip rule), with the S3 backtest-axis words
    const backtest = screen.getByTestId("attention-backtest");
    expect(backtest).toHaveTextContent("通算・確認窓とも 100% 超");
    expect(backtest).toHaveTextContent("通算(2010〜26) 106.4%(区間 98.3〜115.1%");
    expect(screen.getByTestId("attention-prospective")).toHaveTextContent(
      "観察中(集計 120 点・7 的中",
    );
    // S1 (posthoc) is still applicable and not passed → both badges stay
    expect(screen.getByTestId("attention-badge-posthoc")).toBeInTheDocument();
    expect(screen.getByTestId("attention-badge-unconfirmed")).toBeInTheDocument();
  });

  it("a failed chip: main label is the stage name, decision record shown with its basis", async () => {
    useRules(
      attentionRulesFixture({
        S4: {
          prospective: {
            stage: "failed", checkpoint: 300, n_counted: 320,
            decisions: [checkpointDecisionFixture({ decision: "failed", ci: [0.86, 0.98],
              roi_frozen: 0.912 })],
          },
        },
      }),
    );
    const applicable = ["S4"] as AttentionHorse["applicable"];
    renderPanel(
      attentionHorseFixture({
        applicable,
        chip_rule: "S4",
        chip_stage: stageDetail("failed", 300),
        stages: stagesFor(applicable, { S4: stageDetail("failed", 300) }),
        pick_status: pickStatusFor(applicable),
        levels: { backtest: 2, prospective: 1, price_noise: 2 },
      }),
    );
    await panel();
    expect(screen.getByTestId("attention-panel-title").textContent).toBe(
      "注目条件 S4・300 点不通過",
    );
    expect(screen.getByTestId("attention-decision-300").textContent).toBe(
      "300 点の判定: 不通過(91.2%(区間 86.0〜98.0%)〔判断時オッズ・近似〕・12 的中・判定 2027/09/20 12:00)",
    );
    expect(screen.getByTestId("attention-prospective")).toHaveTextContent(
      "表示は続け、pick の保存も続けます",
    );
    // S4 is not a posthoc cell → no 探索後固定 / 前向き未確認 badges
    expect(screen.queryByTestId("attention-badge-posthoc")).toBeNull();
    expect(screen.queryByTestId("attention-badge-unconfirmed")).toBeNull();
  });

  it("passed S1 → 前向き未確認 disappears, 探索後固定 stays", async () => {
    useRules(
      attentionRulesFixture({
        S1: {
          prospective: {
            stage: "passed", checkpoint: 300, n_counted: 310,
            decisions: [checkpointDecisionFixture({ decision: "passed", ci: [1.02, 1.4] })],
            frozen: { valuation_basis: "frozen_pick_odds", roi: 1.21, ci: [1.02, 1.4],
              p_one_sided: 0.01 },
            stored: { valuation_basis: "stored_odds_mutable", roi: 1.19, ci: [1.0, 1.38], n: 310,
              n_missing_stored_odds: 0 },
          },
        },
      }),
    );
    renderPanel(
      attentionHorseFixture({
        chip_stage: stageDetail("passed", 300),
        stages: stagesFor(["S1", "S3", "S4", "S5"], { S1: stageDetail("passed", 300) }),
      }),
    );
    await panel();
    expect(screen.getByTestId("attention-panel-title").textContent).toBe("注目条件 S1・300 点通過");
    expect(screen.getByTestId("attention-badge-posthoc")).toBeInTheDocument();
    expect(screen.queryByTestId("attention-badge-unconfirmed")).toBeNull();
    expect(screen.getByTestId("attention-prospective")).toHaveTextContent(
      "回収率 121.0%(区間 102.0〜140.0%)〔判断時オッズ・近似〕・参考 119.0%〔保存オッズ(参考)・近似〕",
    );
  });

  it("observing with a pending checkpoint → 観察中・判定待ち (US2 scenario 7)", async () => {
    useRules(
      attentionRulesFixture({
        S1: { prospective: { stage: "observing", checkpoint_pending: true, n_counted: 304 } },
      }),
    );
    renderPanel(
      attentionHorseFixture({
        chip_stage: stageDetail("observing", null, true),
        stages: stagesFor(["S1", "S3", "S4", "S5"], { S1: stageDetail("observing", null, true) }),
      }),
    );
    await panel();
    expect(screen.getByTestId("attention-panel-title").textContent).toBe(
      "注目条件 S1・観察中・判定待ち",
    );
    expect(screen.getByTestId("attention-pending")).toHaveTextContent("判定待ち");
  });

  it("S5 only → just 「対照 S5 に該当」 (US1 scenario 7, no rules request)", () => {
    const applicable = ["S5"] as AttentionHorse["applicable"];
    renderPanel(
      attentionHorseFixture({
        applicable,
        chip_rule: null,
        chip_stage: null,
        chip_now: null,
        levels: null,
        stages: stagesFor(applicable),
        pick_status: pickStatusFor(applicable),
      }),
    );
    const root = screen.getByTestId("attention-panel");
    expect(root.textContent).toBe("対照 S5 に該当");
    expect(root.querySelector(".attn-chip")).toBeNull();
    expect(screen.queryByTestId("attention-progress")).toBeNull();
  });

  it("all picks voided (scratched) → the void fact only", async () => {
    useRules();
    renderPanel(
      attentionHorseFixture({
        applicable: [],
        chip_rule: null,
        chip_stage: null,
        chip_now: null,
        levels: null,
        stages: {},
        pick_status: { S1: "void:scratched", S2: "none", S3: "void:scratched",
          S4: "void:scratched", S5: "void:scratched" },
      }),
    );
    // which rule is the control comes from /attention-rules (`control`), not a constant
    await waitFor(() =>
      expect(screen.getByTestId("attention-voided").textContent).toBe(
        "出走取消のため無効: S1, S3, S4, 対照 S5",
      ),
    );
  });

  it("a cancelled horse whose pick is not voided yet: no chip title, badges or progress", async () => {
    // spec「取消馬: チップなし」: scratched after the pick, before the next computation appends
    // the void — the API still returns a live S1 chip, but the race table shows the scratch.
    useRules();
    const { container } = renderPanel(attentionHorseFixture(), PRE_POST, NOW, true);
    await waitFor(() =>
      expect(screen.getByTestId("attention-cancelled").textContent).toBe(
        "出走取消(判断時点の該当: S1, S3, S4, 対照 S5)",
      ),
    );
    expect(screen.queryByTestId("attention-panel-title")).toBeNull();
    expect(screen.queryByTestId("attention-badge-posthoc")).toBeNull();
    expect(screen.queryByTestId("attention-badge-unconfirmed")).toBeNull();
    expect(screen.queryByTestId("attention-progress")).toBeNull();
    expect(container.querySelector(".attn-chip")).toBeNull();
    expect(container.textContent).not.toMatch(/注目条件 S1・/);
  });

  it("a cancelled horse whose picks are already voided: the scratch and the voided rules", async () => {
    useRules();
    renderPanel(
      attentionHorseFixture({
        applicable: [],
        chip_rule: null,
        chip_stage: null,
        chip_now: null,
        levels: null,
        stages: {},
        pick_status: { S1: "void:scratched", S2: "none", S3: "void:scratched",
          S4: "void:scratched", S5: "void:scratched" },
      }),
      SETTLED,
      NOW,
      true,
    );
    await waitFor(() =>
      expect(screen.getByTestId("attention-voided").textContent).toBe(
        "出走取消のため無効: S1, S3, S4, 対照 S5",
      ),
    );
    expect(screen.getByTestId("attention-cancelled").textContent).toBe(
      "出走取消(判断時点の該当: S1, S3, S4, 対照 S5)",
    );
    expect(screen.queryByTestId("attention-progress")).toBeNull();
  });

  it("rules endpoint failure is shown neutrally (no alert role, no error colour)", async () => {
    server.use(
      http.get("*/api/v1/attention-rules", () =>
        HttpResponse.json({ status: 503, code: "error", detail: "boom" }, { status: 503 }),
      ),
    );
    const { container } = renderPanel(attentionHorseFixture());
    expect((await screen.findAllByTestId("attention-rules-error")).length).toBe(3);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelector(".state--error")).toBeNull();
    // the horse-level facts still render without the registry
    expect(screen.getByTestId("attention-judged")).toHaveTextContent("単勝 32.5 倍");
  });

  it("探索後固定 / 対照 follow the API's posthoc / control flags (no hard-coded rule ids)", async () => {
    // a registry where S1 is NOT posthoc and S4 is the control: the panel must follow the API
    useRules(
      attentionRulesFixture({
        S1: { posthoc: false },
        S2: { posthoc: false },
        S4: { control: true },
        S5: { control: false },
      }),
    );
    renderPanel(attentionHorseFixture());
    await panel();
    await waitFor(() =>
      expect(screen.getByTestId("attention-applicable").textContent).toMatch(
        /^S1, S3, S5\(対照 S4 にも該当\)/,
      ),
    );
    expect(screen.queryByTestId("attention-badge-posthoc")).toBeNull();
    expect(screen.queryByTestId("attention-badge-unconfirmed")).toBeNull();
  });

  it("while the rules list is loading: no 探索後固定 / 前向き未確認 badges are guessed", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("*/api/v1/attention-rules", async () => {
        await gate;
        return HttpResponse.json(attentionRulesFixture());
      }),
    );
    renderPanel(attentionHorseFixture());
    expect(screen.getByTestId("attention-panel-title").textContent).toBe("注目条件 S1・研究中");
    expect(screen.queryByTestId("attention-badge-posthoc")).toBeNull();
    expect(screen.queryByTestId("attention-badge-unconfirmed")).toBeNull();
    release();
    expect(await screen.findByTestId("attention-badge-posthoc")).toHaveTextContent("探索後固定");
    expect(screen.getByTestId("attention-badge-unconfirmed")).toHaveTextContent("前向き未確認");
  });

  it("the progress dots are aria-hidden and the level is readable as text", async () => {
    useRules();
    renderPanel(attentionHorseFixture());
    await panel();
    const progress = screen.getByTestId("attention-progress");
    expect(progress.querySelector(".attn-level__dots")?.getAttribute("aria-hidden")).toBe("true");
    expect(progress.querySelector(".visually-hidden")?.textContent).toBe("(3 段階中 1)");
  });

  it.each([
    ["zero picks", attentionRulesFixture(), attentionHorseFixture()],
    [
      "decisions on every basis",
      attentionRulesFixture({
        S1: {
          prospective: {
            stage: "observing", n_counted: 310, n_hits: 14,
            decisions: [checkpointDecisionFixture()],
            frozen: { valuation_basis: "frozen_pick_odds", roi: 1.04, ci: [0.81, 1.29],
              p_one_sided: 0.3 },
            stored: { valuation_basis: "stored_odds_mutable", roi: 1.02, ci: [0.8, 1.27], n: 310,
              n_missing_stored_odds: 0 },
          },
        },
      }),
      s2Horse({ chip_now: "no_longer", field_changed_after_pick: true }),
    ],
    [
      "a failed chip rule (criterion note)",
      attentionRulesFixture({
        S4: {
          prospective: {
            stage: "failed", checkpoint: 300, n_counted: 320,
            decisions: [checkpointDecisionFixture({ decision: "failed", ci: [0.86, 0.98] })],
          },
        },
      }),
      attentionHorseFixture({
        applicable: ["S4"], chip_rule: "S4", chip_stage: stageDetail("failed", 300),
        stages: stagesFor(["S4"], { S4: stageDetail("failed", 300) }),
        pick_status: pickStatusFor(["S4"]), chip_now: "unknown", current: null,
      }),
    ],
  ])("%s: every ROI figure carries a basis label; scoped discipline holds", async (_l, rules, horse) => {
    useRules(rules);
    renderPanel(horse, PRE_POST);
    const root = await panel();
    const n = assertRoiBasisCoverage(root);
    // backtest 2 + selected 1 + prospective 2 + price noise 3 (+ decisions)
    expect(n).toBeGreaterThanOrEqual(8);
    for (const label of Object.values(ROI_BASIS_LABELS)) {
      expect(root.textContent).toContain(label);
    }
    assertAttentionDiscipline(root);
    // no percentage escapes a labelled node (an ROI typed inline would have no basis label)
    assertRoiBasisLabels(root);
    expect(root.textContent).not.toMatch(/印|通常/);
    expect(root.querySelector("button, input, select, a")).toBeNull();
  });
});
