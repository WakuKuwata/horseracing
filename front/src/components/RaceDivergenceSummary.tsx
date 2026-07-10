import type { RaceDivergence } from "../api/types";
import { formatPct } from "../lib/format";

/**
 * Feature 066 axis B: race-level model-vs-market divergence summary (人気馬 / 人気薄 の判断材料).
 *
 * A NEUTRAL FACT of where model p and market q disagree — NOT a claim that the model is right (047:
 * q predicts better) and NOT a buy signal (040 discipline). Shows the favourite direction, any
 * horses the model ranks in its top 3 that the market does not, and a top-3 rank-agreement figure.
 * Suppressed when p/q populations differ (available=false). No profit/edge/value wording, no P&L
 * colour, no sorting. The full per-horse p/q table stays in HorseEntriesTable (040, unchanged).
 */
export function RaceDivergenceSummary({
  divergence,
}: {
  divergence: RaceDivergence | null | undefined;
}) {
  if (!divergence || !divergence.available) return null;

  return (
    <section className="divergence" data-testid="race-divergence">
      <h3 className="divergence__title">モデルと市場の意見差</h3>
      {divergence.summary && (
        <p className="divergence__summary" data-testid="divergence-summary"
           data-favorite-direction={divergence.favorite_direction ?? "none"}>
          {divergence.summary}
        </p>
      )}
      {divergence.underrated_longshots.length > 0 && (
        <div className="divergence__longshots" data-testid="divergence-longshots">
          <span className="divergence__longshots-label">
            モデルが上位に見る人気薄（事実・買い推奨ではありません）:
          </span>
          <ul>
            {divergence.underrated_longshots.map((ls) => (
              <li key={ls.horse_number} data-horse-number={ls.horse_number}>
                {ls.horse_number}番（{ls.popularity_rank}番人気 / モデル{formatPct(ls.p)}・
                市場{formatPct(ls.q)}）
              </li>
            ))}
          </ul>
        </div>
      )}
      {divergence.rank_agreement !== null && divergence.rank_agreement !== undefined && (
        <p className="divergence__agreement" data-testid="divergence-agreement">
          上位3頭の一致度: {formatPct(divergence.rank_agreement)}
          （モデルと市場の上位3頭の重なり）
        </p>
      )}
      <p className="divergence__note">
        どちらが当たるかを示すものではありません。意見が割れている点を確認するための表示です。
        {divergence.model_version && (
          <>
            {" "}
            比較モデル: <code>{divergence.model_version}</code>
          </>
        )}
      </p>
    </section>
  );
}
