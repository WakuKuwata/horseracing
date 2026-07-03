import { useState } from "react";
import { Link } from "react-router-dom";

import { postRefreshRange } from "../api/opsClient";

type Phase = "idle" | "confirming" | "pending" | "done" | "error";

/**
 * Feature 053: enqueue a predict+recommend range refresh (ops job → live CLI). A confirm step is
 * REQUIRED before the write (FR-004), the button is disabled while pending, and the result is
 * async (202) — the accepted job_id links to the job history page (052) for status.
 */
export function RefreshRangeButton({
  dateFrom,
  dateTo,
  label,
}: {
  dateFrom: string;
  dateTo: string;
  label: string;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setPhase("pending");
    const { job, error: err } = await postRefreshRange(dateFrom, dateTo);
    if (err) {
      setError(`${err.code}: ${err.detail}`);
      setPhase("error");
      return;
    }
    setJobId(job?.job_id ?? null);
    setPhase("done");
  }

  if (phase === "done") {
    return (
      <span className="refresh-result" data-state="accepted">
        投入しました → <Link to="/jobs">ジョブ履歴</Link>
        {jobId ? <span className="refresh-jobid"> ({jobId.slice(0, 8)})</span> : null}
      </span>
    );
  }
  if (phase === "error") {
    return (
      <span className="refresh-result" data-state="error" data-code role="alert">
        失敗: {error}
      </span>
    );
  }
  if (phase === "confirming") {
    return (
      <span className="refresh-confirm">
        {dateFrom}〜{dateTo} を更新?（予測→推奨を再生成)
        <button type="button" onClick={run}>実行</button>
        <button type="button" onClick={() => setPhase("idle")}>取消</button>
      </span>
    );
  }
  return (
    <button type="button" className="refresh-btn"
            disabled={phase === "pending"} onClick={() => setPhase("confirming")}>
      {phase === "pending" ? "投入中…" : label}
    </button>
  );
}
