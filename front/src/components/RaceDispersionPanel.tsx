import type { RaceDispersion } from "../api/types";
import { formatPct, formatNum, PLACEHOLDER } from "../lib/format";
import { BAND_LABEL, BAND_ORDER, BAND_CAPTION, UNAVAILABLE_LABEL } from "../lib/dispersionLabels";
import { PseudoValue } from "./PseudoValue";

/**
 * Feature 066 axis A: race-level "how open is this race" (荒れ度) readout, VISUALISED.
 *
 * A DECISION-SUPPORT instrument, NOT a new edge and NOT a buy signal. Summarises the MARKET
 * vote-share q (pseudo) over the canonical field: a 5-step gauge (堅い↔波乱含み, neutral single-hue
 * ramp — NO red/green P&L colour) with the current band marked, plus the raw numbers behind it
 * (本命勝率 / 上位3頭シェア / 集中度). The band comes from a frozen boundary (results never consulted);
 * when no boundary is loaded the gauge is omitted but the raw numbers still show (F8). q missing →
 * honest unavailable state, NEVER a fallback to model p.
 *
 * Display discipline (021/040/049): no profit/danger/value wording, no sorting. The whole q-derived
 * body is wrapped in ONE <PseudoValue kind="market_q"> so the pseudo badge is mandatory (015).
 */
const pct = (v: number | null | undefined): number =>
  v === null || v === undefined || Number.isNaN(v) ? 0 : Math.max(0, Math.min(100, v * 100));

export function RaceDispersionPanel({
  dispersion,
}: {
  dispersion: RaceDispersion | null | undefined;
}) {
  if (!dispersion) return null;

  if (!dispersion.available) {
    return (
      <section className="dispersion" data-testid="race-dispersion" data-available="false">
        <h3 className="dispersion__title">荒れ度<span className="dispersion__sub">市場から見た決着集中度</span></h3>
        <p className="dispersion__empty" data-testid="dispersion-unavailable">
          {dispersion.unavailable_reason
            ? UNAVAILABLE_LABEL[dispersion.unavailable_reason]
            : "荒れ度は表示できません。"}
        </p>
      </section>
    );
  }

  const band = dispersion.band;
  const level = band ? BAND_ORDER.indexOf(band) : -1;
  const bandLabel = band ? BAND_LABEL[band] : null;

  return (
    <section className="dispersion" data-testid="race-dispersion" data-available="true">
      <h3 className="dispersion__title">荒れ度<span className="dispersion__sub">市場から見た決着集中度</span></h3>
      <PseudoValue kind="market_q">
        <div className="dispersion__body">
          <div className="dispersion__band" data-testid="dispersion-band">
            <span className="dispersion__band-name" data-lvl={level}>
              {bandLabel ?? "区分なし"}
            </span>
            {band && <span className="dispersion__band-cap">{BAND_CAPTION[band]}</span>}
          </div>

          {band ? (
            <div className="dispersion__gauge" role="img"
                 aria-label={`5段中${level + 1}段目 ${bandLabel}`}>
              <div className="dispersion__segs">
                {BAND_ORDER.map((b, i) => (
                  <span key={b} className={`dispersion__seg${i === level ? " is-active" : ""}`}
                        data-lvl={i}>
                    {i === level && <i className="dispersion__caret" />}
                  </span>
                ))}
              </div>
              <div className="dispersion__ticks">
                {BAND_ORDER.map((b, i) => (
                  <span key={b} className={i === level ? "on" : undefined}>{BAND_LABEL[b]}</span>
                ))}
              </div>
            </div>
          ) : (
            <p className="dispersion__hint" data-testid="dispersion-no-boundary">
              バンド未設定（生数値のみ）
            </p>
          )}

          <dl className="dispersion__facts">
            <div className="dispersion__fact">
              <dt>本命勝率</dt>
              <dd>
                <span className="dispersion__bar"><i style={{ width: `${pct(dispersion.favorite_win_prob)}%` }} /></span>
                <b>{formatPct(dispersion.favorite_win_prob)}</b>
              </dd>
            </div>
            <div className="dispersion__fact">
              <dt>上位3頭シェア</dt>
              <dd>
                <span className="dispersion__bar"><i style={{ width: `${pct(dispersion.top3_cumulative)}%` }} /></span>
                <b>{formatPct(dispersion.top3_cumulative)}</b>
              </dd>
            </div>
            <div className="dispersion__fact">
              <dt>集中度<span className="dispersion__mut">（エントロピー）</span></dt>
              <dd>
                <span className="dispersion__scale"><i style={{ left: `${pct(dispersion.normalized_entropy)}%` }} /></span>
                <b>{formatNum(dispersion.normalized_entropy, 3)}</b>
              </dd>
            </div>
          </dl>
        </div>
      </PseudoValue>
      <p className="dispersion__note" data-testid="dispersion-note">
        市場のオッズ由来の見方の要約です。買い目の推奨ではなく、実際に荒れるかの保証でもありません。
        （オッズ種別 <code>{dispersion.odds_source ?? PLACEHOLDER}</code>
        {dispersion.odds_source === "final" && "＝発走前でない可能性あり"}）
      </p>
    </section>
  );
}
