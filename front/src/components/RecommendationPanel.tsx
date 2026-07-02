import { useMemo, useState } from "react";

import { useRecommendations } from "../api/queries";
import type { RecommendationRow } from "../api/types";
import { betTypeLabel } from "../lib/betTypes";
import { formatOdds, formatPct, formatSelection } from "../lib/format";
import { PseudoValue, ResultBadge, SourceBadge } from "./PseudoValue";
import { QueryStateView } from "./StateView";

/**
 * Persisted exotic recommendations (READ-ONLY — the API never re-generates them). Each row exposes
 * is_estimated_odds / pseudo_odds / pseudo_roi / double_pseudo so the front CANNOT present
 * pseudo-ROI as realized return. Every non-real figure routes through <PseudoValue>.
 *
 * Feature 049: WIN rows also carry a RETROSPECTIVE backtest (settled/hit/realized_return/
 * realized_roi) from REAL win odds × official result — these are REAL facts (not pseudo), shown in
 * a separate 「結果(実績)」 column group with a <ResultBadge> so they are never read as the
 * decision-time pseudo-ROI beside them.
 */
function pseudoRoiKind(row: RecommendationRow): "pseudo" | "double_pseudo" {
  return row.double_pseudo ? "double_pseudo" : "pseudo";
}

/** 的中セル: settled win 行のみ実績を出す。void=無効・未 settled/非 win=「—」。 */
function HitCell({ row }: { row: RecommendationRow }) {
  if (row.bet_type !== "win" || !row.settled) return <>—</>;
  if (row.hit === null || row.hit === undefined)
    return <span data-result="void" title="対象馬に結果行なし(出走取消等)= 無効">無効</span>;
  return (
    <span data-result={row.hit ? "hit" : "miss"}>
      {row.hit ? "的中" : "不的中"}
      {row.hit && row.dead_heat ? (
        <span title="同着(実配当は分割されるため回収倍率は名目値)"> ⚑同着</span>
      ) : null}{" "}
      <ResultBadge />
    </span>
  );
}

export function RecommendationPanel({ raceId }: { raceId: string }) {
  const query = useRecommendations(raceId);
  const [betType, setBetType] = useState<string>("all");

  const betTypesPresent = useMemo(() => {
    const set = new Set((query.data?.items ?? []).map((r) => r.bet_type));
    return Array.from(set);
  }, [query.data]);

  const rows = useMemo(() => {
    const items = query.data?.items ?? [];
    return betType === "all" ? items : items.filter((r) => r.bet_type === betType);
  }, [query.data, betType]);

  return (
    <div className="panel">
      <h2>買い目推奨(永続データ・推奨は生成しない)</h2>
      <div className="toolbar">
        <label htmlFor="rec-bet-type">券種</label>
        <select
          id="rec-bet-type"
          value={betType}
          onChange={(e) => setBetType(e.target.value)}
        >
          <option value="all">すべて</option>
          {betTypesPresent.map((b) => (
            <option key={b} value={b}>
              {betTypeLabel(b)}
            </option>
          ))}
        </select>
      </div>

      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={() => rows.length === 0}
        loadingLabel="推奨を読み込み中…"
        emptyMessage="この条件の推奨はありません"
      >
        {() => (
          <>
            <table>
              <thead>
                <tr>
                  <th colSpan={2} />
                  <th className="colgroup" colSpan={4}>予測時(疑似)</th>
                  <th className="colgroup" colSpan={2}>結果(実績)</th>
                </tr>
                <tr>
                  <th>券種</th>
                  <th>組み合わせ</th>
                  <th className="num">使用オッズ</th>
                  <th className="num">疑似オッズ</th>
                  <th className="num">疑似ROI</th>
                  <th className="num">Kelly比率</th>
                  <th>的中</th>
                  <th className="num">実現回収</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.recommendation_id}>
                    <td>{betTypeLabel(r.bet_type)}</td>
                    <td>{formatSelection(r.selection)}</td>
                    <td className="num">
                      {/* used odds: estimated → pseudo-badged; real → plain + real source badge */}
                      {r.is_estimated_odds ? (
                        <PseudoValue kind="estimated">
                          {formatOdds(r.estimated_market_odds_used)}
                        </PseudoValue>
                      ) : (
                        <>
                          {formatOdds(r.market_odds_used)} <SourceBadge source="real" />
                        </>
                      )}
                    </td>
                    <td className="num">
                      <PseudoValue kind="pseudo">{formatOdds(r.pseudo_odds)}</PseudoValue>
                    </td>
                    <td className="num">
                      <PseudoValue kind={pseudoRoiKind(r)}>
                        {r.pseudo_roi === null || r.pseudo_roi === undefined
                          ? "—"
                          : `${(r.pseudo_roi * 100).toFixed(1)}%`}
                      </PseudoValue>
                    </td>
                    <td className="num">
                      {/* Feature 043: Kelly effective fraction (016). NULL=flat. Estimated odds →
                          double-pseudo (same kind as pseudo-ROI) so it's never read as real. */}
                      {r.stake_fraction === null || r.stake_fraction === undefined ? (
                        "—"
                      ) : (
                        <PseudoValue kind={pseudoRoiKind(r)}>
                          {`${(r.stake_fraction * 100).toFixed(2)}%`}
                        </PseudoValue>
                      )}
                    </td>
                    {/* Feature 049: REAL retrospective result (not pseudo). win-only; else "—". */}
                    <td>
                      <HitCell row={r} />
                    </td>
                    <td className="num">
                      {r.bet_type === "win" && r.settled && r.realized_return !== null &&
                      r.realized_return !== undefined ? (
                        <>
                          {formatOdds(r.realized_return)}{" "}
                          <span data-result="roi">
                            ({r.realized_roi !== null && r.realized_roi !== undefined
                              ? `${r.realized_roi >= 0 ? "+" : ""}${(r.realized_roi * 100).toFixed(0)}%`
                              : "—"})
                          </span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <WinBacktestSummary rows={rows} />
          </>
        )}
      </QueryStateView>
    </div>
  );
}

/**
 * US2: retrospective WIN summary derived from the DISPLAYED win rows (transparent — the user sees
 * the rows it aggregates). Factual only (n / 的中率 / 回収率), labeled 過去実績・参考 with no profit
 * language, no P/L coloring, no sorting — it is NOT a projection or a strategy claim (021 規律).
 */
function WinBacktestSummary({ rows }: { rows: RecommendationRow[] }) {
  const settled = rows.filter((r) => r.bet_type === "win" && r.settled && r.hit !== null &&
    r.hit !== undefined && r.realized_return !== null && r.realized_return !== undefined);
  if (settled.length === 0) return null;
  const nHit = settled.filter((r) => r.hit).length;
  const totalReturn = settled.reduce((s, r) => s + (r.realized_return ?? 0), 0);
  const hitRate = nHit / settled.length;
  const recovery = totalReturn / settled.length; // per-unit 回収率(平均回収倍率)
  return (
    <div className="backtest-summary" data-testid="win-backtest-summary">
      <h3>単勝推奨の過去実績(参考)</h3>
      <p className="note">
        確定済みの単勝推奨に対する事後集計(retrospective・in-sample)。実オッズ×公式結果の事実であり、
        将来の的中・利益を示すものではありません。
      </p>
      <dl className="backtest-stats">
        <div><dt>確定件数</dt><dd>{settled.length}</dd></div>
        <div><dt>的中</dt><dd>{nHit}</dd></div>
        <div><dt>的中率</dt><dd>{formatPct(hitRate)}</dd></div>
        <div><dt>回収率(平均回収倍率)</dt><dd>×{recovery.toFixed(2)}</dd></div>
      </dl>
    </div>
  );
}
