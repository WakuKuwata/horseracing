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
  const [evInvalidated, setEvInvalidated] = useState(false);

  const start = useMutation<JobAccepted, ErrorInfo, void>({
    mutationFn: () => refreshRace(raceId),
    onSuccess: (job) => {
      setInvalidated(false);
      setEvInvalidated(false);
      setJobId(job.job_id);
    },
  });

  const poll = useQuery<Job, ErrorInfo>({
    queryKey: ["opsJob", jobId],
    queryFn: () => getJob(jobId as string),
    enabled: jobId != null,
    refetchInterval: (q) => (isTerminal(q.state.data?.status) ? false : pollMs),
    // Keep polling while the tab is hidden — the default pauses the interval, so a user who
    // switches away during a long job came back to an eternal 更新中… instead of 完了.
    refetchIntervalInBackground: true,
  });

  const status = poll.data?.status;

  // On a terminal success/partial, refetch the 014 views that a refresh feeds: the race detail
  // (entries/results), the odds panel, the predictions (market q is derived from odds at read
  // time), the 137 期待回収率 (its odds_changed / field_changed states are read-time too) and the
  // 138 注目条件 (its current values, chip_now and scratched voids are read-time) — so the whole
  // page reflects the new data without a manual reload. The 138 rules list is refetched too: its
  // prospective tallies, stages and checkpoint records are read-time over the ingested results, so
  // a chip that moved to 「300 点不通過」 must not sit above a breakdown still saying 研究中.
  useEffect(() => {
    if (!invalidated && (status === "succeeded" || status === "partial")) {
      void qc.invalidateQueries({ queryKey: ["race", raceId] });
      void qc.invalidateQueries({ queryKey: ["odds", raceId] });
      void qc.invalidateQueries({ queryKey: ["predictions", raceId] });
      void qc.invalidateQueries({ queryKey: ["market-ev", raceId] });
      void qc.invalidateQueries({ queryKey: ["attention", raceId] });
      void qc.invalidateQueries({ queryKey: ["attention-rules"] });
      setInvalidated(true);
    }
  }, [status, invalidated, qc, raceId]);

  // 137: a refresh that wrote new odds for a pending race enqueues the 期待回収率 recompute
  // (ops exposes it as followup_job_id). It runs after the refresh in the CPU lane, so keep
  // polling it and refetch the market-ev view once it lands — otherwise the page would show the
  // pre-refresh values until the next reload. 138: the same job writes the race's first
  // computation (scan + picks) and the latest ens15 row the 注目条件 current values read, so the
  // attention view is refetched together with market-ev.
  const followupId =
    status === "succeeded" || status === "partial" ? (poll.data?.followup_job_id ?? null) : null;
  const followupPoll = useQuery<Job, ErrorInfo>({
    queryKey: ["opsJob", followupId],
    queryFn: () => getJob(followupId as string),
    enabled: followupId != null,
    refetchInterval: (q) => (isTerminal(q.state.data?.status) ? false : pollMs),
    refetchIntervalInBackground: true,
  });
  const followupStatus = followupPoll.data?.status;
  useEffect(() => {
    if (!evInvalidated && isTerminal(followupStatus)) {
      void qc.invalidateQueries({ queryKey: ["market-ev", raceId] });
      void qc.invalidateQueries({ queryKey: ["attention", raceId] });
      void qc.invalidateQueries({ queryKey: ["attention-rules"] });
      setEvInvalidated(true);
    }
  }, [followupStatus, evInvalidated, qc, raceId]);

  const running = start.isPending || (jobId != null && !isTerminal(status));

  // What to show next to the button.
  let tone = "";
  let text = "";
  if (start.isError) {
    tone = "error";
    text = `更新失敗: ${start.error?.detail ?? ""}`;
  } else if (running && poll.isError) {
    // The job may still be running — but say the poll itself is failing instead of sitting on a
    // silent 更新中… forever (this exact blindspot hid an ops 500 on the status endpoint).
    tone = "error";
    text = `状態確認エラー(再試行中): ${poll.error?.detail ?? ""}`;
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
