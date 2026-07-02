import { useEffect, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ErrorInfo } from "../api/client";
import {
  getJob,
  isTerminal,
  recommendRace,
  type Job,
  type JobAccepted,
  type JobStatus,
} from "../api/opsClient";

// Feature 043: generate the buy-recommendation set (Kelly EV+stake) for THIS race on demand.
// Write goes through the ops path (024) — never the read-only 014 API. On terminal success we
// invalidate the recommendations query so the 014-backed panel refetches. Separate from the 028
// predict button: recommendations need a prediction_run + odds first (skipped otherwise).
const LABEL: Record<JobStatus, string> = {
  queued: "受付済み…",
  running: "買い目生成中…",
  succeeded: "生成完了",
  partial: "一部完了",
  failed: "生成失敗",
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

export function RecommendButton({
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
    mutationFn: () => recommendRace(raceId),
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

  // On terminal success, refetch the 014 recommendations (prefix-matches ["recommendations", …]).
  useEffect(() => {
    if (!invalidated && status === "succeeded") {
      void qc.invalidateQueries({ queryKey: ["recommendations", raceId] });
      setInvalidated(true);
    }
  }, [status, invalidated, qc, raceId]);

  const running = start.isPending || (jobId != null && !isTerminal(status));

  let tone = "";
  let text = "";
  if (start.isError) {
    tone = "error";
    text = `生成失敗: ${start.error?.detail ?? ""}`;
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
        {running ? "生成中…" : "買い目生成"}
      </button>
      {text && (
        <span className={`predict__status predict__status--${tone}`} role="status">
          {text}
        </span>
      )}
    </span>
  );
}
