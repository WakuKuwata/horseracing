import { http, HttpResponse } from "msw";

import type {
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
  horses: [
    { horse_id: "h1", horse_number: 1, entry_status: "active", age: 4, sex: "牡" },
    { horse_id: "h2", horse_number: 2, entry_status: "active", age: 5, sex: "牝" },
  ],
};

export const predictionResponse: PredictionResponse = {
  race_id: "200806010111",
  horses: [
    { horse_id: "h1", horse_number: 1, win: 0.32, top2: 0.55, top3: 0.7 },
    { horse_id: "h2", horse_number: 2, win: 0.18, top2: 0.4, top3: 0.58 },
  ],
  joint: null,
  joint_bet_type: null,
  joint_logic_version: null,
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
      bet_type: "quinella",
      selection: [1, 2],
      market_odds_used: null,
      estimated_market_odds_used: 12.3,
      is_estimated_odds: true,
      pseudo_odds: 4.5,
      pseudo_roi: 0.18,
      double_pseudo: true,
      logic_version: "011.1",
      computed_at: "2008-05-31T22:30:00Z",
      prediction_run_id: "run-abc",
    },
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
];

export { http, HttpResponse };
