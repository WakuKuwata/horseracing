import { useEffect, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ErrorInfo } from "../api/client";
import {
  getBatch,
  isBatchDone,
  refreshDay,
  type Batch,
  type BatchAccepted,
} from "../api/opsClient";

// US2: refresh every race on the selected day (ops write service). The display stays on 014 — on a
// batch with any progress we invalidate the races query so the list (and its 結果確定 badges)
// refetch. "失敗を再実行" re-enqueues the day; fresh successes are reused, only failed races re-run.
//
// The status line reports every terminal bucket, not just succeeded/failed. On a race day most
// children legitimately end PARTIAL (a race that has not run yet has no result table), and those
// used to appear nowhere — 「完了 12/36 成功・0 失敗」 with 24 races unaccounted for reads exactly
// like "it silently did nothing". `total` covers the whole day (a race whose job was reused is
// still one of the day's races), so `enqueued` is what distinguishes "already up to date" from
// "your click did nothing" and gates the forced-refresh escape hatch.
export function DayRefreshButton({
  date,
  pollMs = 2000,
}: {
  date: string;
  pollMs?: number;
}) {
  const qc = useQueryClient();
  const [traceId, setTraceId] = useState<string | null>(null);
  const [invalidated, setInvalidated] = useState(false);

  const start = useMutation<BatchAccepted, ErrorInfo, boolean | void>({
    mutationFn: (force) => refreshDay(date, force === true),
    onSuccess: (b) => {
      setInvalidated(false);
      setTraceId(b.trace_id);
    },
  });

  const poll = useQuery<Batch, ErrorInfo>({
    queryKey: ["opsBatch", traceId],
    queryFn: () => getBatch(traceId as string),
    enabled: traceId != null,
    refetchInterval: (q) => (isBatchDone(q.state.data?.status) ? false : pollMs),
    // Keep polling while the tab is hidden (default pauses the interval — see RefreshButton).
    refetchIntervalInBackground: true,
  });

  const b = poll.data;
  const done = isBatchDone(b?.status);
  const succeeded = b?.succeeded ?? 0;
  const partial = b?.partial ?? 0;
  const failed = b?.failed ?? 0;
  const skipped = b?.skipped ?? 0;
  const total = b?.total ?? 0;
  const enqueued = b?.enqueued ?? null;
  // races of this day that the click did NOT re-fetch because their job was reused. `total` now
  // reports the whole day (a reused race is still one of its races), so the only thing that can
  // say whether the click did any work is how many it actually enqueued.
  const reused = enqueued != null ? Math.max(total - enqueued, 0) : 0;
  const refetchedNothing = done && total > 0 && enqueued === 0;
  // the parent itself failed (e.g. discovery never ran) — there are no children to explain it
  const parentFailed = b?.status === "failed" && total === 0;

  useEffect(() => {
    if (!invalidated && done && b && succeeded + partial > 0) {
      void qc.invalidateQueries({ queryKey: ["races"] });
      setInvalidated(true);
    }
  }, [done, b, invalidated, qc, succeeded, partial]);

  const running = start.isPending || (traceId != null && !done);
  // the day itself has no races (a non-race day) — distinct from "had races, re-fetched none"
  const nothingRun = done && total === 0;
  const tone = !done
    ? "pending"
    : parentFailed
      ? "error"
      : failed || partial || nothingRun || refetchedNothing
        ? "warn"
        : "ok";

  function statusText(): string {
    if (parentFailed) return "更新失敗: 対象レースを取得できませんでした";
    if (!done) {
      const settled = succeeded + partial + failed + skipped;
      return `取得中 ${settled}/${total}`;
    }
    if (nothingRun) return "更新対象なし: このレース日には対象レースがありません";
    if (refetchedNothing) {
      return `${total} レースはいずれも直近に取得済みのため、再取得していません`;
    }
    const parts = [`完了 ${succeeded}/${total} 成功`];
    if (partial) parts.push(`${partial} 一部`);
    if (failed) parts.push(`${failed} 失敗`);
    if (skipped) parts.push(`${skipped} 対象なし`);
    let text = parts.join("・");
    if (reused) text += `(うち ${reused} レースは直近取得済みのため再取得なし)`;
    return text;
  }

  return (
    <span className="refresh">
      <button
        type="button"
        className="refresh__btn"
        onClick={() => start.mutate(false)}
        disabled={running || !date}
      >
        {running ? "更新中…" : "この日を更新"}
      </button>

      {start.isError && (
        <span className="refresh__status refresh__status--error" role="status">
          更新失敗: {start.error?.detail ?? ""}
        </span>
      )}

      {/* A failing batch poll must not look like silent progress (same blindspot as RefreshButton). */}
      {!start.isError && running && poll.isError && (
        <span className="refresh__status refresh__status--error" role="status">
          状態確認エラー(再試行中): {poll.error?.detail ?? ""}
        </span>
      )}

      {b && !(running && poll.isError) && (
        <span
          className={`refresh__status refresh__status--${tone}`}
          role="status"
        >
          {statusText()}
        </span>
      )}

      {done && failed > 0 && (
        <button
          type="button"
          className="refresh__btn"
          onClick={() => start.mutate(false)}
        >
          失敗を再実行
        </button>
      )}

      {/* Reuse is a freshness optimisation, not a verdict about the source. Before a race the odds
          move continuously, so the operator needs a way out of the window — the endpoint has always
          accepted `force`, it was simply never sent (nor honoured) on this path. */}
      {done && reused > 0 && (
        <button
          type="button"
          className="refresh__btn"
          onClick={() => start.mutate(true)}
        >
          強制的に再取得
        </button>
      )}
    </span>
  );
}
