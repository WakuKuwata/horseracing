import type { HorseEntry, RecommendationRow } from "../api/types";
import { computeAmount, formatYen, summarizeAmounts } from "../lib/budget";
import { betTypeLabel } from "../lib/betTypes";
import { raceMaxFraction } from "../lib/strength";
import { BetSlipCard } from "./BetSlipCard";

/**
 * Feature 087: the betting-slip view. Groups cards by bet type in API first-appearance order
 * (sorting is banned), computes the RACE-WIDE fMax once (per-group recomputation would crown
 * every group's max row 厚め — codex H7), and answers honestly when there is nothing to buy:
 * win-policy skip cards and the empty-state card are first-class content, never a blank.
 */

/** Feature 064: honest win_policy_status → neutral message. */
const WIN_POLICY_MESSAGE: Record<string, string> = {
  no_run: "このレースの予測がまだありません。",
  not_generated: "単勝の買い目はまだ生成されていません。",
  no_win_selected: "単勝は見送りです(policy が条件を満たす買い目を選定しませんでした)。",
};

export function BetSlip({
  items,
  budget,
  entries,
  winPolicyStatus,
  showHistoricalNote = false,
}: {
  items: RecommendationRow[];
  budget: number | null;
  entries?: HorseEntry[];
  winPolicyStatus: string;
  showHistoricalNote?: boolean;
}) {
  // Empty response → the policy reason IS the content (FR-030/031, codex H5).
  if (items.length === 0) {
    const msg = WIN_POLICY_MESSAGE[winPolicyStatus];
    return (
      <div className="betslip">
        {msg ? (
          <div className="betslip__skip-card" data-testid="win-skip-reason">
            <p className="betslip__skip-title">{msg}</p>
          </div>
        ) : (
          <div className="state state--empty" data-state="empty">
            この条件の推奨はありません
          </div>
        )}
      </div>
    );
  }

  const fMax = raceMaxFraction(items.map((r) => r.stake_fraction));

  // Group by bet_type, preserving API first-appearance order (no sorting anywhere).
  const groups: Array<{ betType: string; rows: RecommendationRow[] }> = [];
  for (const row of items) {
    const g = groups.find((x) => x.betType === row.bet_type);
    if (g) g.rows.push(row);
    else groups.push({ betType: row.bet_type, rows: [row] });
  }

  const hasWin = items.some((r) => r.bet_type === "win");
  const skipMsg = !hasWin ? WIN_POLICY_MESSAGE[winPolicyStatus] : undefined;

  const vms = budget !== null ? items.map((r) => computeAmount(r.stake_fraction, budget)) : null;
  const summary = vms !== null && budget !== null ? summarizeAmounts(vms, budget) : null;
  const allSkipped =
    summary !== null && summary.count === 0 && items.length > 0;

  return (
    <div className="betslip">
      {showHistoricalNote ? (
        <p className="note" data-testid="historical-note">
          金額は現在の予算による換算であり、購入履歴ではありません。
        </p>
      ) : null}

      {summary !== null && budget !== null ? (
        <div className="betslip__summary" data-testid="slip-summary">
          <span>購入合計</span>
          <span className="betslip__summary-total">{formatYen(summary.totalYen)}</span>
          <span className="betslip__summary-note">
            {summary.count}点・予算の{summary.budgetPct}%
          </span>
          {allSkipped ? (
            <span className="betslip__summary-note" data-testid="all-too-small">
              全買い目が少額のため見送りです
            </span>
          ) : null}
          {summary.overBudget ? (
            <span className="betslip__over-budget" data-testid="over-budget-note">
              合計が予算を超えています(比率の記録を尊重し縮小はしません)
            </span>
          ) : null}
        </div>
      ) : null}

      {skipMsg ? (
        <div className="betslip__skip-card" data-testid="win-skip-reason">
          <p className="betslip__skip-title">{skipMsg}</p>
        </div>
      ) : null}

      {groups.map((g) => (
        <section key={g.betType}>
          <h3 className="betslip__group-title">{betTypeLabel(g.betType)}</h3>
          <div className="betslip__cards">
            {g.rows.map((row) => (
              <BetSlipCard
                key={row.recommendation_id}
                row={row}
                budget={budget}
                entries={entries}
                fMax={fMax}
                betTypeLabelText={betTypeLabel(row.bet_type)}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
