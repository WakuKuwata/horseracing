import { http, HttpResponse } from "msw";

import type { CalibrationResponse, ImportanceResponse, ModelListResponse } from "../api/types";

export { http, HttpResponse };

const BASE = "*/api/v1";

export const modelListResponse: ModelListResponse = {
  items: [
    {
      model_version: "lgbm-042",
      model_family: "lightgbm",
      feature_version: "features-012",
      label_schema: "win_top2_top3",
      adoption_status: "active",
      created_at: "2026-07-02T06:35:44Z",
      win_log_loss: 0.21706,
      win_auc: 0.7934,
      win_ece: 0.00058,
      win_brier: 0.0592,
      objective: "pl_topk",
      calibration: "isotonic",
      train_through: "2025-10-25",
      n_model_rows: 650129,
      git_sha: "7cd6ba2",
      adopted: true,
      has_calibration: true,
      has_importance: true,
    },
    {
      // an old model with NO recorded metrics — every nullable renders as "—", never NaN
      model_version: "lgbm-old",
      model_family: "lightgbm",
      feature_version: "features-005",
      label_schema: "win_top2_top3",
      adoption_status: "retired",
      created_at: "2025-01-01T00:00:00Z",
      win_log_loss: null,
      win_auc: null,
      win_ece: null,
      win_brier: null,
      objective: null,
      calibration: null,
      train_through: null,
      n_model_rows: null,
      git_sha: null,
      adopted: null,
      has_calibration: false,
      has_importance: false,
    },
  ],
};

export const calibrationResponse: CalibrationResponse = {
  model_version: "lgbm-042",
  oos: true,
  source: "walk_forward_oos",
  label: "win",
  valid_years: [2024, 2025],
  n_total: 1000,
  ece: 0.0006,
  bins: [
    { pred_lo: 0.0, pred_hi: 0.1, pred_mean: 0.05, realized_rate: 0.048,
      realized_ci_low: 0.04, realized_ci_high: 0.06, count: 800, suppressed: false },
  ],
};

export const importanceResponse: ImportanceResponse = {
  model_version: "lgbm-042",
  type: "gain",
  values: [{ feature: "jockey_place_rate", gain: 1234.5 }],
};

export const happyHandlers = [
  http.get(`${BASE}/models`, () => HttpResponse.json(modelListResponse)),
  http.get(`${BASE}/models/:mv/calibration`, () => HttpResponse.json(calibrationResponse)),
  http.get(`${BASE}/models/:mv/importance`, () => HttpResponse.json(importanceResponse)),
];
