import { useEffect, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ErrorInfo } from "../api/client";
import {
  getJob,
  isTerminal,
  refreshRace,
  type Job,
  type JobAccepted,
  type JobStatus,
} from "../api/opsClient";

// One label per state (FR-008): 受付/取得中/成功/一部成功/失敗/対象なし(skipped). The display path
// stays on the read-only 014 data — on success we invalidate the race query so it refetches; we
// never render pseudo values here, so the existing PseudoBadge path is untouched (FR-022).
const LABEL: Record<JobStatus, string> = {
  queued: "受付済み…",
  running: "取得中…",
  succeeded: "更新完了",
  partial: "一部更新",
  failed: "更新失敗",
  skipped: "対象なし",
};

const TONE: Record<JobStatus, string> = {
  queued: "pending",
  running: "pending",
  succeeded: "ok",
  partial: "warn",
  failed: "error",
  skipped: "muted",
};

export function RefreshButton({
  raceId,
  pollMs = 1500,
}: {
  raceId: string;
  pollMs?: number;
}) {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [invalidated, setInvalidated] = useState(false);

  const start = useMutation<JobAccepted, ErrorInfo, void>({
    mutationFn: () => refreshRace(raceId),
    onSuccess: (job) => {
      setInvalidated(false);
      setJobId(job.job_id);
    },
  });

  const poll = useQuery<Job, ErrorInfo>({
    queryKey: ["opsJob", jobId],
    queryFn: () => getJob(jobId as string),
    enabled: jobId != null,
    refetchInterval: (q) => (isTerminal(q.state.data?.status) ? false : pollMs),
  });

  const status = poll.data?.status;

  // On a terminal success/partial, refetch the 014 race detail so the table shows fresh data.
  useEffect(() => {
    if (!invalidated && (status === "succeeded" || status === "partial")) {
      void qc.invalidateQueries({ queryKey: ["race", raceId] });
      setInvalidated(true);
    }
  }, [status, invalidated, qc, raceId]);

  const running = start.isPending || (jobId != null && !isTerminal(status));

  // What to show next to the button.
  let tone = "";
  let text = "";
  if (start.isError) {
    tone = "error";
    text = `更新失敗: ${start.error?.detail ?? ""}`;
  } else if (running) {
    tone = "pending";
    text = status ? LABEL[status] : "受付済み…";
  } else if (status) {
    tone = TONE[status];
    // US3: a fresh recent success is reused rather than re-fetched — say so (neutral, no profit lang).
    text =
      start.data?.reused && status === "succeeded" ? "最新を再利用" : LABEL[status];
  }

  return (
    <span className="refresh">
      <button
        type="button"
        className="refresh__btn"
        onClick={() => start.mutate()}
        disabled={running}
      >
        {running ? "更新中…" : "データ更新"}
      </button>
      {text && (
        <span className={`refresh__status refresh__status--${tone}`} role="status">
          {text}
        </span>
      )}
    </span>
  );
}
