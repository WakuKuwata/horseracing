import { useMemo, useState } from "react";

import { useRecommendations } from "../api/queries";
import type { RecommendationResponse, RecommendationRow } from "../api/types";
import { betTypeLabel } from "../lib/betTypes";
import { formatOdds, formatPct, formatSelection } from "../lib/format";
import { PseudoValue, ResultBadge, SourceBadge } from "./PseudoValue";
import { QueryStateView } from "./StateView";

/**
 * Persisted exotic recommendations (READ-ONLY — the API never re-generates them). Each row exposes
 * is_estimated_odds / pseudo_odds / pseudo_roi / double_pseudo so the front CANNOT present
 * pseudo-ROI as realized return. Every non-real figure routes through <PseudoValue>.
 *
 * Feature 075: WIN rows carry a counterfactual snapshot backtest (settled/hit/
 * counterfactual_snapshot_gross_return/counterfactual_snapshot_net_return) from frozen
 * decision-time win odds × official result. They are shown in a separate
 * 「反実仮想(判断時オッズ)」 column group so they are never read as literally realized returns or
 * confused with the decision-time pseudo-ROI beside them.
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
      {/* Feature 064 (FR-007): always-on neutral disclosure — no profit language, no coloring. */}
      <p className="note" data-testid="no-edge-note">
        このモデルは市場に対する再現可能な優位を持ちません。買い目は損失を抑えるための判断材料であり、
        将来の的中・利益を示すものではありません。過去実績は closing オッズによる事後・in-sample の
        参考値です。
      </p>
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
                  <th className="colgroup" colSpan={2}>反実仮想(判断時オッズ)</th>
                </tr>
                <tr>
                  <th>券種</th>
                  <th>組み合わせ</th>
                  <th className="num">使用オッズ</th>
                  <th className="num">疑似オッズ</th>
                  <th className="num">疑似ROI</th>
                  <th className="num">Kelly比率</th>
                  <th>的中</th>
                  <th className="num">回収</th>
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
                      {r.bet_type === "win" && r.settled &&
                      r.counterfactual_snapshot_gross_return !== null &&
                      r.counterfactual_snapshot_gross_return !== undefined ? (
                        <>
                          {formatOdds(r.counterfactual_snapshot_gross_return)}{" "}
                          <span data-result="roi">
                            ({r.counterfactual_snapshot_net_return !== null &&
                            r.counterfactual_snapshot_net_return !== undefined
                              ? `${r.counterfactual_snapshot_net_return >= 0 ? "+" : ""}${(r.counterfactual_snapshot_net_return * 100).toFixed(0)}%`
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
            <WinBacktestSummary rows={rows} data={query.data} />
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
/** Feature 064: honest win_policy_status → neutral message when the win section is empty. */
const WIN_POLICY_MESSAGE: Record<string, string> = {
  no_run: "このレースの予測がまだありません。",
  not_generated: "単勝の買い目はまだ生成されていません。",
  no_win_selected: "単勝は見送りです(policy が条件を満たす買い目を選定しませんでした)。",
};

/** Feature 064: odds bands for the retrospective recovery breakdown (neutral, not sorted). */
const ODDS_BANDS: Array<[string, (o: number) => boolean]> = [
  ["<3", (o) => o < 3],
  ["3–6", (o) => o >= 3 && o < 6],
  ["6–11", (o) => o >= 6 && o < 11],
  ["11–21", (o) => o >= 11 && o < 21],
  ["21–51", (o) => o >= 21 && o < 51],
  ["51+", (o) => o >= 51],
];

function WinBacktestSummary(
  { rows, data }: { rows: RecommendationRow[]; data?: RecommendationResponse },
) {
  const winRows = rows.filter((r) => r.bet_type === "win");
  const settled = winRows.filter((r) => r.settled && r.hit !== null && r.hit !== undefined &&
    r.counterfactual_snapshot_gross_return !== null &&
    r.counterfactual_snapshot_gross_return !== undefined);

  // Empty win section → surface the honest skip reason (never a blank).
  if (winRows.length === 0) {
    const msg = data ? WIN_POLICY_MESSAGE[data.win_policy_status] : undefined;
    return msg ? (
      <p className="note" data-testid="win-skip-reason">{msg}</p>
    ) : null;
  }
  if (settled.length === 0) return null;

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
