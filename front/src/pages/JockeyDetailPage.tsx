import { Link, useParams } from "react-router-dom";

import { useJockeyHistory, useJockeyProfile } from "../api/queries";
import { QueryStateView } from "../components/StateView";
import { formatNum, formatPct, PLACEHOLDER } from "../lib/format";
import { venueName } from "../lib/venues";

// Feature 029: jockey profile — identity + FACTUAL riding aggregates + recent mounts (not features).
export function JockeyDetailPage() {
  const { jockeyId = "" } = useParams();
  const profile = useJockeyProfile(jockeyId);
  const history = useJockeyHistory(jockeyId, { page_size: 50 });

  return (
    <section>
      <p>
        <Link to="/">← レース一覧</Link>
      </p>

      <div className="panel">
        <QueryStateView
          isLoading={profile.isLoading}
          error={profile.error ?? null}
          data={profile.data}
          loadingLabel="騎手情報を読み込み中…"
        >
          {(j) => (
            <>
              <h2 className="race-title">{j.jockey_name ?? j.jockey_id}</h2>
              <h3 className="section-label">騎乗成績（確定実績）</h3>
              <div className="stat-grid">
                <div className="stat"><span className="stat__k">騎乗</span><span className="stat__v">{j.mounts}</span></div>
                <div className="stat"><span className="stat__k">勝利</span><span className="stat__v">{j.wins}</span></div>
                <div className="stat"><span className="stat__k">勝率</span><span className="stat__v">{formatPct(j.win_rate)}</span></div>
                <div className="stat"><span className="stat__k">連対率</span><span className="stat__v">{formatPct(j.quinella_rate)}</span></div>
                <div className="stat"><span className="stat__k">複勝率</span><span className="stat__v">{formatPct(j.show_rate)}</span></div>
                <div className="stat"><span className="stat__k">平均着順</span><span className="stat__v">{formatNum(j.avg_finish)}</span></div>
              </div>
            </>
          )}
        </QueryStateView>
      </div>

      <div className="panel">
        <h2>騎乗履歴</h2>
        <QueryStateView
          isLoading={history.isLoading}
          error={history.error ?? null}
          data={history.data}
          isEmpty={(d) => d.items.length === 0}
          loadingLabel="履歴を読み込み中…"
          emptyMessage="騎乗履歴がありません"
        >
          {(d) => (
            <table className="data-table">
              <thead>
                <tr><th>日付</th><th>開催</th><th>レース</th><th>騎乗馬</th><th>着順</th></tr>
              </thead>
              <tbody>
                {d.items.map((row) => (
                  <tr key={row.race_id}>
                    <td>{row.race_date ?? PLACEHOLDER}</td>
                    <td>{venueName(row.venue_code)} {row.race_number ?? ""}R</td>
                    <td>
                      <Link to={`/races/${row.race_id}`}>
                        {(row.race_name ?? "").replace(/\*+$/, "") || PLACEHOLDER}
                      </Link>
                    </td>
                    <td>
                      {row.horse_id ? (
                        <Link to={`/horses/${row.horse_id}`}>{row.horse_name ?? row.horse_id}</Link>
                      ) : (
                        (row.horse_name ?? PLACEHOLDER)
                      )}
                    </td>
                    <td>{row.finish_order ?? PLACEHOLDER}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryStateView>
      </div>
    </section>
  );
}
