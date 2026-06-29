import { useEffect, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ErrorInfo } from "../api/client";
import {
  getJob,
  isTerminal,
  predictRace,
  type Job,
  type JobAccepted,
  type JobStatus,
} from "../api/opsClient";

// Feature 028: generate the active model's predictions for THIS race on demand. Write goes through
// the ops path (024) — never the read-only 014 API. On success we invalidate the predictions query
// so the 014-backed prediction section refetches. One label per job state (FR-006).
const LABEL: Record<JobStatus, string> = {
  queued: "受付済み…",
  running: "予測生成中…",
  succeeded: "予測完了",
  partial: "一部完了",
  failed: "予測失敗",
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

export function PredictButton({
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
    mutationFn: () => predictRace(raceId),
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

  // On a terminal success, refetch the 014 predictions (prefix-matches ["predictions", raceId, …]).
  useEffect(() => {
    if (!invalidated && status === "succeeded") {
      void qc.invalidateQueries({ queryKey: ["predictions", raceId] });
      setInvalidated(true);
    }
  }, [status, invalidated, qc, raceId]);

  const running = start.isPending || (jobId != null && !isTerminal(status));

  let tone = "";
  let text = "";
  if (start.isError) {
    tone = "error";
    text = `予測失敗: ${start.error?.detail ?? ""}`;
  } else if (running) {
    tone = "pending";
    text = status ? LABEL[status] : "受付済み…";
  } else if (status) {
    tone = TONE[status];
    text = LABEL[status];
  }

  return (
    <span className="predict">
      <button
        type="button"
        className="predict__btn"
        onClick={() => start.mutate()}
        disabled={running}
      >
        {running ? "予測中…" : "予測する"}
      </button>
      {text && (
        <span className={`predict__status predict__status--${tone}`} role="status">
          {text}
        </span>
      )}
    </span>
  );
}
