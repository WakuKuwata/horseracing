import type { PredictionResponse } from "../api/types";
import type { ErrorInfo } from "../api/client";
import { formatNum, formatPct, PLACEHOLDER } from "../lib/format";
import {
  BAND_LABEL,
  BAND_ORDER,
  EVENT_LABEL,
  PRE_CONFIRMATION_WORDING,
  SCALE_NAME,
  STANDING_DISCLOSURE,
  STRUCTURAL_ZERO_LABEL,
  TOTAL_COLLAPSE_NOTE,
  type ChaosEventKey,
} from "../lib/chaosLabels";
import { PseudoValue } from "./PseudoValue";

type RaceChaos = NonNullable<PredictionResponse["race_chaos"]>;
type AvailableChaos = Extract<RaceChaos, { status: "available" }>;
type ChaosEvent = AvailableChaos["events"][number];

const EVENT_ORDER: ChaosEventKey[] = ["s_ge_20", "himo_are", "total_collapse"];

const UNAVAILABLE_LABEL: Record<
  Extract<RaceChaos, { status: "unavailable" }>["unavailable_reason"],
  string
> = {
  no_snapshot: "発走前の市場スナップショットがないため表示できません。",
  partial_market_odds: "一部の出走馬に市場オッズがないため表示できません。",
  invalid_popularity_ranks: "人気順を確定できないため表示できません。",
  field_too_small: "出走頭数が3頭未満のため表示できません。",
  artifact_unavailable: "表示基準を読み込めないため表示できません。",
  out_of_validity_window: "表示基準の対象期間外のため表示できません。",
  invariant_violation: "確率の整合性を確認できないため表示できません。",
};

function eventValue(event: ChaosEvent | undefined, value: "adjusted_mass" | "raw_mass") {
  if (!event) return PLACEHOLDER;
  if (event.is_structural_zero) return STRUCTURAL_ZERO_LABEL;
  return formatPct(event[value], 0);
}

function capturedTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return PLACEHOLDER;
  return new Intl.DateTimeFormat("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Tokyo",
  }).format(date);
}

function freshness(chaos: AvailableChaos): string {
  const seconds = chaos.snapshot.seconds_to_post;
  const timing =
    seconds === null
      ? "発走時刻との差不明"
      : seconds >= 0
        ? `発走${Math.round(seconds / 60)}分前`
        : `発走${Math.round(Math.abs(seconds) / 60)}分後`;
  return `${timing}・${capturedTime(chaos.snapshot.captured_at)} 取得`;
}

function captureStrengthNote(strength: AvailableChaos["snapshot"]["capture_strength"]) {
  if (strength === "confirmatory") return null;
  if (strength === "weak") {
    return "発走時刻を確認できていないため、確認用の捕捉ではありません。";
  }
  return "捕捉条件を確認できていないため、確認用の捕捉ではありません。";
}

function relativePosition(percentile: number | null): string | null {
  if (percentile === null) return null;
  return `同頭数のレースの中では${percentile < 50 ? "低め" : "高め"}`;
}

/**
 * Feature 084: a market-only estimate of top-three popularity composition.
 *
 * The primary figure is P(S>=20); the five-step band is deliberately secondary. All derived
 * figures share one market-q pseudo badge, and raw market masses stay in the method disclosure.
 */
export function RaceChaosPanel({
  chaos,
  isLoading = false,
  error = null,
}: {
  chaos: RaceChaos | null | undefined;
  isLoading?: boolean;
  error?: ErrorInfo | null;
}) {
  if (isLoading) {
    return (
      <section className="dispersion race-chaos" data-testid="race-chaos">
        <h3 className="dispersion__title">{SCALE_NAME}</h3>
        <p className="dispersion__empty" data-testid="chaos-loading" data-state="loading">
          上位3着の荒れ度を読み込み中…
        </p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="dispersion race-chaos" data-testid="race-chaos">
        <h3 className="dispersion__title">{SCALE_NAME}</h3>
        <div className="dispersion__empty" data-testid="chaos-error" data-state="error">
          <strong>取得エラー {error.status}</strong>
          <span>（{error.code}）</span>
        </div>
      </section>
    );
  }

  if (!chaos) {
    return (
      <section className="dispersion race-chaos" data-testid="race-chaos">
        <h3 className="dispersion__title">{SCALE_NAME}</h3>
        <p className="dispersion__empty" data-testid="chaos-empty" data-state="empty">
          上位3着の荒れ度データがありません。
        </p>
      </section>
    );
  }

  if (chaos.status === "unavailable") {
    return (
      <section
        className="dispersion race-chaos"
        data-testid="race-chaos"
        data-available="false"
      >
        <h3 className="dispersion__title">{SCALE_NAME}</h3>
        <p
          className="dispersion__empty"
          data-testid="chaos-unavailable"
          data-state="unavailable"
        >
          {UNAVAILABLE_LABEL[chaos.unavailable_reason]}
        </p>
      </section>
    );
  }

  const events = new Map(chaos.events.map((event) => [event.key, event]));
  const primary = events.get("s_ge_20");
  const bandLevel = BAND_ORDER.indexOf(chaos.band);
  const strengthNote = captureStrengthNote(chaos.snapshot.capture_strength);

  return (
    <section
      className="dispersion race-chaos"
      data-testid="race-chaos"
      data-available="true"
    >
      <h3 className="dispersion__title">
        {SCALE_NAME}
        <span className="dispersion__sub">P(人気順合計≥20) の5段階スケール</span>
      </h3>

      <PseudoValue kind="market_q">
        <div className="dispersion__body">
          {chaos.calibration_status === "provisional" && (
            <p data-testid="chaos-pre-confirmation">{PRE_CONFIRMATION_WORDING}</p>
          )}

          <div data-testid="chaos-main">
            <div className="dispersion__band">
              <strong className="dispersion__band-name" data-testid="chaos-primary">
                {eventValue(primary, "adjusted_mass")}
              </strong>
              <span>{EVENT_LABEL.s_ge_20}</span>
            </div>

            <div className="dispersion__band" data-testid="chaos-band">
              <span className="dispersion__mut">5段階ラベル</span>
              <b data-lvl={bandLevel}>
                {BAND_LABEL[chaos.band]}
              </b>
            </div>
            <div
              className="dispersion__gauge"
              role="img"
              aria-label={`${SCALE_NAME}：5段中${bandLevel + 1}段目 ${BAND_LABEL[chaos.band]}`}
            >
              <div className="dispersion__segs">
                {BAND_ORDER.map((band, index) => (
                  <span
                    key={band}
                    className={`dispersion__seg${index === bandLevel ? " is-active" : ""}`}
                    data-lvl={index}
                  >
                    {index === bandLevel && <i className="dispersion__caret" />}
                  </span>
                ))}
              </div>
              <div className="dispersion__ticks">
                {BAND_ORDER.map((band, index) => (
                  <span key={band} className={index === bandLevel ? "on" : undefined}>
                    {BAND_LABEL[band]}
                  </span>
                ))}
              </div>
            </div>

            <dl className="dispersion__facts" data-testid="chaos-events">
              {EVENT_ORDER.slice(1).map((key) => {
                const event = events.get(key);
                return (
                  <div className="dispersion__fact" key={key}>
                    <dt>{EVENT_LABEL[key]}</dt>
                    <dd>
                      <b>{eventValue(event, "adjusted_mass")}</b>
                    </dd>
                    {key === "total_collapse" && event?.lambda_sensitive === false && (
                      <small>{TOTAL_COLLAPSE_NOTE}</small>
                    )}
                  </div>
                );
              })}
              <div className="dispersion__fact">
                <dt>人気順合計の期待値</dt>
                <dd>
                  <b>{formatNum(chaos.expected_top3_popularity_sum, 1)}</b>
                </dd>
              </div>
            </dl>

            <p data-testid="chaos-support">
              人気合計の可能範囲は {chaos.feasible_support[0]}〜{chaos.feasible_support[1]}
            </p>
            {chaos.field_size <= 9 && <p>二桁人気の馬はいません</p>}
            {relativePosition(chaos.within_field_size_percentile) && (
              <p data-testid="chaos-relative">
                {relativePosition(chaos.within_field_size_percentile)}
              </p>
            )}
          </div>

          <p className="dispersion__note" data-testid="chaos-standing-disclosure">
            {STANDING_DISCLOSURE}
          </p>

          <details data-testid="chaos-method">
            <summary>方法詳細</summary>
            <p>ステージ割引補正前の生の市場質量</p>
            <dl className="dispersion__facts">
              {EVENT_ORDER.map((key) => {
                const event = events.get(key);
                return (
                  <div className="dispersion__fact" key={key}>
                    <dt>{EVENT_LABEL[key]}</dt>
                    <dd>
                      <b>{eventValue(event, "raw_mass")}</b>
                    </dd>
                  </div>
                );
              })}
            </dl>
            <p>
              補正根拠: <code>{chaos.calibration_basis}</code>
            </p>
          </details>
        </div>
      </PseudoValue>

      <p className="dispersion__note" data-testid="chaos-freshness">
        {freshness(chaos)}
      </p>
      {strengthNote && (
        <p className="dispersion__note" data-testid="chaos-capture-strength">
          {strengthNote}
        </p>
      )}
    </section>
  );
}
