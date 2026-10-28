import { useState } from "react";

import { useCoverage } from "../api/queries";
import { RefreshRangeButton } from "../components/RefreshRangeButton";
import { QueryStateView } from "../components/StateView";
import { isoDaysAgo } from "../lib/dates";
import { formatInt, textOr } from "../lib/format";

/**
 * Feature 052 US1: per-day product coverage.
 *
 * B11 (2026-10-28): the ROI path is the main path (spec 154), so the HOLE a day is highlighted for
 * is now `n_market_ev_computed < n_races` (期待回収率 rows missing), with the two nested ROI
 * columns next to it (genuine pre-race quote on every stored row / 141 mixture computable). The
 * legacy prediction column stays as a plain count ("予測(比較用モデル)"; 044 ACTIVE semantics,
 * 未設定 when no model is active) and no longer drives the highlight.
 *
 * What fills which hole is stated on the page: the 053 range-refresh button runs `live refresh`
 * (legacy predictions + recommendations only); 期待回収率 rows come from the ops `expected_return`
 * job (enqueued by a race refresh) or `horseracing_training market-ev --date`.
 */
export function CoveragePage() {
  const [from, setFrom] = useState(() => isoDaysAgo(30));
  const [to, setTo] = useState(() => isoDaysAgo(0));
  const query = useCoverage(from, to);

  return (
    <div className="panel">
      <h1>データ被覆率</h1>
      <p className="note">
        日ごとの「レース数に対する オッズ/結果/期待回収率/予測(比較用モデル)/推奨 の充足」。
        期待回収率がレース数に満たない日は<mark>ハイライト</mark>= ROI 系の穴。
        「うち発走前 quote」は保存行のすべてが 140 の quote 来歴を持つレース、
        「うち混合可」は保存行から 141 の市場混合が計算できるレース(どちらも計算済みの内数)。
        保存表は最新の計算しか持たないので、発走後に再計算されたレースは quote の内数から外れます。
      </p>
      <p className="note" data-testid="coverage-fill-note">
        「この範囲を更新」「この日を更新」は比較用モデルの予測と推奨だけを作ります(live refresh)。
        期待回収率の穴は、レース個別の更新で enqueue される ops の expected_return ジョブか、
        <code>horseracing_training market-ev --date</code> で埋めます。
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
              期待回収率のモデル: <strong>{data.market_ev_model_version}</strong>
              {" / "}quote 来歴の印: <code>{data.quote_token}</code>
              {" / "}比較用モデル(運用中): <strong>{textOr(data.active_model_version) === "—"
                ? "未設定(予測被覆は 0 表示)" : data.active_model_version}</strong>
            </p>
            <table>
              <thead>
                <tr>
                  <th>開催日</th>
                  <th className="num">レース</th>
                  <th className="num">オッズ</th>
                  <th className="num">結果</th>
                  <th className="num">期待回収率</th>
                  <th className="num">うち発走前 quote</th>
                  <th className="num">うち混合可</th>
                  <th className="num">予測(比較用モデル)</th>
                  <th className="num">推奨</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.days.map((d) => {
                  const hole = d.n_market_ev_computed < d.n_races;
                  return (
                    <tr key={d.date} data-hole={hole}>
                      <td>{d.date}</td>
                      <td className="num">{formatInt(d.n_races)}</td>
                      <td className="num">{formatInt(d.n_with_odds)}</td>
                      <td className="num">{formatInt(d.n_with_results)}</td>
                      <td className="num" data-testid="market-ev-computed">
                        {formatInt(d.n_market_ev_computed)}
                        {hole ? <span title="期待回収率がレース数に満たない日(ROI 系の穴)"> ⚠</span> : null}
                      </td>
                      <td className="num">{formatInt(d.n_market_ev_quote)}</td>
                      <td className="num">{formatInt(d.n_market_ev_mix_available)}</td>
                      <td className="num">{formatInt(d.n_predicted_active)}</td>
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
