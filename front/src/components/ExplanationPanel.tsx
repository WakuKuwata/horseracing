import type { Explanation } from "../api/types";
import { formatNum, PLACEHOLDER } from "../lib/format";
import { featureLabel } from "./featureLabels";

// Feature 040 US1: per-horse SCORE CONTRIBUTION panel (not a probability breakdown).
// Contributions decompose the RAW model score (before race-relative softmax / calibration /
// normalisation), so the limitation notes below are MANDATORY and always rendered — a contribution
// must never be read as "the reason this horse wins" or as a share of the final probability.

const NOTE_SCORE = "校正・レース内正規化前のスコアへの寄与です（最終確率の内訳ではありません）";
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

export function ExplanationPanel({ explanation }: { explanation: Explanation | null | undefined }) {
  if (!explanation) {
    return <div className="explanation explanation--empty">スコア寄与は未提供です</div>;
  }
  const items = explanation.items ?? [];
  const max = items.reduce((m, it) => Math.max(m, Math.abs(it.contribution)), 0);

  return (
    <div className="explanation">
      <div className="explanation-title">モデルのスコア寄与（上位{explanation.k}要因）</div>
      <table className="contrib-table">
        <tbody>
          {items.map((it) => {
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
                  <Bar contribution={it.contribution} max={max} />
                </td>
                <td className="contrib-num num">
                  {it.contribution >= 0 ? "+" : ""}
                  {formatNum(it.contribution, 3)}
                </td>
              </tr>
            );
          })}
          <tr className="contrib-other">
            <td className="contrib-feature">その他の特徴（合算）</td>
            <td className="contrib-value num">{PLACEHOLDER}</td>
            <td className="contrib-bar-cell" />
            <td className="contrib-num num">
              {explanation.other_contribution >= 0 ? "+" : ""}
              {formatNum(explanation.other_contribution, 3)}
            </td>
          </tr>
        </tbody>
      </table>
      <p className="explanation-note">※ {NOTE_SCORE}</p>
      <p className="explanation-note">※ {NOTE_CAUSAL}</p>
    </div>
  );
}
