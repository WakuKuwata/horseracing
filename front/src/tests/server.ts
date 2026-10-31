import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

// B5: every write button now asks GET /ops/v1/status first. Default to "not paused" so the
// existing button tests keep their pre-B5 behaviour; a test that wants the pause overrides it
// with server.use(...). resetHandlers() returns to this default between tests.
export const OPS_STATUS_ACTIVE = {
  collection_paused: false,
  writes_accepted: true,
  collection_mode: null,
  policy_file: { present: false, parse_error: null, schema_version: null, collection_paused: null,
    source_interval_seconds: null, ops_fetch_min_interval_seconds: null,
    shared_limiter_enabled: null },
  policy_mismatch: false,
  ops_fetch_min_interval_seconds: 1,
  detail: "書き込みを受け付けています。",
};

export const OPS_STATUS_PAUSED = {
  ...OPS_STATUS_ACTIVE,
  collection_paused: true,
  writes_accepted: false,
  collection_mode: "paused",
  policy_file: { ...OPS_STATUS_ACTIVE.policy_file, present: true, schema_version: "172-v1",
    collection_paused: true, source_interval_seconds: 60, ops_fetch_min_interval_seconds: 60,
    shared_limiter_enabled: true },
  ops_fetch_min_interval_seconds: 60,
  detail: "データ取得を停止中です。更新・予測・買い目生成・購入記録の受付を止めています。",
};

// 2026-10-31: today's T−3 capture window is open → only the refresh buttons are disabled.
export const OPS_STATUS_CAPTURING = {
  ...OPS_STATUS_ACTIVE,
  capture_window: {
    date: "2026-10-31",
    starts_at: "2026-10-31T00:44:00+00:00",
    ends_at: "2026-10-31T07:25:00+00:00",
    active: true,
    n_races: 23,
    detail: "発走直前の判断記録(T−3 捕捉)を取得中のため、データ更新は 16:25 まで受け付けません。予測・買い目生成・購入記録はそのまま使えます。",
  },
};

export const server = setupServer(
  http.get("*/ops/v1/status", () => HttpResponse.json(OPS_STATUS_ACTIVE)),
);
