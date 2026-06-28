import type { PredictionResponse } from "../api/types";
import { formatDateTime, formatPct, PLACEHOLDER } from "../lib/format";
import { PseudoValue } from "./PseudoValue";

/**
 * Feature 021 US1: model p vs market q side by side.
 *
 * NEUTRAL presentation (codex R3): p−q is "model vs market disagreement", NOT a buy signal — no
 * profit language, no win/loss colours, no sorting/highlighting BY the edge. q is pseudo (market
 * vote-share, FL bias) and renders through <PseudoValue kind="market_q"> so it can never appear
 * unlabelled (V). q is null (未提供) when a horse lacks odds. When the API says p and q are on
 * different populations (canonical_consistent=false) the divergence is mathematically incomparable
 * (R1) and we suppress the 差 column. We also disclose that the market out-predicts the model (020).
 */
export function PQCompare({ data }: { data: PredictionResponse }) {
  // Order by model p (descending) — a model-side ordering, NOT an edge ordering.
  const horses = [...data.horses].sort((a, b) => (b.win ?? 0) - (a.win ?? 0));
  const comparable = data.canonical_consistent === true;

  return (
    <div>
      <p className="note" data-testid="market-superiority-note">
        ※ 市場推定 q は実データでモデル p より win 予測が上手いことが確認されています(020)。
        p−q は「モデルと市場の見解の相違」であり、買い目の推奨ではありません。
      </p>
      <div className="audit">
        <span>
          オッズ時刻: <code>{formatDateTime(data.odds_as_of)}</code>
        </span>
        <span>
          オッズ種別: <code>{data.odds_source ?? PLACEHOLDER}</code>
        </span>
        <span>
          q 出所: <code>{data.market_prob_source ?? PLACEHOLDER}</code>
        </span>
      </div>
      {!comparable && (
        <p className="state state--empty" data-testid="pq-incomparable">
          p と q の母集団が一致しないため、差(p−q)は表示しません(比較不可)。
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th className="num">馬番</th>
            <th>馬ID</th>
            <th className="num">モデル勝率 p</th>
            <th className="num">市場推定 q</th>
            {comparable && <th className="num">差 (p−q)</th>}
          </tr>
        </thead>
        <tbody>
          {horses.map((h) => {
            const diff =
              comparable && h.win != null && h.market_win_prob != null
                ? h.win - h.market_win_prob
                : null;
            return (
              <tr key={h.horse_id}>
                <td className="num">{h.horse_number ?? PLACEHOLDER}</td>
                <td>{h.horse_id}</td>
                <td className="num">{formatPct(h.win)}</td>
                <td className="num">
                  {h.market_win_prob == null ? (
                    PLACEHOLDER
                  ) : (
                    <PseudoValue kind="market_q">{formatPct(h.market_win_prob)}</PseudoValue>
                  )}
                </td>
                {comparable && (
                  <td className="num">
                    {diff == null
                      ? PLACEHOLDER
                      : `${diff >= 0 ? "+" : ""}${(diff * 100).toFixed(1)}pt`}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
