import { useMemo, useState } from "react";

import { useRecommendations } from "../api/queries";
import type { RecommendationRow } from "../api/types";
import { betTypeLabel } from "../lib/betTypes";
import { formatOdds, formatSelection } from "../lib/format";
import { PseudoValue, SourceBadge } from "./PseudoValue";
import { QueryStateView } from "./StateView";

/**
 * Persisted exotic recommendations (READ-ONLY — the API never re-generates them). Each row exposes
 * is_estimated_odds / pseudo_odds / pseudo_roi / double_pseudo so the front CANNOT present
 * pseudo-ROI as realized return. Every non-real figure routes through <PseudoValue>.
 */
function pseudoRoiKind(row: RecommendationRow): "pseudo" | "double_pseudo" {
  return row.double_pseudo ? "double_pseudo" : "pseudo";
}

export function RecommendationPanel({ raceId }: { raceId: string }) {
  const query = useRecommendations(raceId);
  const [betType, setBetType] = useState<string>("all");

  const betTypesPresent = useMemo(() => {
    const set = new Set((query.data?.items ?? []).map((r) => r.bet_type));
    return Array.from(set);
  }, [query.data]);

  const rows = useMemo(() => {
    const items = query.data?.items ?? [];
    return betType === "all" ? items : items.filter((r) => r.bet_type === betType);
  }, [query.data, betType]);

  return (
    <div className="panel">
      <h2>買い目推奨(永続データ・推奨は生成しない)</h2>
      <div className="toolbar">
        <label htmlFor="rec-bet-type">券種</label>
        <select
          id="rec-bet-type"
          value={betType}
          onChange={(e) => setBetType(e.target.value)}
        >
          <option value="all">すべて</option>
          {betTypesPresent.map((b) => (
            <option key={b} value={b}>
              {betTypeLabel(b)}
            </option>
          ))}
        </select>
      </div>

      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={() => rows.length === 0}
        loadingLabel="推奨を読み込み中…"
        emptyMessage="この条件の推奨はありません"
      >
        {() => (
          <table>
            <thead>
              <tr>
                <th>券種</th>
                <th>組み合わせ</th>
                <th className="num">使用オッズ</th>
                <th className="num">疑似オッズ</th>
                <th className="num">疑似ROI</th>
                <th className="num">Kelly比率</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.recommendation_id}>
                  <td>{betTypeLabel(r.bet_type)}</td>
                  <td>{formatSelection(r.selection)}</td>
                  <td className="num">
                    {/* used odds: estimated → pseudo-badged; real → plain with real source badge */}
                    {r.is_estimated_odds ? (
                      <PseudoValue kind="estimated">
                        {formatOdds(r.estimated_market_odds_used)}
                      </PseudoValue>
                    ) : (
                      <>
                        {formatOdds(r.market_odds_used)} <SourceBadge source="real" />
                      </>
                    )}
                  </td>
                  <td className="num">
                    <PseudoValue kind="pseudo">{formatOdds(r.pseudo_odds)}</PseudoValue>
                  </td>
                  <td className="num">
                    <PseudoValue kind={pseudoRoiKind(r)}>
                      {r.pseudo_roi === null || r.pseudo_roi === undefined
                        ? "—"
                        : `${(r.pseudo_roi * 100).toFixed(1)}%`}
                    </PseudoValue>
                  </td>
                  <td className="num">
                    {/* Feature 043: Kelly effective fraction (016). NULL=flat (no Kelly). Estimated
                        odds → double-pseudo (same kind as pseudo-ROI) so it's never read as real. */}
                    {r.stake_fraction === null || r.stake_fraction === undefined ? (
                      "—"
                    ) : (
                      <PseudoValue kind={pseudoRoiKind(r)}>
                        {`${(r.stake_fraction * 100).toFixed(2)}%`}
                      </PseudoValue>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryStateView>
    </div>
  );
}
