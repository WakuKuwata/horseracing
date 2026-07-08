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
// A successful predict enqueues an auto-recommend follow-up (followup_job_id) — we keep polling
// that second job so the buy-ups land on screen without a separate 買い目生成 click.
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

// Follow-up (auto recommend) outcome, appended to 予測完了. A failed/odd follow-up must not make
// the prediction itself look failed — predictions DID land — so it stays a suffix, warn at worst.
function followupSuffix(job: Job | undefined): { tone: string; text: string } {
  switch (job?.status) {
    case "succeeded":
      return { tone: "ok", text: "予測・買い目生成完了" };
    case "skipped":
      return job.reason?.includes("no win odds")
        ? { tone: "warn", text: "予測完了(買い目: オッズ未取得)" }
        : { tone: "ok", text: "予測完了(買い目: 生成済み)" };
    case "failed":
      return { tone: "warn", text: "予測完了(買い目生成は失敗)" };
    default:
      return { tone: "pending", text: "予測完了・買い目生成中…" };
  }
}

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
  const [recsInvalidated, setRecsInvalidated] = useState(false);

  const start = useMutation<JobAccepted, ErrorInfo, void>({
    mutationFn: () => predictRace(raceId),
    onSuccess: (job) => {
      setInvalidated(false);
      setRecsInvalidated(false);
      setJobId(job.job_id);
    },
  });

  const poll = useQuery<Job, ErrorInfo>({
    queryKey: ["opsJob", jobId],
    queryFn: () => getJob(jobId as string),
    enabled: jobId != null,
    refetchInterval: (q) => (isTerminal(q.state.data?.status) ? false : pollMs),
    // Predictions take minutes — keep polling while the tab is hidden (default pauses the
    // interval, leaving an eternal 予測中… for anyone who switched away and came back).
    refetchIntervalInBackground: true,
  });

  const status = poll.data?.status;
  // Second stage: the auto-recommend job the predict enqueued on success (null for old jobs).
  const followupId = status === "succeeded" ? (poll.data?.followup_job_id ?? null) : null;

  const followupPoll = useQuery<Job, ErrorInfo>({
    queryKey: ["opsJob", followupId],
    queryFn: () => getJob(followupId as string),
    enabled: followupId != null,
    refetchInterval: (q) => (isTerminal(q.state.data?.status) ? false : pollMs),
    refetchIntervalInBackground: true,
  });
  const followupStatus = followupPoll.data?.status;

  // On a terminal success, refetch the 014 predictions (prefix-matches ["predictions", raceId, …]).
  useEffect(() => {
    if (!invalidated && status === "succeeded") {
      void qc.invalidateQueries({ queryKey: ["predictions", raceId] });
      setInvalidated(true);
    }
  }, [status, invalidated, qc, raceId]);

  // When the auto-recommend lands, refetch the recommendations panel too.
  useEffect(() => {
    if (!recsInvalidated && followupStatus === "succeeded") {
      void qc.invalidateQueries({ queryKey: ["recommendations", raceId] });
      setRecsInvalidated(true);
    }
  }, [followupStatus, recsInvalidated, qc, raceId]);

  const running = start.isPending || (jobId != null && !isTerminal(status));

  let tone = "";
  let text = "";
  if (start.isError) {
    tone = "error";
    text = `予測失敗: ${start.error?.detail ?? ""}`;
  } else if (running && poll.isError) {
    // Surface a failing status poll instead of a silent 予測中… forever (regression guard: an ops
    // 500 on the job endpoint once left this button stuck pending after the job had finished).
    tone = "error";
    text = `状態確認エラー(再試行中): ${poll.error?.detail ?? ""}`;
  } else if (running) {
    tone = "pending";
    text = status ? LABEL[status] : "受付済み…";
  } else if (status === "succeeded" && followupId != null) {
    ({ tone, text } = followupSuffix(followupPoll.data));
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
