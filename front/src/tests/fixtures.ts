import { http, HttpResponse } from "msw";

import type {
  AttentionAvailable,
  AttentionDayItem,
  AttentionDayResponse,
  AttentionFrozenStats,
  AttentionHorse,
  AttentionPriceNoise,
  AttentionProspective,
  AttentionRulesResponse,
  AttentionSelectedCalibration,
  AttentionUnavailable,
  CalibrationResponse,
  CheckpointDecision,
  EvSnapshot,
  ImportanceResponse,
  MarketEvAvailable,
  MarketEvUnavailable,
  OddsResponse,
  PredictionResponse,
  RaceDetail,
  RacePage,
  RecommendationResponse,
  RuleId,
  RuleSummary,
  StageDetail,
} from "../api/types";

const BASE = "*/api/v1";

export const racePage: RacePage = {
  items: [
    {
      race_id: "200806010111",
      race_date: "2008-06-01",
      venue_code: "05",
      race_number: 11,
      race_class: "G1",
      track_type: "芝",
      distance: 2400,
      has_results: true,
    },
  ],
  page: 1,
  page_size: 20,
  total: 1,
  has_next: false,
};

export const raceDetail: RaceDetail = {
  race_id: "200806010111",
  race_date: "2008-06-01",
  venue_code: "05",
  race_number: 11,
  race_class: "G1",
  track_type: "芝",
  distance: 2400,
  has_results: true,
  horses: [
    { horse_id: "h1", horse_number: 1, entry_status: "active", age: 4, sex: "牡" },
    { horse_id: "h2", horse_number: 2, entry_status: "active", age: 5, sex: "牝" },
  ],
};

export const predictionResponse: PredictionResponse = {
  race_id: "200806010111",
  horses: [
    { horse_id: "h1", horse_number: 1, win: 0.32, top2: 0.55, top3: 0.7,
      market_win_prob: 0.3, prior_starts_band: "many",
      divergence: "model_higher",
      explanation: {
        method: "lgbm_pred_contrib", method_version: 1, k: 2,
        base_value: -3.0, score: -2.4, other_contribution: 0.1,
        items: [
          { feature: "te_jockey_id", value: 0.08, contribution: 0.5 },
          { feature: "rel_time_avg", value: -0.3, contribution: -0.2 },
        ],
      } },
    { horse_id: "h2", horse_number: 2, win: 0.18, top2: 0.4, top3: 0.58,
      market_win_prob: 0.2, prior_starts_band: "few",
      divergence: null, explanation: null },
  ],
  joint: null,
  joint_bet_type: null,
  joint_logic_version: null,
  market_prob_source: "win_odds_vote_share",
  canonical_consistent: true,
  odds_as_of: "2008-06-01T05:00:00Z",
  odds_source: "final",
  available_models: [],
  run: {
    prediction_run_id: "run-abc",
    logic_version: "009.1",
    model_version: "lgbm-006",
    computed_at: "2008-05-31T22:00:00Z",
  },
};

export const jointResponse: PredictionResponse = {
  ...predictionResponse,
  joint: [
    { selection: [1, 2], prob: 0.21 },
    { selection: [1, 3], prob: 0.14 },
  ],
  joint_bet_type: "quinella",
  joint_logic_version: "009.1",
};

export const oddsResponse: OddsResponse = {
  race_id: "200806010111",
  win: [
    { horse_id: "h1", horse_number: 1, odds: 3.1, is_estimated: false, odds_source: "real", updated_at: "2008-06-01T05:00:00Z" },
    { horse_id: "h2", horse_number: 2, odds: 5.4, is_estimated: false, odds_source: "real", updated_at: "2008-06-01T05:00:00Z" },
  ],
  estimated: [
    { bet_type: "win", selection: [1], odds: 3.2, is_estimated: true, odds_source: "estimated", pseudo: true, as_of: "2008-06-01T05:00:00Z" },
    { bet_type: "quinella", selection: [1, 2], odds: 12.3, is_estimated: true, odds_source: "estimated", pseudo: true, as_of: "2008-06-01T05:00:00Z" },
  ],
  real_exotic: [
    { bet_type: "quinella", selection: [1, 2], odds: 10.8, is_estimated: false, odds_source: "real", coverage_scope: "full", updated_at: "2008-06-01T16:00:00Z" },
  ],
};

export const recommendationResponse: RecommendationResponse = {
  race_id: "200806010111",
  win_policy_status: "generated",
  favorite_baseline: null,
  items: [
    {
      recommendation_id: "rec-1",
      bet_type: "quinella",
      selection: [1, 2],
      stake_fraction: 0.0123,
      market_odds_used: null,
      estimated_market_odds_used: 12.3,
      is_estimated_odds: true,
      pseudo_odds: 4.5,
      pseudo_roi: 0.18,
      double_pseudo: true,
      logic_version: "011.1",
      computed_at: "2008-05-31T22:30:00Z",
      prediction_run_id: "run-abc",
      settled: false,
      dead_heat: false,
    },
    {
      // Feature 049: a SETTLED win recommendation that HIT (real odds ×3.2 → +220%).
      recommendation_id: "rec-win-hit",
      bet_type: "win",
      selection: [1],
      stake_fraction: 0.02,
      market_odds_used: 3.2,
      estimated_market_odds_used: null,
      is_estimated_odds: false,
      pseudo_odds: 3.1,
      pseudo_roi: 0.35,
      double_pseudo: false,
      logic_version: "win-lv",
      computed_at: "2008-05-31T22:30:00Z",
      prediction_run_id: "run-abc",
      settled: true,
      hit: true,
      dead_heat: false,
      counterfactual_snapshot_gross_return: 3.2,
      counterfactual_snapshot_net_return: 2.2,
    },
    {
      // Feature 049: a SETTLED win recommendation that MISSED (return 0 → -100%).
      recommendation_id: "rec-win-miss",
      bet_type: "win",
      selection: [2],
      stake_fraction: null,
      market_odds_used: 8.0,
      estimated_market_odds_used: null,
      is_estimated_odds: false,
      pseudo_odds: 6.0,
      pseudo_roi: 0.1,
      double_pseudo: false,
      logic_version: "win-lv",
      computed_at: "2008-05-31T22:30:00Z",
      prediction_run_id: "run-abc",
      settled: true,
      hit: false,
      dead_heat: false,
      counterfactual_snapshot_gross_return: 0.0,
      counterfactual_snapshot_net_return: -1.0,
    },
  ],
};

export const calibrationResponse: CalibrationResponse = {
  model_version: "lgbm-006",
  oos: true,
  source: "walk_forward_oos",
  label: "win",
  valid_years: [2008, 2009],
  n_total: 200,
  ece: 0.012,
  bins: [
    {
      pred_lo: 0.0, pred_hi: 0.1, pred_mean: 0.05, realized_rate: 0.06,
      realized_ci_low: 0.03, realized_ci_high: 0.09, count: 150, suppressed: false,
    },
    {
      pred_lo: 0.5, pred_hi: 0.6, pred_mean: 0.55, realized_rate: 0.5,
      realized_ci_low: 0.2, realized_ci_high: 0.8, count: 4, suppressed: true,
    },
  ],
};

export const importanceResponse: ImportanceResponse = {
  model_version: "lgbm-006",
  type: "gain",
  values: [
    { feature: "rel_time_avg", gain: 250.0 },
    { feature: "te_jockey_id", gain: 120.0 },
    { feature: "venue_code", gain: 40.0 },
  ],
};

export const shadowLogResponse = {
  n_prospective: 3, n_settled: 2, n_hit: 1, hit_rate: 0.5,
  counterfactual_snapshot_recovery_rate: 0.9,
  n_pending: 1, n_void: 0, weak_pretime: 0,
  by_month: [{ month: "2026-08", n_settled: 2, counterfactual_snapshot_recovery: 0.9 }],
  first_at: "2026-08-01T09:00:00", last_at: "2026-08-15T09:00:00",
};

// Feature 137: the default is the typed empty state (not computed) so no existing test sees an
// extra 期待回収率 column or percentage string; wiring tests install marketEvAvailable explicitly.
export const marketEvNotComputed: MarketEvUnavailable = {
  status: "unavailable",
  race_id: "200806010111",
  reason: "not_computed",
  threshold: 1.2,
};

export const marketEvAvailable: MarketEvAvailable = {
  status: "available",
  race_id: "200806010111",
  model_version: "mev-binary-v2",
  logic_version: "mev-v1;features=roi-explore-2026-09;drop=sameday,weightlive;data>=2007",
  computed_at: "2008-06-01T01:15:00Z",
  odds_observed_at: "2008-06-01T01:10:00Z",
  odds_changed_after_compute: false,
  result_pending_at_compute: true,
  threshold: 1.2,
  is_pseudo: true,
  horses: [
    { horse_id: "h1", horse_number: 1, expected_return: 1.237, odds_used: 3.1,
      exceeds_threshold: true },
    { horse_id: "h2", horse_number: 2, expected_return: 0.864, odds_used: 5.4,
      exceeds_threshold: false },
  ],
};

// --- Feature 138: 注目条件 (attention conditions S1–S5) ----------------------------------------
// Frozen values below are the production refreeze (2026-10-02, evidence/rules_S1_S5_freeze.json) as
// the API serves them (ratio units: 1.210735 = 121.1%). The default handlers serve the typed empty
// race state (not computed), the 5 rules at zero prospective picks (pre-launch: start_date null,
// every rule 研究中) and an empty day — so no existing test sees an extra chip. Component tests
// build richer states with the builders (overrides are shallow; `prospective` is merged one level).

/** The race's first computation has not happened yet (typed 200, not an error). */
export const attentionNotComputed: AttentionUnavailable = {
  status: "unavailable",
  race_id: "200806010111",
  reason: "not_computed",
};

/** API's fixed rules-list disclaimer (attention.DISCLAIMER). */
export const ATTENTION_RULES_DISCLAIMER =
  "注目条件は過去データで最も有望だった条件で、検証済みの条件ではありません。" +
  "S1・S2 は結果を見てから見つけた条件で、過去検証の p 値は多重探索を補正していません。" +
  "回収率はいずれも近似です。的中や利益を保証するものではありません。";

export function stageDetail(
  stage: StageDetail["stage"] = "researching",
  checkpoint: StageDetail["checkpoint"] = null,
  checkpoint_pending = false,
): StageDetail {
  return { stage, checkpoint, checkpoint_pending };
}

function frozen(
  roi: number, ci_low: number, ci_high: number, p_one_sided: number, n: number, hits: number,
): AttentionFrozenStats {
  return { roi, ci_low, ci_high, p_one_sided, n, hits };
}

function selected(n: number, mean_ev: number, realized_roi: number): AttentionSelectedCalibration {
  return { n, mean_ev, realized_roi };
}

function noise(sigma: number, roi: number, n: number, overlap: number): AttentionPriceNoise {
  return { sigma, roi, n, overlap };
}

type FrozenRule = Omit<RuleSummary, "prospective">;

const FROZEN_BACKTEST_BOOTSTRAP = {
  impl: "horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1",
  b: 20000,
  seed: 20260905,
  block: "race_day",
  block_universe: "race-days with >=1 selected bet of the rule in the window",
};

function frozenRule(
  id: RuleId,
  rank: number,
  definition_ja: string,
  shape: Pick<RuleSummary, "uses_ensemble" | "ev_gt" | "odds_band" | "gap_days" | "posthoc" | "control">,
  all: AttentionFrozenStats,
  c: AttentionFrozenStats,
  bets: number[],
  sel: [AttentionSelectedCalibration, AttentionSelectedCalibration],
  priceNoise: AttentionPriceNoise[],
  levels: RuleSummary["levels"],
): FrozenRule {
  return {
    id, rank, definition_ja, ...shape,
    backtest: {
      all, c, bets_2024_25_26: bets,
      selected: { all: sel[0], c: sel[1] },
      valuation_basis: "closing_odds_approx",
      bootstrap: { ...FROZEN_BACKTEST_BOOTSTRAP },
    },
    price_noise: priceNoise,
    levels,
  };
}

const BAND_RULE = { odds_band: [20.0, 40.0] as [number, number], gap_days: [14, 112] as [number, number] };

/** Frozen registry (definitions + backtest + price-noise test + axis levels) in rank order. */
export const FROZEN_ATTENTION_RULES: Record<RuleId, FrozenRule> = {
  S1: frozenRule(
    "S1", 1, "15 seed 平均の期待回収率が 120% 超、単勝 20 倍以上 40 倍未満、前走から 14〜112 日",
    { uses_ensemble: true, ev_gt: 1.2, ...BAND_RULE, posthoc: true, control: false },
    frozen(1.210735, 1.053376, 1.371353, 0.0006, 5235, 225),
    frozen(1.176839, 0.854896, 1.517019, 0.114644, 1183, 47),
    [97, 105, 102],
    [selected(5235, 1.405512, 1.210735), selected(1183, 1.32409, 1.176839)],
    [noise(0.1, 1.166235, 6432, 0.586), noise(0.2, 1.044695, 9981, 0.3),
      noise(0.3, 0.966821, 14786, 0.165)],
    { backtest: 3, price_noise: 2 },
  ),
  S2: frozenRule(
    "S2", 2, "15 seed 平均の期待回収率が 130% 超、単勝 20 倍以上 40 倍未満、前走から 14〜112 日",
    { uses_ensemble: true, ev_gt: 1.3, ...BAND_RULE, posthoc: true, control: false },
    frozen(1.291924, 1.08847, 1.506363, 0.00015, 3108, 141),
    frozen(1.401758, 0.886217, 1.972972, 0.026799, 512, 24),
    [43, 32, 46],
    [selected(3108, 1.515389, 1.291924), selected(512, 1.430179, 1.401758)],
    [noise(0.1, 1.227577, 3899, 0.568), noise(0.2, 1.076371, 6620, 0.264),
      noise(0.3, 0.983593, 10844, 0.132)],
    { backtest: 3, price_noise: 2 },
  ),
  S3: frozenRule(
    "S3", 3, "15 seed 平均の期待回収率が 120% 超(単勝オッズの制限なし)",
    { uses_ensemble: true, ev_gt: 1.2, odds_band: null, gap_days: null, posthoc: false,
      control: false },
    frozen(1.064064, 0.9828, 1.151261, 0.059297, 22384, 1315),
    frozen(1.112789, 0.932525, 1.309569, 0.100795, 4848, 273),
    [422, 413, 301],
    [selected(22384, 1.381646, 1.064064), selected(4848, 1.309934, 1.112789)],
    [noise(0.1, 1.033016, 28914, 0.62), noise(0.2, 0.956798, 50134, 0.316),
      noise(0.3, 0.907118, 81229, 0.18)],
    { backtest: 2, price_noise: 2 },
  ),
  S4: frozenRule(
    "S4", 4, "15 seed 平均の期待回収率が 110% 超、単勝 20 倍以上 40 倍未満、前走から 14〜112 日",
    { uses_ensemble: true, ev_gt: 1.1, ...BAND_RULE, posthoc: false, control: false },
    frozen(1.14284, 1.029091, 1.258316, 0.00275, 8987, 371),
    frozen(1.034672, 0.832284, 1.250163, 0.361282, 2515, 93),
    [226, 240, 224],
    [selected(8987, 1.296997, 1.14284), selected(2515, 1.229117, 1.034672)],
    [noise(0.1, 1.099928, 10611, 0.615), noise(0.2, 1.00851, 14899, 0.349),
      noise(0.3, 0.956266, 19986, 0.213)],
    { backtest: 2, price_noise: 2 },
  ),
  S5: frozenRule(
    "S5", 5, "単 seed(137 のモデル)の期待回収率が 120% 超(対照)",
    { uses_ensemble: false, ev_gt: 1.2, odds_band: null, gap_days: null, posthoc: false,
      control: true },
    frozen(1.008277, 0.943703, 1.076621, 0.39458, 30036, 1820),
    frozen(0.995144, 0.863715, 1.137162, 0.523174, 7682, 438),
    [659, 689, 543],
    [selected(30036, 1.401886, 1.008277), selected(7682, 1.334943, 0.995144)],
    [noise(0.1, 0.984019, 36553, 0.669), noise(0.2, 0.941295, 56691, 0.381),
      noise(0.3, 0.890056, 85718, 0.233)],
    { backtest: 1, price_noise: 1 },
  ),
};

export const ATTENTION_RULE_IDS: RuleId[] = ["S1", "S2", "S3", "S4", "S5"];

/** Prospective tally at zero picks (pre-launch: no start date, 研究中, next checkpoint 300). */
export function prospectiveFixture(
  overrides: Partial<AttentionProspective> = {},
): AttentionProspective {
  return {
    start_date: null,
    policy_version: "v1",
    stage: "researching",
    checkpoint: null,
    checkpoint_pending: false,
    decisions: [],
    next_checkpoint: 300,
    remaining_to_next: 300,
    n_counted: 0,
    n_hits: 0,
    n_picks_total: 0,
    frozen: { valuation_basis: "frozen_pick_odds", roi: null, ci: null, p_one_sided: null },
    stored: {
      valuation_basis: "stored_odds_mutable", roi: null, ci: null, n: 0, n_missing_stored_odds: 0,
    },
    bootstrap: {
      impl: "horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1",
      b: 20000,
      seed: 20260905,
      block: "race_day",
      block_universe: "race-days with >=1 counted pick of the rule",
      rng: "numpy.default_rng(PCG64)",
      numpy_version: "2.5.0",
    },
    counts: {
      voided_scratched: 0,
      before_start: 0,
      post_time_unknown: 0,
      computed_after_post: 0,
      result_known_at_compute: 0,
      observed_after_post: 0,
      pending_result: 0,
      unsettled_horse: 0,
      dead_heat: 0,
    },
    flags: { field_changed_after_pick: 0 },
    by_judged_freshness: {
      "<=10m": { n: 0, hits: 0, roi_frozen: null },
      "<=60m": { n: 0, hits: 0, roi_frozen: null },
      ">60m": { n: 0, hits: 0, roi_frozen: null },
    },
    odds_drift: { n: 0, median_log_ratio: null, p10: null, p90: null },
    ...overrides,
  };
}

/** A recorded checkpoint decision (default: a 300-point `continue` whose CI straddles 100%). */
export function checkpointDecisionFixture(
  overrides: Partial<CheckpointDecision> = {},
): CheckpointDecision {
  return {
    checkpoint: 300,
    decision: "continue",
    n_counted: 300,
    n_hits: 12,
    roi_frozen: 1.042,
    ci: [0.81, 1.29],
    decided_at: "2027-09-20T03:00:00Z",
    settlement_cutoff: "2027-09-17T03:00:00Z",
    prospective_start_date: "2026-10-03",
    skipped_pending_before_last: 0,
    counted_pick_ids_sha256: "0".repeat(64),
    bootstrap: {
      impl: "horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1",
      b: 20000,
      seed: 20260905,
      block_universe: "race-days with >=1 counted pick of the rule",
    },
    ...overrides,
  };
}

export type RuleSummaryOverrides = Partial<FrozenRule> & {
  prospective?: Partial<AttentionProspective>;
};

export function ruleSummaryFixture(id: RuleId, overrides: RuleSummaryOverrides = {}): RuleSummary {
  const { prospective, ...rest } = overrides;
  return { ...FROZEN_ATTENTION_RULES[id], ...rest, prospective: prospectiveFixture(prospective) };
}

/** `/attention-rules`: all 5 rules in rank order. Per-rule overrides, e.g.
 *  `attentionRulesFixture({ S4: { prospective: { stage: "failed", checkpoint: 300, decisions: [
 *  checkpointDecisionFixture({ decision: "failed", ci: [0.86, 0.98] })] } } })`. */
export function attentionRulesFixture(
  overrides: Partial<Record<RuleId, RuleSummaryOverrides>> = {},
): AttentionRulesResponse {
  return {
    rule_set_version: "attention-S1-S5-v1",
    items: ATTENTION_RULE_IDS.map((id) => ruleSummaryFixture(id, overrides[id])),
    disclaimer: ATTENTION_RULES_DISCLAIMER,
  };
}

export function evSnapshotFixture(overrides: Partial<EvSnapshot> = {}): EvSnapshot {
  return {
    ens_expected_return: 1.31,
    single_expected_return: 1.284,
    odds: 32.5,
    odds_observed_at: "2008-06-01T00:10:00Z",
    computed_at: "2008-06-01T00:12:00Z",
    run_id: "11111111-2222-3333-4444-555555555555",
    is_pseudo: true,
    ...overrides,
  };
}

/** `stages` as the API returns it: one entry per APPLICABLE rule (all 研究中 unless given). */
export function stagesFor(
  applicable: RuleId[],
  given: Partial<Record<RuleId, StageDetail>> = {},
): Record<string, StageDetail> {
  return Object.fromEntries(applicable.map((id) => [id, given[id] ?? stageDetail()]));
}

/** `pick_status` as the API returns it: every rule, "pick" for the applicable ones. */
export function pickStatusFor(applicable: RuleId[]): AttentionHorse["pick_status"] {
  const status: AttentionHorse["pick_status"] = {};
  for (const id of ATTENTION_RULE_IDS) status[id] = applicable.includes(id) ? "pick" : "none";
  return status;
}

/** One horse of `/races/{id}/attention`. Default: an S1 chip horse (S1, S3, S4 + control S5),
 *  研究中, current values still match; levels = S1's frozen axes at zero prospective picks.
 *  `stages`/`pick_status` follow the final `applicable` unless overridden. */
export function attentionHorseFixture(overrides: Partial<AttentionHorse> = {}): AttentionHorse {
  const applicable = overrides.applicable ?? ["S1", "S3", "S4", "S5"];
  return {
    horse_id: "h1",
    horse_number: 1,
    applicable,
    chip_rule: "S1",
    chip_stage: stageDetail(),
    chip_s2: false,
    chip_now: "matches",
    judged: evSnapshotFixture({ ens_expected_return: 1.25, single_expected_return: 1.28 }),
    current: evSnapshotFixture({
      ens_expected_return: 1.246, single_expected_return: 1.26, odds: 30.8,
      odds_observed_at: "2008-06-01T05:58:00Z", computed_at: "2008-06-01T06:00:00Z",
      run_id: "66666666-7777-8888-9999-000000000000",
    }),
    field_changed_after_pick: false,
    levels: { backtest: 3, prospective: 1, price_noise: 2 },
    stages: stagesFor(applicable),
    pick_status: pickStatusFor(applicable),
    ...overrides,
  };
}

/** A horse with no applicable rule (no chip, nothing judged). */
export function attentionNoChipHorseFixture(
  overrides: Partial<AttentionHorse> = {},
): AttentionHorse {
  return attentionHorseFixture({
    horse_id: "h2",
    horse_number: 2,
    applicable: [],
    chip_rule: null,
    chip_stage: null,
    chip_s2: false,
    chip_now: null,
    judged: null,
    current: evSnapshotFixture({
      ens_expected_return: 0.86, single_expected_return: 0.9, odds: 5.4,
      odds_observed_at: "2008-06-01T05:58:00Z", computed_at: "2008-06-01T06:00:00Z",
      run_id: "66666666-7777-8888-9999-000000000000",
    }),
    levels: null,
    ...overrides,
  });
}

/** `/races/{id}/attention` available (race of `raceDetail`): h1 = S1 chip, h2 = nothing. */
export function attentionAvailableFixture(
  overrides: Partial<AttentionAvailable> = {},
): AttentionAvailable {
  return {
    status: "available",
    race_id: "200806010111",
    post_time: "2008-06-01T06:40:00Z",
    has_results: true,
    judged_at: "2008-06-01T00:12:00Z",
    selection_policy_version: "v1",
    rule_set_version: "attention-S1-S5-v1",
    horses: [attentionHorseFixture(), attentionNoChipHorseFixture()],
    ...overrides,
  };
}

/** One row of `/attention/day` (default: the S1 chip horse above). */
export function attentionDayItemFixture(
  overrides: Partial<AttentionDayItem> = {},
): AttentionDayItem {
  const ruleIds: RuleId[] = ["S1", "S3", "S4", "S5"];
  return {
    race_id: "200806010111",
    post_time: "2008-06-01T06:40:00Z",
    has_results: true,
    venue_code: "05",
    race_number: 11,
    horse_id: "h1",
    horse_number: 1,
    horse_name: "テストホース",
    chip_rule: "S1",
    chip_stage: stageDetail(),
    chip_s2: false,
    chip_now: "matches",
    stages: stagesFor(ruleIds),
    levels: { backtest: 3, prospective: 1, price_noise: 2 },
    current_odds_observed_at: "2008-06-01T05:58:00Z",
    ...overrides,
  };
}

export function attentionDayFixture(
  date = "2008-06-01",
  items: AttentionDayItem[] = [],
): AttentionDayResponse {
  return { date, items };
}

/** Default happy-path handlers; tests override individually with server.use(). */
export const happyHandlers = [
  http.get(`${BASE}/shadow-log`, () => HttpResponse.json(shadowLogResponse)),
  http.get(`${BASE}/races`, () => HttpResponse.json(racePage)),
  http.get(`${BASE}/races/:id`, () => HttpResponse.json(raceDetail)),
  http.get(`${BASE}/races/:id/predictions`, ({ request }) => {
    const url = new URL(request.url);
    return HttpResponse.json(url.searchParams.has("bet_type") ? jointResponse : predictionResponse);
  }),
  http.get(`${BASE}/races/:id/odds`, () => HttpResponse.json(oddsResponse)),
  http.get(`${BASE}/races/:id/recommendations`, () => HttpResponse.json(recommendationResponse)),
  http.get(`${BASE}/races/:id/market-ev`, () => HttpResponse.json(marketEvNotComputed)),
  // Feature 138: typed empty race state, 5 rules at zero picks, empty day.
  http.get(`${BASE}/races/:id/attention`, ({ params }) =>
    HttpResponse.json({ ...attentionNotComputed, race_id: String(params.id) }),
  ),
  http.get(`${BASE}/attention-rules`, () => HttpResponse.json(attentionRulesFixture())),
  http.get(`${BASE}/attention/day`, ({ request }) =>
    HttpResponse.json(
      attentionDayFixture(new URL(request.url).searchParams.get("date") ?? "", []),
    ),
  ),
  http.get(`${BASE}/models/:mv/calibration`, () => HttpResponse.json(calibrationResponse)),
  http.get(`${BASE}/models/:mv/importance`, () => HttpResponse.json(importanceResponse)),
];

export { http, HttpResponse };
