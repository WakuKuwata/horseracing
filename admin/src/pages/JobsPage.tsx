import { useState } from "react";

import { useJobs } from "../api/queries";
import { QueryStateView } from "../components/StateView";
import { capturePresentation } from "../lib/captureLabels";
import { formatDateTime, formatInt, textOr } from "../lib/format";

const STATUSES = ["", "queued", "running", "succeeded", "partial", "skipped", "failed"];
// Every job_type the ops worker and the scrape pipeline actually write. The previous list omitted
// the scrape sub-jobs (entries/results/odds/exotic_quotes/horse_profile) — which are exactly the
// rows an operator needs when a refresh reports 一部 — and refresh_range, while listing "laps",
// which this deployment does not produce.
const JOB_TYPES = [
  "",
  "refresh_race",
  "refresh_day",
  "refresh_range",
  "entries",
  "results",
  "odds",
  "exotic_quotes",
  "exotic_odds",
  "horse_profile",
  "predict",
  "recommend",
  "race_laps",
];

/**
 * Feature 052 US2: ingestion_jobs history — the ops service only exposes single-job polling;
 * this is the list/filter view (newest first). Failed rows surface error_message inline.
 */
export function JobsPage() {
  const [status, setStatus] = useState("");
  const [jobType, setJobType] = useState("");
  const query = useJobs({
    ...(status ? { status } : {}),
    ...(jobType ? { job_type: jobType } : {}),
    limit: 100,
  });

  return (
    <div className="panel">
      <h1>ジョブ履歴</h1>
      <p className="note">ingestion_jobs の監査履歴(新しい順・最大 100 件)。</p>
      <div className="toolbar">
        <label>
          状態
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map((s) => <option key={s} value={s}>{s || "すべて"}</option>)}
          </select>
        </label>
        <label>
          種別
          <select value={jobType} onChange={(e) => setJobType(e.target.value)}>
            {JOB_TYPES.map((t) => <option key={t} value={t}>{t || "すべて"}</option>)}
          </select>
        </label>
      </div>
      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={(d) => d.items.length === 0}
        loadingLabel="ジョブ履歴を読み込み中…"
        emptyMessage="該当するジョブがありません"
      >
        {(data) => (
          <table>
            <thead>
              <tr>
                <th>種別</th>
                <th>対象</th>
                <th>状態</th>
                <th>荒れ度捕捉</th>
                <th className="num">retry</th>
                <th className="num">処理/スキップ/エラー行</th>
                <th>開始</th>
                <th>完了</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((j) => {
                const capture = j.capture
                  ? capturePresentation(j.capture, j.started_at)
                  : null;
                return (
                  <tr key={j.ingestion_job_id} data-status={j.status}>
                    <td>{textOr(j.job_type)}</td>
                    <td>{textOr(j.scope_value)}</td>
                    <td>
                      <span className={`badge badge--job-${j.status}`}>{j.status}</span>
                      {j.error_message ? (
                        <div className="job-error">{j.error_message}</div>
                      ) : null}
                    </td>
                    <td>
                      {capture ? (
                        <span
                          className={`badge badge--capture-${capture.tone}`}
                          data-testid={`capture-${j.ingestion_job_id}`}
                          data-capture-tone={capture.tone}
                        >
                          {capture.label}
                        </span>
                      ) : "—"}
                    </td>
                    <td className="num">{formatInt(j.retry_count)}</td>
                    <td className="num">
                      {formatInt(j.processed_rows)}/{formatInt(j.skipped_rows)}/{formatInt(j.error_count)}
                    </td>
                    <td>{formatDateTime(j.started_at)}</td>
                    <td>{formatDateTime(j.completed_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </QueryStateView>
    </div>
  );
}
