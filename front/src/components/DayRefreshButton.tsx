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
// batch with any success we invalidate the races query so the list (and its 結果確定 badges) refetch.
// "失敗を再実行" re-enqueues the day; fresh successes are reused, only failed races re-run.
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

  const start = useMutation<BatchAccepted, ErrorInfo, void>({
    mutationFn: () => refreshDay(date),
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
  });

  const b = poll.data;
  const done = isBatchDone(b?.status);

  useEffect(() => {
    if (!invalidated && done && b && b.succeeded > 0) {
      void qc.invalidateQueries({ queryKey: ["races"] });
      setInvalidated(true);
    }
  }, [done, b, invalidated, qc]);

  const running = start.isPending || (traceId != null && !done);
  const failed = b?.failed ?? 0;

  return (
    <span className="refresh">
      <button
        type="button"
        className="refresh__btn"
        onClick={() => start.mutate()}
        disabled={running || !date}
      >
        {running ? "更新中…" : "この日を更新"}
      </button>

      {start.isError && (
        <span className="refresh__status refresh__status--error" role="status">
          更新失敗: {start.error?.detail ?? ""}
        </span>
      )}

      {b && (
        <span
          className={`refresh__status refresh__status--${done ? (failed ? "warn" : "ok") : "pending"}`}
          role="status"
        >
          {done
            ? `完了 ${b.succeeded}/${b.total} 成功${failed ? `・${failed} 失敗` : ""}`
            : `取得中 ${(b.succeeded ?? 0) + failed}/${b.total}`}
        </span>
      )}

      {done && failed > 0 && (
        <button
          type="button"
          className="refresh__btn"
          onClick={() => start.mutate()}
        >
          失敗を再実行
        </button>
      )}
    </span>
  );
}
