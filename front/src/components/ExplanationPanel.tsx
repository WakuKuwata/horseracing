import type { Explanation } from "../api/types";
import { formatNum, PLACEHOLDER } from "../lib/format";
import { featureLabel } from "./featureLabels";

// Feature 040 US1: per-horse SCORE CONTRIBUTION panel (not a probability breakdown).
// Contributions decompose the RAW model score (before race-relative softmax / calibration /
// normalisation), so the limitation notes below are MANDATORY and always rendered — a contribution
// must never be read as "the reason this horse wins" or as a share of the final probability.

const NOTE_SCORE_V1 = "校正・レース内正規化前のスコアへの寄与です（最終確率の内訳ではありません）";
const NOTE_SCORE_V2 = "同一レース内の平均に対する、レース内正規化前の相対スコア寄与です（最終確率の内訳ではありません）";
const NOTE_CAUSAL = "相関に基づく説明であり、因果関係を示すものではありません";

function Bar({ contribution, max }: { contribution: number; max: number }) {
  const pct = max > 0 ? (Math.abs(contribution) / max) * 100 : 0;
  const positive = contribution >= 0;
  return (
    <span className="contrib-bar" aria-hidden="true">
      <span className={`contrib-fill ${positive ? "contrib-pos" : "contrib-neg"}`}
        style={{ width: `${pct.toFixed(1)}%` }} />
    </span>
  );
}

function Unavailable({ formatMismatch = false }: { formatMismatch?: boolean }) {
  return (
    <div className="explanation explanation--empty">
      スコア寄与は未提供です{formatMismatch && "（形式不整合）"}
    </div>
  );
}

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function ExplanationPanel({ explanation }: { explanation: Explanation | null | undefined }) {
  if (!explanation) {
    return <Unavailable />;
  }

  const isV2 = explanation.method_version === 2;
  if (explanation.method_version !== 1 && !isV2) {
    return <Unavailable />;
  }

  const items = explanation.items ?? [];
  if (isV2 && (
    !isFiniteNumber(explanation.score_centered)
    || !isFiniteNumber(explanation.other_contribution_centered)
    || items.some((item) => !isFiniteNumber(item.contribution_centered))
  )) {
    return <Unavailable formatMismatch />;
  }

  // v2 with no candidate feature (e.g. a one-horse race, where every value is shared) has nothing
  // to compare. Render the reason only — no 上位N heading and no "その他" row, both of which would
  // pad the panel with zeros and read as if factors had been found (contract §4).
  if (isV2 && items.length === 0) {
    return (
      <div className="explanation">
        <p className="explanation-empty">このレースでは比較できる差がありません</p>
        <p className="explanation-note">※ {NOTE_SCORE_V2}</p>
        <p className="explanation-note">※ {NOTE_CAUSAL}</p>
      </div>
    );
  }

  const displayItems = items.map((item) => ({
    item,
    contribution: isV2 ? item.contribution_centered! : item.contribution,
  }));
  const max = displayItems.reduce((m, { contribution }) => Math.max(m, Math.abs(contribution)), 0);
  const otherContribution = isV2
    ? explanation.other_contribution_centered!
    : explanation.other_contribution;

  return (
    <div className="explanation">
      <div className="explanation-title">
        {isV2 ? "レース内でのスコア寄与" : "モデルのスコア寄与"}（上位{explanation.k}要因）
      </div>
      <table className="contrib-table">
        <tbody>
          {displayItems.map(({ item: it, contribution }) => {
            const fl = featureLabel(it.feature);
            return (
              <tr key={it.feature}>
                <td className="contrib-feature">
                  {fl.label}
                  {fl.derived && <span className="badge badge-derived">導出特徴</span>}
                </td>
                <td className="contrib-value num">
                  {it.value == null
                    ? PLACEHOLDER
                    : typeof it.value === "number"
                      ? formatNum(it.value, 3)
                      : it.value}
                </td>
                <td className="contrib-bar-cell">
                  <Bar contribution={contribution} max={max} />
                </td>
                <td className="contrib-num num">
                  {contribution >= 0 ? "+" : ""}
                  {formatNum(contribution, 3)}
                </td>
              </tr>
            );
          })}
          <tr className="contrib-other">
            <td className="contrib-feature">その他の特徴（合算）</td>
            <td className="contrib-value num">{PLACEHOLDER}</td>
            <td className="contrib-bar-cell" />
            <td className="contrib-num num">
              {otherContribution >= 0 ? "+" : ""}
              {formatNum(otherContribution, 3)}
            </td>
          </tr>
        </tbody>
      </table>
      <p className="explanation-note">※ {isV2 ? NOTE_SCORE_V2 : NOTE_SCORE_V1}</p>
      <p className="explanation-note">※ {NOTE_CAUSAL}</p>
    </div>
  );
}
