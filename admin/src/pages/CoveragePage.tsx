import { useState } from "react";

import { useCoverage } from "../api/queries";
import { RefreshRangeButton } from "../components/RefreshRangeButton";
import { QueryStateView } from "../components/StateView";
import { formatInt, textOr } from "../lib/format";

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

/**
 * Feature 052 US1: per-day product coverage. A day where n_predicted_active < n_races is a HOLE
 * (highlighted) — the operational signal to run `live refresh`. Counts use the ACTIVE model only
 * (044 semantics); no active model → predicted 0 with the model shown as 未設定 (honest).
 */
export function CoveragePage() {
  const [from, setFrom] = useState(() => isoDaysAgo(30));
  const [to, setTo] = useState(() => isoDaysAgo(0));
  const query = useCoverage(from, to);

  return (
    <div className="panel">
      <h1>データ被覆率</h1>
      <p className="note">
        日ごとの「レース数に対する オッズ/結果/予測(運用中モデル)/推奨 の充足」。
        予測がレース数に満たない日は<mark>ハイライト</mark>= backfill の穴。
      </p>
      <div className="toolbar">
        <label>
          from <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          to <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        {/* Feature 053: enqueue a predict+recommend backfill for the whole visible range. */}
        <RefreshRangeButton dateFrom={from} dateTo={to} label="この範囲を更新" />
      </div>
      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={(d) => d.days.length === 0}
        loadingLabel="被覆データを読み込み中…"
        emptyMessage="この期間に開催日がありません"
      >
        {(data) => (
          <>
            <p className="note">
              運用中モデル: <strong>{textOr(data.active_model_version) === "—"
                ? "未設定(予測被覆は 0 表示)" : data.active_model_version}</strong>
            </p>
            <table>
              <thead>
                <tr>
                  <th>開催日</th>
                  <th className="num">レース</th>
                  <th className="num">オッズ</th>
                  <th className="num">結果</th>
                  <th className="num">予測(運用中)</th>
                  <th className="num">推奨</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.days.map((d) => {
                  const hole = d.n_predicted_active < d.n_races;
                  return (
                    <tr key={d.date} data-hole={hole}>
                      <td>{d.date}</td>
                      <td className="num">{formatInt(d.n_races)}</td>
                      <td className="num">{formatInt(d.n_with_odds)}</td>
                      <td className="num">{formatInt(d.n_with_results)}</td>
                      <td className="num">
                        {formatInt(d.n_predicted_active)}
                        {hole ? <span title="予測がレース数に満たない日(backfill の穴)"> ⚠</span> : null}
                      </td>
                      <td className="num">{formatInt(d.n_with_recommendations)}</td>
                      <td>
                        {/* Feature 053: update just this day (from=to=this date). */}
                        <RefreshRangeButton dateFrom={d.date} dateTo={d.date} label="この日を更新" />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
      </QueryStateView>
    </div>
  );
}
