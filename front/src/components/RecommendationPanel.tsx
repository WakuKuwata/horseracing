import { useState } from "react";

import { useRecommendations } from "../api/queries";
import type { HorseEntry } from "../api/types";
import { useBudget } from "../lib/budget";
import { BetSlip } from "./BetSlip";
import { BudgetInput } from "./BudgetInput";
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

export function RecommendationPanel({
  raceId,
  entries,
}: {
  raceId: string;
  entries?: HorseEntry[];
}) {
  const query = useRecommendations(raceId);
  const { budget, setBudget } = useBudget();

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
      <h2>買い目推奨(永続データ・推奨は生成しない)</h2>
      {/* Feature 064 (FR-007): always-on neutral disclosure — no profit language, no coloring. */}
      <p className="note" data-testid="no-edge-note">
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
        loadingLabel="推奨を読み込み中…"
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
                  {view === "results" ? "買い目を見る" : "答え合わせを見る"}
                </button>
              </div>
            ) : null}

            {view === "slip" ? (
              <>
                <BudgetInput budget={budget} onBudgetChange={setBudget} />
                <BetSlip
                  items={data.items}
                  budget={budget}
                  entries={entries}
                  winPolicyStatus={data.win_policy_status}
                  showHistoricalNote={hasSettled}
                />
              </>
            ) : (
              <RecommendationResults items={data.items} data={data} />
            )}
          </>
        )}
      </QueryStateView>
    </div>
  );
}
