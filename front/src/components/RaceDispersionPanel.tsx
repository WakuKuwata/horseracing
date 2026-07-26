import type { RaceDispersion } from "../api/types";
import { formatPct, formatNum, PLACEHOLDER } from "../lib/format";
import {
  BAND_LABEL,
  BAND_ORDER,
  UNAVAILABLE_LABEL,
  DIRECTION_LABEL,
} from "../lib/dispersionLabels";
import { PseudoValue } from "./PseudoValue";

/**
 * Feature 066 axis A: market support concentration detail.
 *
 * Feature 084 demotes this entropy-based instrument to a collapsed disclosure so it cannot be
 * mistaken for the primary top-three outcome readout. Its frozen firm..open API vocabulary remains
 * unchanged, but outcome-claiming band captions are intentionally absent.
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
      <details className="dispersion" data-testid="race-dispersion" data-available="false">
        <summary className="dispersion__title">
          市場の支持集中度
          <span className="dispersion__sub">単勝支持の5段階スケール</span>
        </summary>
        <p className="dispersion__empty" data-testid="dispersion-unavailable">
          {dispersion.unavailable_reason
            ? UNAVAILABLE_LABEL[dispersion.unavailable_reason]
            : "市場の支持集中度は表示できません。"}
        </p>
      </details>
    );
  }

  const band = dispersion.band;
  const level = band ? BAND_ORDER.indexOf(band) : -1;
  const bandLabel = band ? BAND_LABEL[band] : null;

  return (
    <details className="dispersion" data-testid="race-dispersion" data-available="true">
      <summary className="dispersion__title">
        市場の支持集中度
        <span className="dispersion__sub">単勝支持の5段階スケール</span>
      </summary>
      <PseudoValue kind="market_q">
        <div className="dispersion__body">
          <div className="dispersion__band" data-testid="dispersion-band">
            <span className="dispersion__band-name" data-lvl={level}>
              {bandLabel ?? "区分なし"}
            </span>
          </div>

          {band ? (
            <div className="dispersion__gauge" role="img"
                 aria-label={`市場の支持集中度：5段中${level + 1}段目 ${bandLabel}`}>
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
      {dispersion.model_delta?.direction && (
        <p className="dispersion__model-delta" data-testid="dispersion-model-delta">
          <span className="dispersion__mut">モデル目線（校正済み）</span>
          <b>{DIRECTION_LABEL[dispersion.model_delta.direction]}</b>
          <span className="dispersion__mut">
            （集中度差 {formatNum(dispersion.model_delta.normalized_entropy_delta, 3)}）
          </span>
        </p>
      )}
      <p className="dispersion__note" data-testid="dispersion-note">
        市場のオッズ由来の見方の要約です。買い目の推奨ではなく、実際に荒れるかの保証でもありません。
        （オッズ種別 <code>{dispersion.odds_source ?? PLACEHOLDER}</code>
        {dispersion.odds_source === "final" && "＝発走前でない可能性あり"}）
      </p>
    </details>
  );
}
