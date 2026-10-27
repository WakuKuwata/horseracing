import { useState } from "react";
import type { ReactNode } from "react";

import { usePurchaseRecording } from "../api/purchaseRecording";

import { useRecommendations } from "../api/queries";
import type { HorseEntry, RecommendationResponse } from "../api/types";
import { computeAmount, useBudget } from "../lib/budget";
import { BetSlip } from "./BetSlip";
import { BudgetInput } from "./BudgetInput";
import { FreeformPurchaseForm, type FreeformPayload } from "./FreeformPurchaseForm";
import { PurchaseActions, type PresentedBet } from "./PurchaseActions";
import { RecommendationResults } from "./RecommendationResults";
import { QueryStateView } from "./StateView";

/**
 * Feature 087: the recommendations panel is now a thin state-switching parent.
 *
 * - view "slip" (買い目): budget input + amount-first bet cards (BetSlip/BetSlipCard).
 * - view "results" (答え合わせ): the settled retrospective (RecommendationResults, FR-022
 *   content unchanged from the pre-087 table).
 * - The race's settled state picks the default view; a toggle (settled races only) switches.
 * - Budget state is owned HERE exactly once (useBudget) and passed down as props (codex H3).
 *
 * Persisted data only — the panel never generates recommendations (read-only boundary).
 */

type View = "slip" | "results";

function PurchaseRecordsSection({ comparisonMode, children }: { comparisonMode: boolean; children: ReactNode }) {
  if (!comparisonMode) return <>{children}</>;
  return (
    <details className="comparison-records">
      <summary>購入記録・訂正</summary>
      <p className="note">
        実際に行った購入や見送りの記録です。比較用の換算額と購入履歴は別に扱います。
      </p>
      {children}
    </details>
  );
}

/** The slip exactly as this render presents it (Feature 106: frozen into the record). */
function presentedBetsOf(
  data: RecommendationResponse | undefined,
  budget: number | null,
): PresentedBet[] {
  if (!data) return [];
  return data.items.map((item) => {
    const amount = budget !== null ? computeAmount(item.stake_fraction, budget) : null;
    return {
      betType: item.bet_type,
      selection: [...item.selection],
      amountYen: amount !== null && amount.kind === "amount" ? amount.yen : null,
      oddsUsed: item.market_odds_used ?? item.estimated_market_odds_used ?? null,
    };
  });
}

export function RecommendationPanel({
  raceId,
  raceDate,
  entries,
  comparisonMode = false,
}: {
  raceId: string;
  raceDate?: string;
  entries?: HorseEntry[];
  comparisonMode?: boolean;
}) {
  const query = useRecommendations(raceId);
  const { budget, setBudget } = useBudget();
  // Feature 106 state shared with the ROI-view record section (B6): same existing-record query,
  // same submit (+ B5 pause flag so the controls say why instead of 503ing).
  const { existingView, submit: submitPurchase, paused } = usePurchaseRecording(raceId, raceDate);

  // View override survives toggling but NOT a race change (codex D7/H8): the effective view is
  // derived every render, so the async response flipping hasSettled updates the default view.
  const [viewState, setViewState] = useState<{ raceId: string; override: View | null }>({
    raceId,
    override: null,
  });
  if (viewState.raceId !== raceId) {
    setViewState({ raceId, override: null });
  }

  const items = query.data?.items ?? [];
  const hasSettled = items.some((r) => r.settled);
  const view: View = viewState.override ?? (hasSettled ? "results" : "slip");

  return (
    <div className="panel">
      <h2>{comparisonMode ? "旧予測モデルの買い目（比較用）" : "買い目推奨(永続データ・推奨は生成しない)"}</h2>
      {comparisonMode && (
        <p className="note" data-testid="recommendation-comparison-note">
          旧方策が選定した買い目とKelly配分の比較表示です。
          金額は比較用の仮予算による換算で、上の期待回収率に基づく買い目ではありません。
        </p>
      )}
      {/* Feature 064 (FR-007): always-on neutral disclosure — no profit language, no coloring. */}
      <p className="note" data-testid="no-edge-note">
        勝ちは約束しません。実測と算出値は区別して表示します。
        このモデルは市場に対する再現可能な優位を持ちません。買い目は損失を抑えるための判断材料であり、
        将来の的中・利益を示すものではありません。過去実績は closing オッズによる事後・in-sample の
        参考値です。
      </p>
      {/* FR-024: recommendations follow the ADOPTED model's run — the model selector above only
          switches the prediction display, never these rows. */}
      <p className="note" data-testid="model-scope-note">
        買い目は生成時に採用されていたモデルの予測に基づきます(モデル切替はこの表示に影響しません)。
      </p>

      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        loadingLabel={comparisonMode ? "比較用の買い目を読み込み中…" : "推奨を読み込み中…"}
      >
        {(data) => (
          <>
            {hasSettled ? (
              <div className="view-toggle">
                <button
                  type="button"
                  onClick={() =>
                    setViewState({ raceId, override: view === "results" ? "slip" : "results" })
                  }
                >
                  {view === "results" ? (comparisonMode ? "選定・配分を見る" : "買い目を見る") : "答え合わせを見る"}
                </button>
              </div>
            ) : null}

            {view === "slip" ? (
              <>
                <BudgetInput budget={budget} onBudgetChange={setBudget}
                  label={comparisonMode ? "比較用の仮予算" : undefined} />
                <BetSlip
                  items={data.items}
                  budget={budget}
                  entries={entries}
                  winPolicyStatus={data.win_policy_status}
                  showHistoricalNote={hasSettled || comparisonMode}
                  comparisonMode={comparisonMode}
                />
              </>
            ) : (
              <RecommendationResults items={data.items} data={data} />
            )}

            {/* Feature 106: record the ACTUAL purchase (append-only via ops). Mounted under
                the slip so the frozen snapshot is exactly what this panel presented. */}
            <PurchaseRecordsSection comparisonMode={comparisonMode}>
            <PurchaseActions
              raceId={raceId}
              paused={paused}
              presentedBets={presentedBetsOf(data, budget)}
              predictionRunId={data.items[0]?.prediction_run_id ?? null}
              winPolicy={data.win_policy_status}
              presentationAvailable
              hasResults={hasSettled}
              submit={submitPurchase}
              existingRecord={
                existingView
                  ? { kind: existingView.kind, recordId: existingView.record_id }
                  : null
              }
            />

            {/* Feature 106 US4: manual entry for a purchase the slip never presented.
                Disabled once an effective record exists (corrections go through the
                editor above; a voided race becomes recordable again). */}
            <FreeformPurchaseForm
              raceId={raceId}
              horseNumbers={(entries ?? [])
                .map((h) => h.horse_number)
                .filter((n): n is number => typeof n === "number")}
              submit={(payload: FreeformPayload) => submitPurchase(payload)}
              disabled={existingView !== null || paused}
            />
            </PurchaseRecordsSection>
          </>
        )}
      </QueryStateView>
    </div>
  );
}
