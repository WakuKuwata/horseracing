import type { JobRow } from "../api/types";

type Capture = NonNullable<JobRow["capture"]>;
export type CaptureTone = "neutral" | "muted" | "attention";

const STALE_CAPTURE_MS = 18_000;

export const CAPTURE_ELIGIBILITY_REASONS = [
  "already_captured",
  "outside_primary_horizon",
  "result_settled",
  "post_time_unknown",
  "post_time_elapsed",
  "ok",
  "min_seconds_to_post",
  "concurrent_capture",
  "no_started_horses",
  "field_too_small",
  "auto_capture_disabled",
] as const;

export const CAPTURE_ATTENTION_REASONS = [
  "field_changed_during_fetch",
  "robots_disallowed",
  "fetch_failed",
  "result_settled_during_fetch",
  "throttle_backlog",
  "deadline_exceeded",
  "post_time_elapsed_during_fetch",
  "min_seconds_to_post_during_fetch",
  "source_cooldown",
  "outer_timeout",
  "launch_failed",
] as const;

const ELIGIBILITY_REASON_SET = new Set<string>(CAPTURE_ELIGIBILITY_REASONS);

const CAPTURE_REASON_LABEL: Record<string, string> = {
  already_captured: "捕捉済み",
  field_changed_during_fetch: "取得中に出走構成が変更",
  robots_disallowed: "robots.txt により取得不可",
  fetch_failed: "取得元へのアクセス失敗",
  result_settled_during_fetch: "取得中に結果確定",
  throttle_backlog: "取得待ちが上限超過",
  outside_primary_horizon: "主捕捉時間帯の対象外",
  deadline_exceeded: "捕捉期限超過",
  result_settled: "結果確定済み",
  post_time_unknown: "発走時刻不明",
  post_time_elapsed: "発走済み",
  ok: "正常",
  min_seconds_to_post: "発走直前",
  post_time_elapsed_during_fetch: "取得中に発走",
  min_seconds_to_post_during_fetch: "取得中に運用下限到達",
  concurrent_capture: "別プロセスが捕捉中",
  source_cooldown: "取得元クールダウン中",
  artifact_unavailable: "表示基準を利用不可",
  entries_incomplete: "出走表が未完成",
  invalid_race_id: "レース ID が不正",
  race_not_found: "レースが見つからない",
  race_date_unknown: "開催日不明",
  invalid_post_time: "発走時刻が不正",
  no_started_horses: "出走馬なし",
  field_too_small: "出走頭数が4頭未満",
  invalid_capture_time: "捕捉時刻が不正",
  source_unavailable: "取得元不明",
  partial_market_odds: "市場オッズが不完全",
  invalid_popularity_ranks: "人気順が不正",
  auto_capture_disabled: "自動捕捉停止中",
  outer_timeout: "外側の待機期限超過",
  launch_failed: "捕捉プロセスの起動失敗",
};

function reasonLabel(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return CAPTURE_REASON_LABEL[reason] ?? reason;
}

function withReason(label: string, reason: string | null): string {
  return reason ? `${label}（${reason}）` : label;
}

function staleInFlight(startedAt: string | null | undefined, nowMs: number): boolean {
  if (!startedAt) return false;
  const timestamp = Date.parse(startedAt);
  return Number.isFinite(timestamp) && nowMs - timestamp > STALE_CAPTURE_MS;
}

export function capturePresentation(
  capture: Capture,
  startedAt: string | null | undefined,
  nowMs = Date.now(),
): { label: string; tone: CaptureTone } {
  const reason = reasonLabel(capture.reason);
  if (capture.state === "started" || capture.state === "launched") {
    if (capture.state === "launched" && staleInFlight(startedAt, nowMs)) {
      return { label: withReason("捕捉不明", reason), tone: "attention" };
    }
    return { label: "捕捉中", tone: "neutral" };
  }
  if (capture.outcome === "captured") {
    return { label: "荒れ度を凍結", tone: "neutral" };
  }
  if (capture.outcome === "skipped" && capture.reason && ELIGIBILITY_REASON_SET.has(capture.reason)) {
    return { label: withReason("捕捉見送り", reason), tone: "muted" };
  }
  return { label: withReason("捕捉できず", reason), tone: "attention" };
}
