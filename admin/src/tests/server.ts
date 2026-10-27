import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

// B5: the write button asks GET /ops/v1/status first. Default to "not paused" so existing tests
// keep their behaviour; a test that wants the pause overrides it with server.use(...).
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

export const server = setupServer(
  http.get("*/ops/v1/status", () => HttpResponse.json(OPS_STATUS_ACTIVE)),
);
