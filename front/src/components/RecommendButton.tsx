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

// A skipped job carries the betting CLI's reason — distinguish the benign 生成済み from the two
// actionable prerequisites instead of one opaque 対象なし. Matched on stable CLI substrings
// (cli.py recommend-serve); unknown reasons fall back to the generic label.
function skipLabel(reason: string | null | undefined): { tone: string; text: string } {
  if (reason?.includes("already exist")) {
    return { tone: "ok", text: "生成済み(既存の買い目を表示中)" };
  }
  if (reason?.includes("no prediction_run")) {
    return { tone: "warn", text: "予測未生成 — 先に「予測する」を実行してください" };
  }
  if (reason?.includes("no win odds")) {
    return { tone: "warn", text: "オッズ未取得 — 先に「データ更新」を実行してください" };
  }
  return { tone: TONE.skipped, text: LABEL.skipped };
}

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
    // Keep polling while the tab is hidden (default pauses the interval — see RefreshButton).
    refetchIntervalInBackground: true,
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
  } else if (running && poll.isError) {
    // Surface a failing status poll instead of a silent 生成中… forever (same blindspot as the
    // refresh/predict buttons — the job can finish while the poll endpoint errors).
    tone = "error";
    text = `状態確認エラー(再試行中): ${poll.error?.detail ?? ""}`;
  } else if (running) {
    tone = "pending";
    text = status ? LABEL[status] : "受付済み…";
  } else if (status === "skipped") {
    ({ tone, text } = skipLabel(poll.data?.reason));
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
