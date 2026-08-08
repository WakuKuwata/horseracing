import type { ReactNode } from "react";

import type { HorseEntry, RecommendationRow } from "../api/types";
import { computeAmount, formatYen } from "../lib/budget";
import { frameChipClass } from "../lib/frameColors";
import { formatOdds } from "../lib/format";
import { strengthOf } from "../lib/strength";
import { PseudoValue, SourceBadge } from "./PseudoValue";

/**
 * Feature 087: one bet = one card. The yen amount is the primary element; the expert figures
 * (used odds / pseudo odds / pseudo ROI / Kelly fraction) live in a 「根拠を見る」 disclosure and
 * keep their exact 043/075 PseudoValue kinds. Per codex D3, a double-pseudo row's PRIMARY amount
 * (including 「少額のため見送り」) is itself badged double_pseudo and always visible; amounts on
 * real-odds rows stay plain arithmetic.
 */

export function pseudoRoiKind(row: RecommendationRow): "pseudo" | "double_pseudo" {
  return row.double_pseudo ? "double_pseudo" : "pseudo";
}

/** Wrap a double-pseudo row's primary value in its provenance badge; leave real-odds rows plain. */
function PrimaryValue({ row, children }: { row: RecommendationRow; children: ReactNode }) {
  if (row.double_pseudo) {
    return <PseudoValue kind="double_pseudo">{children}</PseudoValue>;
  }
  return <>{children}</>;
}

const REFERENCE_LABEL = "—(参考表示・賭け比率の記録なし)";

function AmountArea({ row, budget }: { row: RecommendationRow; budget: number | null }) {
  // Budget unset → honest fallback: the persisted Kelly fraction, badged as today (FR-005).
  if (budget === null) {
    if (row.stake_fraction === null || row.stake_fraction === undefined) {
      return <span className="betslip-card__amount betslip-card__amount--ref">{REFERENCE_LABEL}</span>;
    }
    return (
      <span className="betslip-card__amount betslip-card__amount--ref">
        Kelly{" "}
        <PseudoValue kind={pseudoRoiKind(row)}>
          {`${(row.stake_fraction * 100).toFixed(2)}%`}
        </PseudoValue>
      </span>
    );
  }

  const vm = computeAmount(row.stake_fraction, budget);
  if (vm.kind === "reference") {
    return <span className="betslip-card__amount betslip-card__amount--ref">{REFERENCE_LABEL}</span>;
  }
  if (vm.kind === "too_small") {
    return (
      <span className="betslip-card__amount betslip-card__amount--skip" data-amount="too_small">
        <PrimaryValue row={row}>少額のため見送り</PrimaryValue>
      </span>
    );
  }
  return (
    <span className="betslip-card__amount" data-amount="yen">
      <PrimaryValue row={row}>{formatYen(vm.yen)}</PrimaryValue>
    </span>
  );
}

/**
 * Selection chips: every horse number in the selection is matched against the race-detail
 * entries; horse_name and frame degrade INDEPENDENTLY (codex H6) — a missing name keeps the
 * frame color, a missing frame keeps the name on a neutral chip, and an unmatched number
 * renders number-only. Never crashes, never hides the row.
 */
function SelectionChips({
  selection,
  entries,
}: {
  selection: number[];
  entries?: HorseEntry[];
}) {
  const chips = selection.map((num, i) => {
    const entry = entries?.find((e) => e.horse_number === num);
    return (
      <span key={num} className="betslip-card__chip-item">
        {i > 0 ? <span className="betslip-card__chip-sep">-</span> : null}
        <span className={frameChipClass(entry?.frame)}>{num}</span>
      </span>
    );
  });
  const names = selection
    .map((num) => entries?.find((e) => e.horse_number === num)?.horse_name)
    .filter((n): n is string => n !== null && n !== undefined && n !== "");
  return (
    <>
      <span className="betslip-card__chips">{chips}</span>
      {names.length > 0 ? (
        <span className="betslip-card__names">{names.join(" / ")}</span>
      ) : null}
    </>
  );
}

function StrengthMeter({ row, fMax }: { row: RecommendationRow; fMax: number }) {
  const s = strengthOf(row.stake_fraction, fMax);
  if (s === null) return null;
  return (
    <span className="strength">
      <span className="strength__label">{s.label}</span>
      <span className="strength__bar" aria-hidden="true">
        <i style={{ width: `${s.barPct}%` }} />
      </span>
    </span>
  );
}

export function BetSlipCard({
  row,
  budget,
  entries,
  fMax,
  betTypeLabelText,
}: {
  row: RecommendationRow;
  budget: number | null;
  entries?: HorseEntry[];
  fMax: number;
  betTypeLabelText: string;
}) {
  return (
    <div className="betslip-card" data-testid={`bet-slip-card-${row.recommendation_id}`}>
      <div className="betslip-card__row">
        <span className="betslip-card__bettype">{betTypeLabelText}</span>
        <SelectionChips selection={row.selection} entries={entries} />
        <StrengthMeter row={row} fMax={fMax} />
        <AmountArea row={row} budget={budget} />
      </div>
      <details className="betslip-card__evidence">
        <summary aria-label={`根拠を見る(${betTypeLabelText} ${row.selection.join("-")})`}>
          根拠を見る
        </summary>
        <div className="betslip-card__evidence-body">
          <span>
            使用オッズ{" "}
            {row.is_estimated_odds ? (
              <PseudoValue kind="estimated">
                {formatOdds(row.estimated_market_odds_used)}
              </PseudoValue>
            ) : (
              <>
                {formatOdds(row.market_odds_used)} <SourceBadge source="real" />
              </>
            )}
          </span>
          <span>
            疑似オッズ <PseudoValue kind="pseudo">{formatOdds(row.pseudo_odds)}</PseudoValue>
          </span>
          <span>
            疑似ROI{" "}
            <PseudoValue kind={pseudoRoiKind(row)}>
              {row.pseudo_roi === null || row.pseudo_roi === undefined
                ? "—"
                : `${(row.pseudo_roi * 100).toFixed(1)}%`}
            </PseudoValue>
          </span>
          <span>
            Kelly比率{" "}
            {row.stake_fraction === null || row.stake_fraction === undefined ? (
              "—"
            ) : (
              <PseudoValue kind={pseudoRoiKind(row)}>
                {`${(row.stake_fraction * 100).toFixed(2)}%`}
              </PseudoValue>
            )}
          </span>
        </div>
      </details>
    </div>
  );
}
