import type { RaceDispersion } from "../api/types";
import { formatPct, formatNum, PLACEHOLDER } from "../lib/format";
import { BAND_LABEL, UNAVAILABLE_LABEL } from "../lib/dispersionLabels";
import { PseudoValue } from "./PseudoValue";

/**
 * Feature 066 axis A: race-level "how open is this race" (荒れ度) readout.
 *
 * A DECISION-SUPPORT instrument, NOT a new edge and NOT a buy signal. It summarises the MARKET
 * vote-share q (pseudo, market-derived) over the canonical field: a 5-step band (堅い↔波乱含み) plus
 * the raw numbers behind it (本命勝率 / 上位3頭 / 正規化エントロピー). The band comes from a frozen
 * historical boundary (results never consulted); when no boundary is loaded the band is omitted but
 * the raw numbers still show. q missing → honest unavailable state, NEVER a fallback to model p.
 *
 * Display discipline (021/040/049): no profit/danger/value wording, no red/green P&L colour, no
 * sorting. q figures render through <PseudoValue kind="market_q"> so the pseudo badge is mandatory.
 */
export function RaceDispersionPanel({
  dispersion,
}: {
  dispersion: RaceDispersion | null | undefined;
}) {
  if (!dispersion) return null;

  if (!dispersion.available) {
    return (
      <section className="dispersion" data-testid="race-dispersion" data-available="false">
        <h3 className="dispersion__title">荒れ度(市場から見た決着集中度)</h3>
        <p className="state state--empty" data-testid="dispersion-unavailable">
          {dispersion.unavailable_reason
            ? UNAVAILABLE_LABEL[dispersion.unavailable_reason]
            : "荒れ度は表示できません。"}
        </p>
      </section>
    );
  }

  const bandLabel = dispersion.band ? BAND_LABEL[dispersion.band] : null;

  return (
    <section className="dispersion" data-testid="race-dispersion" data-available="true">
      <h3 className="dispersion__title">荒れ度(市場から見た決着集中度)</h3>
      <div className="dispersion__band" data-testid="dispersion-band">
        {/* band is a neutral descriptor; null when no boundary artifact is loaded (F8). */}
        <PseudoValue kind="market_q">
          <span className="dispersion__band-label" data-band={dispersion.band ?? "none"}>
            {bandLabel ?? "区分なし"}
          </span>
        </PseudoValue>
        {!bandLabel && (
          <span className="dispersion__hint" data-testid="dispersion-no-boundary">
            （バンド未設定：生数値のみ）
          </span>
        )}
      </div>
      {/* Raw numbers always shown beside the band (guards against false precision of a lone label). */}
      <dl className="dispersion__facts">
        <div>
          <dt>本命勝率</dt>
          <dd>
            <PseudoValue kind="market_q">{formatPct(dispersion.favorite_win_prob)}</PseudoValue>
          </dd>
        </div>
        <div>
          <dt>上位3頭シェア</dt>
          <dd>
            <PseudoValue kind="market_q">{formatPct(dispersion.top3_cumulative)}</PseudoValue>
          </dd>
        </div>
        <div>
          <dt>正規化エントロピー</dt>
          <dd>
            <PseudoValue kind="market_q">{formatNum(dispersion.normalized_entropy, 3)}</PseudoValue>
          </dd>
        </div>
      </dl>
      <p className="dispersion__note" data-testid="dispersion-note">
        市場のオッズ由来の見方の要約です。買い目の推奨ではなく、実際に荒れるかの保証でもありません。
        （オッズ種別: <code>{dispersion.odds_source ?? PLACEHOLDER}</code>
        {dispersion.odds_source === "final" && "＝発走前でない可能性あり"}）
      </p>
    </section>
  );
}
