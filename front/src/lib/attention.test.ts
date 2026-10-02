import { describe, expect, it } from "vitest";

import type { AttentionLevels, RuleSummary, StageDetail } from "../api/types";
import {
  ATTENTION_SCOPE,
  UNMEASURED_ODDS_DRIFT,
} from "./forbiddenPhrases";
import {
  BACKTEST_LEVEL_LABELS,
  CHIP_NOW_LABELS,
  DECISION_LABELS,
  EXCLUSION_LABELS,
  EXCLUSION_ORDER,
  FRESHNESS_LABELS,
  PROGRESS_LABEL,
  ROI_BASIS_LABELS,
  ROI_BASIS_LABEL_VALUES,
  backtestLevelLabel,
  chipLabel,
  chipNowLabel,
  emphasisLevel,
  freshnessLevel,
  isClosedStage,
  progressDots,
  progressScaleText,
  roiBasisTag,
  ruleFlags,
  ruleIdText,
  stageLabel,
} from "./attention";

const POST = "2026-10-03T06:25:00Z"; // 15:25 JST
const POST_MS = new Date(POST).getTime();
const MIN = 60 * 1000;

/** The render time `minutesBeforePost` before the post, with the latest odds `ageMinutes` old. */
function at(ageMinutes: number, minutesBeforePost = 120) {
  const now = POST_MS - minutesBeforePost * MIN;
  const observed = new Date(now - ageMinutes * MIN).toISOString();
  return { now, observed };
}

function stage(
  s: StageDetail["stage"],
  checkpoint: StageDetail["checkpoint"] = null,
  checkpoint_pending = false,
): StageDetail {
  return { stage: s, checkpoint, checkpoint_pending };
}

describe("freshnessLevel (価格鮮度・現在値の取得時刻 × 描画時刻)", () => {
  it("is 3 「10 分以内」 up to and INCLUDING exactly 10 minutes", () => {
    const { now, observed } = at(10);
    expect(freshnessLevel(observed, POST, false, now)).toEqual({
      level: 3, reason: "within10m", label: "10 分以内",
    });
    const fresh = at(0);
    expect(freshnessLevel(fresh.observed, POST, false, fresh.now).level).toBe(3);
  });

  it("drops to 2 「60 分以内」 just past 10 minutes and stays 2 at exactly 60 minutes", () => {
    const now = POST_MS - 120 * MIN;
    const justPast10 = new Date(now - 10 * MIN - 1).toISOString();
    expect(freshnessLevel(justPast10, POST, false, now)).toEqual({
      level: 2, reason: "within60m", label: "60 分以内",
    });
    const { now: n60, observed: o60 } = at(60);
    expect(freshnessLevel(o60, POST, false, n60).level).toBe(2);
  });

  it("drops to 1 「60 分超」 just past 60 minutes", () => {
    const now = POST_MS - 120 * MIN;
    const justPast60 = new Date(now - 60 * MIN - 1).toISOString();
    expect(freshnessLevel(justPast60, POST, false, now)).toEqual({
      level: 1, reason: "over60m", label: "60 分超",
    });
  });

  it("accepts the render time as a Date as well as epoch ms", () => {
    const { now, observed } = at(5);
    expect(freshnessLevel(observed, POST, false, new Date(now)).level).toBe(3);
  });

  it("is 1 「発走後」 from the post time on (exactly at the post, and after)", () => {
    const observed = new Date(POST_MS - 2 * MIN).toISOString();
    expect(freshnessLevel(observed, POST, false, POST_MS)).toEqual({
      level: 1, reason: "afterPost", label: "発走後",
    });
    expect(freshnessLevel(observed, POST, false, POST_MS + 30 * MIN).label).toBe("発走後");
    // one millisecond before the post the odds are still fresh
    expect(freshnessLevel(observed, POST, false, POST_MS - 1).level).toBe(3);
  });

  it("is 「発走後」 whenever the race has results, even with an unknown post time", () => {
    const { now, observed } = at(1);
    expect(freshnessLevel(observed, POST, true, now).label).toBe("発走後");
    expect(freshnessLevel(observed, null, true, now).label).toBe("発走後");
  });

  it("is 1 「発走時刻不明」 when the post time is unknown or unparseable", () => {
    const { now, observed } = at(1);
    expect(freshnessLevel(observed, null, false, now)).toEqual({
      level: 1, reason: "postUnknown", label: "発走時刻不明",
    });
    expect(freshnessLevel(observed, undefined, null, now).label).toBe("発走時刻不明");
    expect(freshnessLevel(observed, "not-a-time", false, now).label).toBe("発走時刻不明");
  });

  it("is 1 「最新の計算なし」 when there is no current row (null / unparseable)", () => {
    const now = POST_MS - 30 * MIN;
    expect(freshnessLevel(null, POST, false, now)).toEqual({
      level: 1, reason: "noCurrent", label: "最新の計算なし",
    });
    expect(freshnessLevel(undefined, POST, false, now).label).toBe("最新の計算なし");
    expect(freshnessLevel("garbage", POST, false, now).label).toBe("最新の計算なし");
    // no current row AND no post time: the missing calculation is said first
    expect(freshnessLevel(null, null, false, now).label).toBe("最新の計算なし");
    // a finished race says 発走後 even without a current row
    expect(freshnessLevel(null, POST, true, now).label).toBe("発走後");
  });

  it("treats an observation time after the render time (clock skew) as age 0", () => {
    const now = POST_MS - 30 * MIN;
    const future = new Date(now + 5 * MIN).toISOString();
    expect(freshnessLevel(future, POST, false, now).level).toBe(3);
  });
});

describe("emphasisLevel (4 軸の最小値)", () => {
  const S1_LEVELS: AttentionLevels = { backtest: 3, prospective: 1, price_noise: 2 };

  it("is the minimum of the 4 axes", () => {
    const all3: AttentionLevels = { backtest: 3, prospective: 3, price_noise: 3 };
    expect(emphasisLevel(all3, 3, "matches")).toBe(3);
    expect(emphasisLevel(all3, 2, "matches")).toBe(2);
    expect(emphasisLevel(all3, 1, "matches")).toBe(1);
    expect(emphasisLevel({ ...all3, backtest: 2 }, 3, "matches")).toBe(2);
    expect(emphasisLevel({ ...all3, prospective: 1 }, 3, "matches")).toBe(1);
    expect(emphasisLevel({ ...all3, price_noise: 2 }, 3, "matches")).toBe(2);
  });

  it("with the frozen values and zero prospective picks, every rule starts at the weakest (SC-003)", () => {
    expect(emphasisLevel(S1_LEVELS, 3, "matches")).toBe(1);
    expect(emphasisLevel({ backtest: 2, prospective: 1, price_noise: 2 }, 3, "matches")).toBe(1);
  });

  it("is forced to 1 when chip_now is no_longer / unknown, whatever the axes", () => {
    const all3: AttentionLevels = { backtest: 3, prospective: 3, price_noise: 3 };
    expect(emphasisLevel(all3, 3, "no_longer")).toBe(1);
    expect(emphasisLevel(all3, 3, "unknown")).toBe(1);
    expect(emphasisLevel(all3, 3, null)).toBe(1);
  });

  it("is 1 without axis levels (no chip)", () => {
    expect(emphasisLevel(null, 3, "matches")).toBe(1);
    expect(emphasisLevel(undefined, 3, "matches")).toBe(1);
  });

  it("is fixed to 1 when the chip's stage is failed / undecided (主ラベル=段階名)", () => {
    const all3: AttentionLevels = { backtest: 3, prospective: 3, price_noise: 3 };
    expect(emphasisLevel(all3, 3, "matches", stage("failed", 300))).toBe(1);
    expect(emphasisLevel(all3, 3, "matches", stage("undecided", 600))).toBe(1);
    expect(emphasisLevel(all3, 3, "matches", stage("passed", 300))).toBe(3);
    expect(emphasisLevel(all3, 2, "matches", stage("observing", 300, true))).toBe(2);
  });
});

describe("stageLabel / chipLabel (ASCII → 日本語)", () => {
  it("maps every stage", () => {
    expect(stageLabel(stage("researching"))).toBe("研究中");
    expect(stageLabel(stage("observing"))).toBe("観察中");
    expect(stageLabel(stage("observing", 300))).toBe("観察中");
    expect(stageLabel(stage("observing", null, true))).toBe("観察中・判定待ち");
    expect(stageLabel(stage("observing", 300, true))).toBe("観察中・判定待ち");
    expect(stageLabel(stage("passed", 300))).toBe("300 点通過");
    expect(stageLabel(stage("passed", 600))).toBe("600 点通過");
    expect(stageLabel(stage("failed", 300))).toBe("300 点不通過");
    expect(stageLabel(stage("failed", 600))).toBe("600 点不通過");
    expect(stageLabel(stage("undecided", 600))).toBe("判定保留");
  });

  it("never invents a checkpoint number", () => {
    expect(stageLabel(stage("passed"))).toBe("通過");
    expect(stageLabel(stage("failed"))).toBe("不通過");
  });

  it("builds the chip's main label with no threshold number", () => {
    expect(chipLabel("S1", stage("researching"))).toBe("注目条件 S1・研究中");
    expect(chipLabel("S4", stage("failed", 300))).toBe("注目条件 S4・300 点不通過");
    expect(chipLabel("S3", stage("observing", 300, true))).toBe("注目条件 S3・観察中・判定待ち");
    expect(chipLabel("S2", stage("researching"))).not.toMatch(/\d+\s*%|超/);
  });

  it("marks failed / undecided as closed stages", () => {
    expect(isClosedStage("failed")).toBe(true);
    expect(isClosedStage("undecided")).toBe(true);
    expect(isClosedStage("researching")).toBe(false);
    expect(isClosedStage("observing")).toBe(false);
    expect(isClosedStage("passed")).toBe(false);
  });
});

describe("axis wording (FR-006)", () => {
  it("states the backtest axis criteria in the same words as the rule", () => {
    expect(backtestLevelLabel(3)).toBe("通算の区間下限 100% 超・確認窓 110% 以上");
    expect(backtestLevelLabel(2)).toBe("通算・確認窓とも 100% 超");
    expect(backtestLevelLabel(1)).toBe("通算か確認窓が 100% 以下");
  });

  it("draws the progress as text dots, always next to its label", () => {
    expect(PROGRESS_LABEL).toBe("検証の進み具合");
    expect(progressDots(1)).toBe("●○○");
    expect(progressDots(2)).toBe("●●○");
    expect(progressDots(3)).toBe("●●●");
    // the dots are aria-hidden; screen readers get the level as text
    expect(progressScaleText(1)).toBe("3 段階中 1");
    expect(progressScaleText(3)).toBe("3 段階中 3");
  });

  it("spells the checkpoint decisions and the 9 exclusive exclusion classes (single source)", () => {
    expect(DECISION_LABELS).toEqual({
      passed: "通過", failed: "不通過", continue: "継続", undecided: "判定保留",
    });
    // classify_pick priority order, 9 exclusive classes
    expect(EXCLUSION_ORDER).toEqual([
      "voided_scratched", "before_start", "post_time_unknown", "computed_after_post",
      "result_known_at_compute", "observed_after_post", "pending_result", "unsettled_horse",
      "dead_heat",
    ]);
    expect(EXCLUSION_ORDER.map((k) => EXCLUSION_LABELS[k])).toEqual([
      "取消 void", "集計開始前", "発走時刻不明", "発走後の計算", "計算時に結果確定済み",
      "発走後のオッズ", "結果未確定", "自馬の結果なし", "同着",
    ]);
  });

  it("annotates the chip only when the current values no longer match / are missing", () => {
    expect(chipNowLabel("no_longer")).toBe("判断時点のみ該当");
    expect(chipNowLabel("unknown")).toBe("現在値なし");
    expect(chipNowLabel("matches")).toBeNull();
    expect(chipNowLabel(null)).toBeNull();
    expect(chipNowLabel(undefined)).toBeNull();
  });

  it("freezes the three ROI basis labels, keyed by the API valuation_basis", () => {
    expect(ROI_BASIS_LABELS).toEqual({
      closing_odds_approx: "確定オッズ近似",
      frozen_pick_odds: "判断時オッズ・近似",
      stored_odds_mutable: "保存オッズ(参考)・近似",
    });
    expect([...ROI_BASIS_LABEL_VALUES].sort()).toEqual(
      Object.values(ROI_BASIS_LABELS).sort(),
    );
    // every label says it is an approximation (no official win payout is stored)
    for (const label of ROI_BASIS_LABEL_VALUES) expect(label).toMatch(/近似/);
    expect(roiBasisTag("closing_odds_approx")).toBe("〔確定オッズ近似〕");
  });

  it("never uses a forbidden phrase, an unmeasured drift claim or 「通常」", () => {
    const vocabulary = [
      ...Object.values(ROI_BASIS_LABELS),
      ...Object.values(FRESHNESS_LABELS),
      ...Object.values(BACKTEST_LEVEL_LABELS),
      ...Object.values(CHIP_NOW_LABELS),
      ...Object.values(DECISION_LABELS),
      ...Object.values(EXCLUSION_LABELS),
      PROGRESS_LABEL,
      progressScaleText(1),
      progressScaleText(2),
      progressScaleText(3),
      ruleIdText("S5", new Set(["S5"] as const)),
      ...(["researching", "observing", "passed", "failed", "undecided"] as const).flatMap(
        (s) => [
          stageLabel(stage(s, 300)),
          stageLabel(stage(s, 600, true)),
          chipLabel("S1", stage(s, 300)),
        ],
      ),
    ];
    for (const text of vocabulary) {
      expect(text).not.toMatch(ATTENTION_SCOPE);
      expect(text).not.toMatch(UNMEASURED_ODDS_DRIFT);
      expect(text).not.toMatch(/通常|印|推奨|おすすめ/);
    }
  });
});

describe("ATTENTION_SCOPE", () => {
  it("catches purchase-inducing words but allows 条件 / 回収率 / bare 買い", () => {
    for (const bad of [
      "おすすめ", "推奨", "買い目", "買え", "買うべき", "狙い目", "勝負", "勝てる", "お得",
      "利益が出る", "妙味", "危険", "儲かる", "edge",
    ]) {
      expect(bad).toMatch(ATTENTION_SCOPE);
    }
    for (const ok of ["注目条件", "回収率", "買い方", "利益を保証するものではありません"]) {
      expect(ok).not.toMatch(ATTENTION_SCOPE);
    }
  });
});

describe("ruleFlags / ruleIdText (registry attributes come from the API, not constants)", () => {
  function rule(id: RuleSummary["id"], posthoc: boolean, control: boolean): RuleSummary {
    return { id, posthoc, control } as RuleSummary;
  }

  it("reads posthoc / control from the rules list", () => {
    const flags = ruleFlags([
      rule("S1", true, false),
      rule("S2", true, false),
      rule("S3", false, false),
      rule("S5", false, true),
    ]);
    expect(flags && [...flags.posthoc]).toEqual(["S1", "S2"]);
    expect(flags && [...flags.control]).toEqual(["S5"]);
    // a registry change (e.g. S3 re-flagged) follows the API, nothing hard-coded
    expect([...(ruleFlags([rule("S3", true, true)])?.posthoc ?? [])]).toEqual(["S3"]);
  });

  it("is unknown (null) while the list is not loaded", () => {
    expect(ruleFlags(undefined)).toBeNull();
    expect(ruleFlags(null)).toBeNull();
  });

  it("prefixes 対照 only when the rule is known to be a control", () => {
    const control = new Set(["S5"] as const);
    expect(ruleIdText("S5", control)).toBe("対照 S5");
    expect(ruleIdText("S1", control)).toBe("S1");
    expect(ruleIdText("S5", null)).toBe("S5");
  });
});
