import { Link, useParams } from "react-router-dom";

import { useHorseHistory, useHorseProfile } from "../api/queries";
import { QueryStateView } from "../components/StateView";
import { formatNum, formatOdds, formatPct, PLACEHOLDER } from "../lib/format";
import { venueName } from "../lib/venues";

// Feature 029: horse profile — identity + pedigree (names) + FACTUAL career aggregates + history.
// These are confirmed results (NOT model predictions/features); shown separately from p/q.
export function HorseDetailPage() {
  const { horseId = "" } = useParams();
  const profile = useHorseProfile(horseId);
  const history = useHorseHistory(horseId, { page_size: 50 });

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
          loadingLabel="馬情報を読み込み中…"
        >
          {(h) => (
            <>
              <h2 className="race-title">{h.horse_name ?? h.horse_id}</h2>
              <div className="race-meta">
                <span>{h.sex ?? PLACEHOLDER}</span>
                <span>{h.birth_year != null ? `${h.birth_year}年産` : PLACEHOLDER}</span>
                <span>父 {h.sire_name ?? PLACEHOLDER}</span>
                <span>母 {h.dam_name ?? PLACEHOLDER}</span>
                <span>母父 {h.damsire_name ?? PLACEHOLDER}</span>
              </div>

              <h3 className="section-label">通算成績（確定実績）</h3>
              <div className="stat-grid">
                <div className="stat"><span className="stat__k">出走</span><span className="stat__v">{h.starts}</span></div>
                <div className="stat"><span className="stat__k">勝利</span><span className="stat__v">{h.wins}</span></div>
                <div className="stat"><span className="stat__k">勝率</span><span className="stat__v">{formatPct(h.win_rate)}</span></div>
                <div className="stat"><span className="stat__k">連対率</span><span className="stat__v">{formatPct(h.quinella_rate)}</span></div>
                <div className="stat"><span className="stat__k">複勝率</span><span className="stat__v">{formatPct(h.show_rate)}</span></div>
                <div className="stat"><span className="stat__k">平均着順</span><span className="stat__v">{formatNum(h.avg_finish)}</span></div>
              </div>
            </>
          )}
        </QueryStateView>
      </div>

      <div className="panel">
        <h2>出走履歴</h2>
        <QueryStateView
          isLoading={history.isLoading}
          error={history.error ?? null}
          data={history.data}
          isEmpty={(d) => d.items.length === 0}
          loadingLabel="履歴を読み込み中…"
          emptyMessage="出走履歴がありません"
        >
          {(d) => (
            <table className="data-table">
              <thead>
                <tr>
                  <th>日付</th><th>開催</th><th>レース</th><th>着順</th>
                  <th>人気</th><th>単勝</th><th>上がり</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((row) => (
                  <tr key={row.race_id}>
                    <td>{row.race_date ?? PLACEHOLDER}</td>
                    <td>{venueName(row.venue_code)} {row.race_number ?? ""}R</td>
                    <td>
                      <Link to={`/races/${row.race_id}`}>
                        {(row.race_name ?? "").replace(/\*+$/, "") || row.race_class || PLACEHOLDER}
                      </Link>
                    </td>
                    <td>{row.finish_order ?? (row.entry_status && row.entry_status !== "started" ? "—" : PLACEHOLDER)}</td>
                    <td>{row.popularity ?? PLACEHOLDER}</td>
                    <td>{formatOdds(row.odds)}</td>
                    <td>{formatNum(row.last_3f, 1)}</td>
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
