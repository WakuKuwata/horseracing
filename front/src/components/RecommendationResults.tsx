import type { RecommendationResponse, RecommendationRow } from "../api/types";
import { betTypeLabel } from "../lib/betTypes";
import { formatOdds, formatPct, formatSelection } from "../lib/format";
import { ResultBadge } from "./PseudoValue";

/**
 * Feature 087 (T017B): the 答え合わせ view — the settled-race retrospective that used to live
 * inline in RecommendationPanel's table. Extracted UNCHANGED in computation and meaning
 * (FR-022): per-row 的中/無効/同着 semantics (049/075), the counterfactual snapshot summary,
 * honest baselines and the odds-band breakdown (064). No new numbers are computed here.
 */

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

function ReturnCell({ row }: { row: RecommendationRow }) {
  if (
    row.bet_type === "win" && row.settled &&
    row.counterfactual_snapshot_gross_return !== null &&
    row.counterfactual_snapshot_gross_return !== undefined
  ) {
    return (
      <>
        {formatOdds(row.counterfactual_snapshot_gross_return)}{" "}
        <span data-result="roi">
          ({row.counterfactual_snapshot_net_return !== null &&
          row.counterfactual_snapshot_net_return !== undefined
            ? `${row.counterfactual_snapshot_net_return >= 0 ? "+" : ""}${(row.counterfactual_snapshot_net_return * 100).toFixed(0)}%`
            : "—"})
        </span>
      </>
    );
  }
  return <>—</>;
}

/** Feature 064: odds bands for the retrospective recovery breakdown (neutral, not sorted). */
const ODDS_BANDS: Array<[string, (o: number) => boolean]> = [
  ["<3", (o) => o < 3],
  ["3–6", (o) => o >= 3 && o < 6],
  ["6–11", (o) => o >= 6 && o < 11],
  ["11–21", (o) => o >= 11 && o < 21],
  ["21–51", (o) => o >= 21 && o < 51],
  ["51+", (o) => o >= 51],
];

export function RecommendationResults({
  items,
  data,
}: {
  items: RecommendationRow[];
  data: RecommendationResponse;
}) {
  const winRows = items.filter((r) => r.bet_type === "win");
  return (
    <div className="results-view" data-testid="results-view">
      {winRows.length > 0 ? (
        <table>
          <thead>
            <tr>
              <th>券種</th>
              <th>組み合わせ</th>
              <th>的中</th>
              <th className="num">回収(反実仮想)</th>
            </tr>
          </thead>
          <tbody>
            {winRows.map((r) => (
              <tr key={r.recommendation_id}>
                <td>{betTypeLabel(r.bet_type)}</td>
                <td>{formatSelection(r.selection)}</td>
                <td><HitCell row={r} /></td>
                <td className="num"><ReturnCell row={r} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <WinBacktestSummary rows={items} data={data} />
    </div>
  );
}

/**
 * US2 (049): retrospective WIN summary derived from the DISPLAYED win rows (transparent — the
 * user sees the rows it aggregates). Factual only (n / 的中率 / 回収率), labeled 過去実績・参考
 * with no profit language, no P/L coloring, no sorting — NOT a projection (021 規律).
 */
export function WinBacktestSummary({
  rows,
  data,
}: {
  rows: RecommendationRow[];
  data?: RecommendationResponse;
}) {
  const winRows = rows.filter((r) => r.bet_type === "win");
  const settled = winRows.filter((r) => r.settled && r.hit !== null && r.hit !== undefined &&
    r.counterfactual_snapshot_gross_return !== null &&
    r.counterfactual_snapshot_gross_return !== undefined);

  if (winRows.length === 0 || settled.length === 0) return null;

  const nHit = settled.filter((r) => r.hit).length;
  const totalReturn = settled.reduce(
    (s, r) => s + (r.counterfactual_snapshot_gross_return ?? 0),
    0,
  );
  const hitRate = nHit / settled.length;
  const recovery = totalReturn / settled.length; // per-unit 回収率(平均回収倍率)
  const fav = data?.favorite_baseline;

  return (
    <div className="backtest-summary" data-testid="win-backtest-summary">
      <h3>単勝推奨の反実仮想(判断時オッズ)(参考)</h3>
      <p className="note">
        確定済みの単勝推奨に対する事後集計(retrospective・in-sample)。判断時に凍結したオッズ×
        公式結果に基づく反実仮想値であり、将来の的中・利益を示すものではありません。
      </p>
      <dl className="backtest-stats">
        <div><dt>確定件数</dt><dd>{settled.length}</dd></div>
        <div><dt>的中</dt><dd>{nHit}</dd></div>
        <div><dt>的中率</dt><dd>{formatPct(hitRate)}</dd></div>
        <div><dt>反実仮想(判断時オッズ)回収率(平均回収倍率)</dt><dd>×{recovery.toFixed(2)}</dd></div>
      </dl>
      {/* Feature 064: honest reference lines — NOT profit strategies (no coloring, no ranking). */}
      <table className="baseline-table" data-testid="win-baselines">
        <thead>
          <tr><th>基準</th><th className="num">回収</th></tr>
        </thead>
        <tbody>
          <tr>
            <td>賭けない(資金を減らさない基準)</td>
            <td className="num">×1.00</td>
          </tr>
          <tr>
            <td>本命ベタ買い(市場ベースライン・現在オッズ基準{fav?.horse_number ? `・${fav.horse_number}番` : ""})</td>
            <td className="num">
              {fav && fav.settled && fav.current_odds_gross_return !== null &&
                fav.current_odds_gross_return !== undefined
                ? `×${fav.current_odds_gross_return.toFixed(2)}${fav.hit ? "(的中)" : "(不的中)"}`
                : "—"}
            </td>
          </tr>
        </tbody>
      </table>
      <WinOddsBandBreakdown settled={settled} />
    </div>
  );
}

/** Feature 064: retrospective recovery by odds band — the longshot-tail bleed made visible. */
function WinOddsBandBreakdown({ settled }: { settled: RecommendationRow[] }) {
  const bands = ODDS_BANDS.map(([label, test]) => {
    const inBand = settled.filter((r) => {
      const o = r.market_odds_used;
      return o !== null && o !== undefined && test(o);
    });
    const ret = inBand.reduce(
      (s, r) => s + (r.counterfactual_snapshot_gross_return ?? 0),
      0,
    );
    return {
      label,
      n: inBand.length,
      counterfactual_snapshot_recovery: inBand.length ? ret / inBand.length : null,
    };
  }).filter((b) => b.n > 0);
  if (bands.length === 0) return null;
  return (
    <table className="oddsband-table" data-testid="win-odds-band">
      <thead>
        <tr><th>オッズ帯</th><th className="num">件数</th><th className="num">回収</th></tr>
      </thead>
      <tbody>
        {bands.map((b) => (
          <tr key={b.label}>
            <td>{b.label}</td>
            <td className="num">{b.n}</td>
            <td className="num">
              {b.counterfactual_snapshot_recovery !== null
                ? `×${b.counterfactual_snapshot_recovery.toFixed(2)}`
                : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
