import { http, HttpResponse } from "msw";

import type {
  CalibrationResponse,
  ImportanceResponse,
  OddsResponse,
  PredictionResponse,
  RaceDetail,
  RacePage,
  RecommendationResponse,
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
      realized_return: 3.2,
      realized_roi: 2.2,
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
      realized_return: 0.0,
      realized_roi: -1.0,
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

/** Default happy-path handlers; tests override individually with server.use(). */
export const happyHandlers = [
  http.get(`${BASE}/races`, () => HttpResponse.json(racePage)),
  http.get(`${BASE}/races/:id`, () => HttpResponse.json(raceDetail)),
  http.get(`${BASE}/races/:id/predictions`, ({ request }) => {
    const url = new URL(request.url);
    return HttpResponse.json(url.searchParams.has("bet_type") ? jointResponse : predictionResponse);
  }),
  http.get(`${BASE}/races/:id/odds`, () => HttpResponse.json(oddsResponse)),
  http.get(`${BASE}/races/:id/recommendations`, () => HttpResponse.json(recommendationResponse)),
  http.get(`${BASE}/models/:mv/calibration`, () => HttpResponse.json(calibrationResponse)),
  http.get(`${BASE}/models/:mv/importance`, () => HttpResponse.json(importanceResponse)),
];

export { http, HttpResponse };
